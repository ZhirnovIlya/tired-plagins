"""Клиент партнёрского API основного продукта TIREK (docs/PARTNER_API.md в tirek-ai).

Номера площадки (аккаунт, пользователь) в TIREK не передаются: только их хэш (external_id).
Номер площадки может выглядеть как ПДн (11–12 цифр, e-mail) — TIREK такой отклоняет с 422.
"""

import hashlib
from datetime import date

import httpx

from gateway.config import Settings

# Тариф, с которым создаётся арендатор, если тариф площадки не сопоставлен в PLAN_MAP.
PLUGIN = "plugin"

# Коды ошибок login-codes, означающие «подписка не активна» (403 и 404 по контракту).
NOT_ACTIVE = {"tenant/frozen", "tenant/not-found"}


def external_id(platform: str, raw: str) -> str:
    """Детерминированный номер для TIREK: создание арендатора остаётся идемпотентным.

    Строчный hex из 32 символов: TIREK считает такой токен id приложения, а не ПДн
    (src/tirek/api/pii.py, _HEX_ID_MIN_LEN = 32) — короче или в верхнем регистре нельзя.
    """
    return hashlib.sha256(f"{platform}:{raw}".encode()).hexdigest()[:32]


def _error_code(r: httpx.Response) -> str | None:
    try:
        return r.json()["detail"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


class Tirek:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        key = settings.tirek_partner_key.get_secret_value()
        self.platform = settings.platform
        self.http = httpx.AsyncClient(
            base_url=settings.tirek_api_url,
            headers={"Authorization": f"Bearer {key}"},
            transport=transport,
        )

    async def _json(self, method: str, url: str, **kw) -> dict:
        r = await self.http.request(method, url, **kw)
        r.raise_for_status()
        return r.json()

    async def ensure_tenant(self, account_id: str, plan: str) -> dict:
        """Идемпотентно; повтор возвращает арендатора как есть (тариф не меняет).
        → {tenant_id, plan, status}"""
        body = {"external_account_id": external_id(self.platform, account_id), "plan": plan}
        return await self._json("POST", "/v1/partner/tenants", json=body)

    async def patch_tenant(self, tenant_id: str, **changes: str) -> dict:
        return await self._json("PATCH", f"/v1/partner/tenants/{tenant_id}", json=changes)

    async def login_code(self, tenant_id: str, user_id: str) -> str | None:
        """Одноразовый код входа в кабинет; None — арендатор не активен или удалён."""
        body = {"tenant_id": tenant_id, "external_user_id": external_id(self.platform, user_id)}
        r = await self.http.post("/v1/partner/login-codes", json=body)
        if r.status_code in (403, 404) and _error_code(r) in NOT_ACTIVE:
            return None
        r.raise_for_status()
        return r.json()["code"]

    async def usage(self, from_: date, to: date) -> dict:
        params = {"from": from_.isoformat(), "to": to.isoformat()}
        return await self._json("GET", "/v1/partner/usage", params=params)
