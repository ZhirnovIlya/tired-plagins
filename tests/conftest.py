"""Общие фикстуры: поддельный партнёрский API TIREK и клиент шлюза.

Поддельный сервер повторяет контракт из docs/WEBHOOKS.md / README («Контракт с TIREK»)
и записывает каждый вызов, чтобы тест видел, что именно шлюз отправил в TIREK.
"""

import hashlib
import hmac
import json
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


class FakeTirek:
    """Поддельный TIREK: арендаторы в памяти, журнал вызовов."""

    def __init__(self):
        self.tenants: dict[str, dict] = {}  # tenant_id -> {account, plan, status}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.new_status = "active"  # статус, с которым POST создаёт нового арендатора
        self.down = False
        self.codes = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {PARTNER_KEY}":
            return httpx.Response(401, json={"detail": "bad key"})
        if self.down:
            return httpx.Response(503, json={"detail": "down"})
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))

        if request.method == "POST" and path == "/v1/partner/tenants":
            for tid, t in self.tenants.items():
                if t["account"] == body["external_account_id"]:
                    return httpx.Response(200, json={"tenant_id": tid, "status": t["status"]})
            tid = f"t-{len(self.tenants) + 1}"
            self.tenants[tid] = {
                "account": body["external_account_id"],
                "plan": body["plan"],
                "status": self.new_status,
            }
            return httpx.Response(201, json={"tenant_id": tid, "status": self.new_status})

        if request.method == "PATCH" and path.startswith("/v1/partner/tenants/"):
            tid = path.rsplit("/", 1)[1]
            if tid not in self.tenants:
                return httpx.Response(404, json={"detail": "no tenant"})
            self.tenants[tid].update(body)
            return httpx.Response(200, json={"tenant_id": tid, **self.tenants[tid]})

        if request.method == "POST" and path == "/v1/partner/login-codes":
            t = self.tenants.get(body["tenant_id"])
            if t is None or t["status"] != "active":
                return httpx.Response(409, json={"detail": "tenant not active"})
            self.codes += 1
            return httpx.Response(200, json={"code": f"code-{self.codes}", "expires_in": 60})

        if request.method == "GET" and path == "/v1/partner/usage":
            q = dict(request.url.params)
            self.calls[-1] = (request.method, path, q)
            return httpx.Response(200, json={"from": q.get("from"), "to": q.get("to"), "items": []})

        return httpx.Response(404, json={"detail": "not found"})

    def paths(self) -> list[tuple[str, str, dict | None]]:
        calls, self.calls = self.calls, []
        return calls


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
