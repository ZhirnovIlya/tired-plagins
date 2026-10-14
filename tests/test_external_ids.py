"""Номера площадки в TIREK не уходят: только sha256("<PLATFORM>:<номер>")[:32] в hex."""

import json
import re

import httpx
import pytest
from conftest import PARTNER_KEY, PLATFORM_KEY, event, ext, send, token

T = "/v1/partner/tenants"
PII_LIKE = ["79161234567", "771234567890", "ivan.petrov@example.ru"]  # 11 цифр, 12 цифр, e-mail


@pytest.mark.parametrize("raw", PII_LIKE)
def test_pii_like_ids_reach_tirek_only_as_hash(client, fake, raw):
    # поддельный TIREK, как настоящий, отказывает такому номеру
    direct = httpx.Request(
        "POST",
        f"http://tirek.test{T}",
        headers={"Authorization": f"Bearer {PARTNER_KEY}"},
        json={"external_account_id": raw, "plan": "plugin"},
    )
    assert fake.handle(direct).status_code == 422
    fake.paths()

    assert send(client, event("e1", "subscription.purchased", account=raw)).status_code == 200
    assert client.get("/sso", params={"token": token(account_id=raw, sub=raw)}).status_code == 303
    calls = fake.paths()
    assert [b for _, _, b in calls] == [
        {"external_account_id": ext(raw), "plan": "plugin"},
        {"tenant_id": "t-1", "external_user_id": ext(raw)},
    ]
    assert re.fullmatch(r"[0-9a-f]{32}", ext(raw))
    assert raw not in json.dumps(calls)


def test_same_id_gives_same_hash_so_creation_stays_idempotent(make_client, fake, tmp_path):
    for n in (1, 2):  # два независимых запуска шлюза, у каждого своя база
        client = make_client(db_path=str(tmp_path / f"gw-{n}.sqlite3"))
        r = send(client, event(f"e{n}", "subscription.purchased", account="771234567890"))
        assert r.status_code == 200
    (_, _, first), (_, _, second) = fake.paths()
    assert first == second == {"external_account_id": ext("771234567890"), "plan": "plugin"}
    assert len(fake.tenants) == 1


def test_usage_gives_platform_its_own_account_ids(client, fake):
    send(client, event("e1", "subscription.purchased", account="771234567890"))
    r = client.get(
        "/usage",
        params={"from": "2026-10-01", "to": "2026-10-31"},
        headers={"Authorization": f"Bearer {PLATFORM_KEY}"},
    )
    assert r.status_code == 200
    [tenant] = r.json()["tenants"]
    assert tenant["tenant_id"] == "t-1"
    assert tenant["external_account_id"] == "771234567890"
