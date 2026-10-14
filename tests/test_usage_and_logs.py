import logging
import time

from conftest import (
    JWT_SECRET,
    PARTNER_KEY,
    PLATFORM_KEY,
    WEBHOOK_SECRET,
    event,
    send,
    signed,
    token,
)

PERIOD = {"from": "2026-09-01", "to": "2026-09-30"}


def test_usage_proxies_period(client, fake):
    r = client.get("/usage", params=PERIOD, headers={"Authorization": f"Bearer {PLATFORM_KEY}"})
    assert r.status_code == 200
    assert r.json() == {"from": "2026-09-01", "to": "2026-09-30", "items": []}
    assert fake.paths() == [("GET", "/v1/partner/usage", PERIOD)]


def test_usage_requires_platform_key(client, fake):
    assert client.get("/usage", params=PERIOD).status_code == 401
    bad = {"Authorization": f"Bearer {PARTNER_KEY}"}  # ключ TIREK — не ключ площадки
    assert client.get("/usage", params=PERIOD, headers=bad).status_code == 401
    assert fake.paths() == []


def test_logs_contain_no_secrets_and_no_personal_data(client, fake, caplog):
    caplog.set_level(logging.DEBUG)
    good, expired = token(), token(exp=int(time.time()) - 10)

    send(client, event("e1", "subscription.purchased"))
    raw, headers = signed(event("e2", "subscription.cancelled"), secret="forged")
    client.post("/webhooks", content=raw, headers=headers)
    client.get("/sso", params={"token": good})
    client.get("/sso", params={"token": expired})
    client.get("/usage", params=PERIOD, headers={"Authorization": f"Bearer {PLATFORM_KEY}"})
    client.get("/usage", params=PERIOD, headers={"Authorization": "Bearer guess"})

    # Строки «HTTP Request: … http://testserver/…» пишет тестовый клиент (сторона площадки),
    # не шлюз. В бою их аналог — журнал доступа uvicorn, он выключен (--no-access-log).
    own = [r.getMessage() for r in caplog.records if "http://testserver/" not in r.getMessage()]
    assert any("tirek.test" in m for m in own) and any(r.name == "gateway" for r in caplog.records)
    text = "\n".join(own)
    for secret in (
        WEBHOOK_SECRET,
        JWT_SECRET,
        PARTNER_KEY,
        PLATFORM_KEY,
        good,
        expired,
        "user-7",
        "code-1",
        "guess",
        "forged",
    ):
        assert secret not in text, f"в логе найдено: {secret!r}"
