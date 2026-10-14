"""HTTP-шлюз: вебхуки подписки, единый вход, учёт.

Запуск: uvicorn --factory gateway.app:create_app --no-access-log
(журнал доступа выключен: в строке запроса /sso лежит токен площадки).
В логи пишутся только id событий, аккаунтов (компаний) и арендаторов — без секретов и ПДн.
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import date
from typing import Annotated
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from gateway.adapters import bbmarket, neutral
from gateway.adapters.neutral import Event, EventType
from gateway.config import Settings
from gateway.store import Store
from gateway.tirek import PLUGIN, TenantGone, Tirek

log = logging.getLogger("gateway")

ADAPTERS = {"neutral": neutral, "bbmarket": bbmarket}

PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TIREK — вход</title></head>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem">
<h1>Не получилось войти в TIREK</h1><p>{}</p></body></html>"""

AGAIN = "Вернитесь на площадку и откройте TIREK оттуда ещё раз."
MESSAGES = {
    400: f"В ссылке для входа нет данных площадки. {AGAIN}",
    401: f"Ссылка для входа устарела или недействительна. {AGAIN}",
    403: "Подписка на TIREK не активна. Оформите или продлите её на площадке, "
    "затем откройте TIREK ещё раз.",
    502: "TIREK временно недоступен. Попробуйте открыть его через несколько минут.",
}


def page(status: int) -> HTMLResponse:
    return HTMLResponse(PAGE.format(MESSAGES[status]), status)


class UnknownPlan(Exception):
    pass


def signature_ok(settings: Settings, headers, raw: bytes) -> bool:
    """HMAC-SHA256 от «<timestamp>.<тело>», метка времени — в пределах окна."""
    ts = headers.get("x-tirek-timestamp", "")
    sig = headers.get("x-tirek-signature", "")
    if not ts.isdigit() or abs(time.time() - int(ts)) > settings.webhook_tolerance_seconds:
        return False
    secret = settings.webhook_secret.get_secret_value().encode()
    expected = "sha256=" + hmac.new(secret, f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), sig.encode())


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    settings = settings or Settings()
    store = Store(settings.db_path)
    tirek = Tirek(settings, transport)
    adapter = ADAPTERS[settings.platform]
    app = FastAPI(title="TIREK Partner Gateway", docs_url=None, redoc_url=None, openapi_url=None)

    async def apply(ev: Event) -> str:
        tenant_id, last = store.account(ev.account_id)
        if last and ev.occurred_at < last:
            return "stale"  # вебхуки приходят не по порядку: старое не отменяет новое
        if ev.type in (EventType.purchased, EventType.renewed):
            plan = settings.plan_map.get(ev.plan, PLUGIN)
            t = await tirek.ensure_tenant(ev.account_id, plan)
            tenant_id = t["tenant_id"]
            # повторный POST тариф не меняет (контракт TIREK) — тариф из события ставит PATCH
            changes = {"plan": plan} if ev.plan in settings.plan_map and t["plan"] != plan else {}
            if t["status"] != "active":
                changes["status"] = "active"
            if changes:
                await tirek.patch_tenant(tenant_id, **changes)
        else:
            if ev.type is EventType.plan_changed:
                if ev.plan not in settings.plan_map:
                    raise UnknownPlan
                changes = {"plan": settings.plan_map[ev.plan]}
            else:  # отмена, возврат
                changes = {"status": "frozen"}
            if tenant_id is None:  # вебхук покупки потерялся или ещё не пришёл
                tenant_id = (await tirek.ensure_tenant(ev.account_id, PLUGIN))["tenant_id"]
            try:
                await tirek.patch_tenant(tenant_id, **changes)
            except TenantGone:  # арендатор удалён в TIREK
                if "status" in changes:  # заморозить нечего — цель достигнута
                    store.save_account(ev.account_id, tenant_id, ev.occurred_at)
                    return "gone"
                # смена тарифа: создать заново (tenant_id детерминирован) и повторить
                tenant_id = (await tirek.ensure_tenant(ev.account_id, PLUGIN))["tenant_id"]
                await tirek.patch_tenant(tenant_id, **changes)
        store.save_account(ev.account_id, tenant_id, ev.occurred_at)
        return "processed"

    @app.post("/webhooks")
    async def webhook(request: Request):
        raw = await request.body()
        if not signature_ok(settings, request.headers, raw):
            log.warning("вебхук отклонён: подпись или метка времени")
            return JSONResponse({"detail": "invalid signature or timestamp"}, 401)
        try:
            ev = adapter.to_event(json.loads(raw))
        except (ValueError, KeyError, TypeError):
            log.warning("вебхук отклонён: тело не по схеме")
            return JSONResponse({"detail": "invalid payload"}, 400)
        # ponytail: seen() и record() не атомарны — два одинаковых вебхука, пришедших разом,
        # оба дойдут до TIREK. Терпимо: вызовы TIREK идемпотентны по контракту. Если станет мало —
        # занимать событие в БД до вызова TIREK (INSERT со статусом «в работе»), при сбое снимать.
        if store.seen(ev.event_id):
            log.info("событие %s уже обработано — повтор пропущен", ev.event_id)
            return {"status": "duplicate"}
        try:
            result = await apply(ev)
        except UnknownPlan:
            log.warning("событие %s: тариф площадки не сопоставлен в PLAN_MAP", ev.event_id)
            return JSONResponse({"detail": "unknown plan"}, 422)
        except httpx.HTTPError as e:
            log.error("событие %s: API TIREK не ответило (%s)", ev.event_id, type(e).__name__)
            return JSONResponse({"detail": "upstream error"}, 502)
        store.record(ev.event_id, ev.type, ev.account_id, result)
        log.info("событие %s (%s), аккаунт %s: %s", ev.event_id, ev.type, ev.account_id, result)
        return {"status": result}

    @app.get("/sso")
    async def sso(token: str | None = None):
        # ponytail: повтор того же JWT до exp не блокируется (jti не храним) —
        # площадка выдаёт токен с коротким exp; хранить jti, если этого мало.
        if not token:
            return page(400)
        try:
            claims = jwt.decode(
                token,
                settings.jwt_key(),
                algorithms=[settings.sso_jwt_algorithm],
                audience=settings.sso_audience,
                leeway=settings.sso_leeway_seconds,
                options={"require": ["exp", "iat", "aud"]},
            )
            account_id, user_id = adapter.sso_ids(claims)
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as e:
            log.warning("единый вход отклонён: %s", type(e).__name__)
            return page(401)
        try:
            tenant_id, _ = store.account(account_id)
            if tenant_id is None:  # вебхук покупки ещё не пришёл — спросить TIREK
                t = await tirek.ensure_tenant(account_id, PLUGIN)
                tenant_id = t["tenant_id"]
                store.save_account(account_id, tenant_id)
                active = t["status"] == "active"
                code = await tirek.login_code(tenant_id, user_id) if active else None
            else:
                code = await tirek.login_code(tenant_id, user_id)
        except httpx.HTTPError as e:
            log.error(
                "единый вход, аккаунт %s: API TIREK не ответило (%s)", account_id, type(e).__name__
            )
            return page(502)
        if code is None:
            log.info("единый вход, аккаунт %s: подписка не активна", account_id)
            return page(403)
        log.info("единый вход, аккаунт %s → арендатор %s", account_id, tenant_id)
        url = f"{settings.tirek_cabinet_url.rstrip('/')}/#/login?{urlencode({'code': code})}"
        return RedirectResponse(url, 303)

    @app.get("/usage")
    async def usage(
        from_: Annotated[date, Query(alias="from")],
        to: date,
        authorization: Annotated[str, Header()] = "",
    ):
        expected = f"Bearer {settings.platform_api_key.get_secret_value()}"
        if not hmac.compare_digest(authorization.encode(), expected.encode()):
            log.warning("учёт: неверный ключ площадки")
            return JSONResponse({"detail": "invalid key"}, 401)
        try:
            data = await tirek.usage(from_, to)
        except httpx.HTTPError as e:
            log.error("учёт: API TIREK не ответило (%s)", type(e).__name__)
            return JSONResponse({"detail": "upstream error"}, 502)
        # в TIREK лежит хэш номера, площадке — её собственный номер аккаунта
        accounts = store.accounts_by_tenant()
        for t in data.get("tenants", []):
            t["external_account_id"] = accounts.get(t["tenant_id"])
        return data

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app
