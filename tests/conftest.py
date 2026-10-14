"""Общие фикстуры: поддельный партнёрский API TIREK и клиент шлюза.

Поддельный сервер повторяет контракт TIREK (docs/PARTNER_API.md основного продукта)
и записывает каждый вызов, чтобы тест видел, что именно шлюз отправил в TIREK.
"""

import hashlib
import hmac
import json
import re
import time

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.config import Settings

PARTNER_KEY = "partner-key-test-0123"
WEBHOOK_SECRET = "whsec-test-0123"
JWT_SECRET = "jwt-secret-test-0123-0123456789abcdef"
PLATFORM_KEY = "platform-key-test-0123"
AUDIENCE = "tirek-gateway"
CABINET = "https://app.tirek.test"


def ext(raw: str, platform: str = "neutral") -> str:
    """Номер, который шлюз обязан передать в TIREK вместо номера площадки."""
    return hashlib.sha256(f"{platform}:{raw}".encode()).hexdigest()[:32]


# Как в TIREK (src/tirek/api/partner.py, pii.py): шаблон номера и ПДн-похожие цифры
# (11–12 подряд); строчный hex от 32 символов — id приложения, не ПДн.
EXTERNAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
LOOKS_PII = re.compile(r"(?<!\d)\d{11,12}(?!\d)")
HEX_ID = re.compile(r"[0-9a-f]{32,}")


def _bad_external(value) -> bool:
    if not isinstance(value, str) or not EXTERNAL_ID.fullmatch(value):
        return True
    return not HEX_ID.fullmatch(value) and LOOKS_PII.search(value) is not None


def _error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"detail": {"code": code, "message": code}})


class FakeTirek:
    """Поддельный TIREK по docs/PARTNER_API.md основного продукта: арендаторы в памяти,
    журнал вызовов."""

    def __init__(self):
        self.tenants: dict[str, dict] = {}  # tenant_id -> {account, plan, status}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.new_status = "active"  # статус, с которым POST создаёт нового арендатора
        self.down = False
        self.codes = 0
        self.ids: dict[str, str] = {}  # external_account_id -> tenant_id

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {PARTNER_KEY}":
            return _error(401, "auth/invalid-key")
        if self.down:
            return httpx.Response(503, json={"detail": "down"})
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))

        if request.method == "POST" and path == "/v1/partner/tenants":
            if _bad_external(body.get("external_account_id")):
                return _error(422, "validation")
            # tenant_id детерминирован по номеру, как в TIREK: после удаления — тот же
            tid = self.ids.setdefault(body["external_account_id"], f"t-{len(self.ids) + 1}")
            if tid in self.tenants:  # тариф повтором не меняется
                return httpx.Response(200, json={"tenant_id": tid, **_public(self.tenants[tid])})
            self.tenants[tid] = {
                "account": body["external_account_id"],
                "plan": body["plan"],
                "status": self.new_status,
            }
            return httpx.Response(201, json={"tenant_id": tid, **_public(self.tenants[tid])})

        if request.method == "PATCH" and path.startswith("/v1/partner/tenants/"):
            tid = path.rsplit("/", 1)[1]
            if tid not in self.tenants:
                return _error(404, "tenant/not-found")
            self.tenants[tid].update(body)
            return httpx.Response(200, json={"tenant_id": tid, **_public(self.tenants[tid])})

        if request.method == "POST" and path == "/v1/partner/login-codes":
            if _bad_external(body.get("external_user_id")):
                return _error(422, "validation")
            t = self.tenants.get(body["tenant_id"])
            if t is None:
                return _error(404, "tenant/not-found")
            if t["status"] != "active":
                return _error(403, "tenant/frozen")
            self.codes += 1
            return httpx.Response(201, json={"code": f"code-{self.codes}", "expires_in": 60})

        if request.method == "GET" and path == "/v1/partner/usage":
            q = dict(request.url.params)
            self.calls[-1] = (request.method, path, q)
            tenants = [
                {
                    "tenant_id": tid,
                    **_public(t),
                    "external_account_id": t["account"],
                    "projects": 0,
                    "runs": {},
                }
                for tid, t in self.tenants.items()
            ]
            return httpx.Response(200, json={"from": q["from"], "to": q["to"], "tenants": tenants})

        return httpx.Response(404, json={"detail": "Not Found"})

    def paths(self) -> list[tuple[str, str, dict | None]]:
        calls, self.calls = self.calls, []
        return calls


def _public(t: dict) -> dict:
    return {"plan": t["plan"], "status": t["status"]}


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        platform="neutral",
        webhook_secret=WEBHOOK_SECRET,
        sso_jwt_algorithm="HS256",
        sso_jwt_secret=JWT_SECRET,
        sso_audience=AUDIENCE,
        tirek_api_url="http://tirek.test",
        tirek_partner_key=PARTNER_KEY,
        tirek_cabinet_url=CABINET,
        platform_api_key=PLATFORM_KEY,
        db_path=str(tmp_path / "gateway.sqlite3"),
        plan_map={"pro": "plugin_pro"},
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def fake():
    return FakeTirek()


@pytest.fixture
def make_client(tmp_path, fake):
    def _make(**overrides) -> TestClient:
        app = create_app(make_settings(tmp_path, **overrides), httpx.MockTransport(fake.handle))
        return TestClient(app, follow_redirects=False)

    return _make


@pytest.fixture
def client(make_client):
    return make_client()


def signed(body: dict | bytes, ts: int | None = None, secret: str = WEBHOOK_SECRET):
    """Тело и заголовки вебхука, подписанные так, как описано в docs/WEBHOOKS.md."""
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    ts = int(time.time()) if ts is None else ts
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Tirek-Timestamp": str(ts),
        "X-Tirek-Signature": f"sha256={sig}",
    }
    return raw, headers


def event(event_id, type_, account="acc-1", plan=None, at="2026-10-14T10:00:00Z") -> dict:
    e = {"event_id": event_id, "type": type_, "account_id": account, "occurred_at": at}
    if plan is not None:
        e["plan"] = plan
    return e


def send(client, body, **kw):
    raw, headers = signed(body, **kw)
    return client.post("/webhooks", content=raw, headers=headers)


def token(secret=JWT_SECRET, algorithm="HS256", **claims) -> str:
    now = int(time.time())
    payload = {
        "account_id": "acc-1",
        "sub": "user-7",
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 120,
    }
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, secret, algorithm=algorithm)
