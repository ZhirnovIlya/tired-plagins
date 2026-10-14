"""Клиент партнёрского API основного продукта TIREK (контракт — README, «Контракт с TIREK»)."""

from datetime import date

import httpx

from gateway.config import Settings

# Тариф, с которым создаётся арендатор, если тариф площадки не сопоставлен в PLAN_MAP.
PLUGIN = "plugin"

# Ответы login-codes, означающие «арендатор не активен» (подписки нет).
# TODO: сверить с реализацией в основном продукте (ветка feat/partner-plans).
NOT_ACTIVE = (403, 404, 409)


class Tirek:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        key = settings.tirek_partner_key.get_secret_value()
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
        """Идемпотентно: вернёт существующего арендатора аккаунта. → {tenant_id, status}"""
        body = {"external_account_id": account_id, "plan": plan}
        return await self._json("POST", "/v1/partner/tenants", json=body)

    async def patch_tenant(self, tenant_id: str, **changes: str) -> dict:
        return await self._json("PATCH", f"/v1/partner/tenants/{tenant_id}", json=changes)

    async def login_code(self, tenant_id: str, user_id: str) -> str | None:
        """Одноразовый код входа в кабинет; None — арендатор не активен."""
        body = {"tenant_id": tenant_id, "external_user_id": user_id}
        r = await self.http.post("/v1/partner/login-codes", json=body)
        if r.status_code in NOT_ACTIVE:
            return None
        r.raise_for_status()
        return r.json()["code"]

    async def usage(self, from_: date, to: date) -> dict:
        params = {"from": from_.isoformat(), "to": to.isoformat()}
        return await self._json("GET", "/v1/partner/usage", params=params)
