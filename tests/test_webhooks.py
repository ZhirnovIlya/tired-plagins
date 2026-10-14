import time

import pytest
from conftest import event, ext, send, signed

T = "/v1/partner/tenants"


def test_purchase_creates_tenant_with_plugin_plan(client, fake):
    r = send(client, event("e1", "subscription.purchased"))
    assert r.status_code == 200 and r.json() == {"status": "processed"}
    assert fake.paths() == [("POST", T, {"external_account_id": ext("acc-1"), "plan": "plugin"})]


def test_wrong_signature_is_rejected_without_calls(client, fake):
    raw, headers = signed(event("e1", "subscription.purchased"), secret="not-our-secret")
    assert client.post("/webhooks", content=raw, headers=headers).status_code == 401
    assert fake.paths() == []


def test_body_changed_after_signing_is_rejected(client, fake):
    raw, headers = signed(event("e1", "subscription.purchased"))
    raw = raw.replace(b"acc-1", b"acc-2")
    assert client.post("/webhooks", content=raw, headers=headers).status_code == 401
    assert fake.paths() == []


def test_missing_signature_headers_rejected(client, fake):
    r = client.post("/webhooks", json=event("e1", "subscription.purchased"))
    assert r.status_code == 401
    assert fake.paths() == []


@pytest.mark.parametrize("shift", [-310, +310])
def test_timestamp_outside_window_rejected(client, fake, shift):
    r = send(client, event("e1", "subscription.purchased"), ts=int(time.time()) + shift)
    assert r.status_code == 401
    assert fake.paths() == []


def test_replay_returns_200_without_action(client, fake):
    send(client, event("e1", "subscription.purchased"))
    fake.paths()
    r = send(client, event("e1", "subscription.purchased"))
    assert r.status_code == 200 and r.json() == {"status": "duplicate"}
    assert fake.paths() == []


def _purchased(client, fake):
    send(client, event("e0", "subscription.purchased", at="2026-10-14T09:00:00Z"))
    fake.paths()


@pytest.mark.parametrize(
    ("type_", "plan", "expected"),
    [
        ("subscription.plan_changed", "pro", [("PATCH", f"{T}/t-1", {"plan": "plugin_pro"})]),
        ("subscription.cancelled", None, [("PATCH", f"{T}/t-1", {"status": "frozen"})]),
        ("subscription.refunded", None, [("PATCH", f"{T}/t-1", {"status": "frozen"})]),
    ],
)
def test_event_type_maps_to_api_call(client, fake, type_, plan, expected):
    _purchased(client, fake)
    r = send(client, event("e1", type_, plan=plan))
    assert r.status_code == 200, r.text
    assert fake.paths() == expected


def test_renewal_after_cancel_reactivates(client, fake):
    _purchased(client, fake)
    send(client, event("e1", "subscription.cancelled", at="2026-10-14T09:30:00Z"))
    fake.paths()
    assert send(client, event("e2", "subscription.renewed")).status_code == 200
    assert fake.paths() == [
        ("POST", T, {"external_account_id": ext("acc-1"), "plan": "plugin"}),
        ("PATCH", f"{T}/t-1", {"status": "active"}),
    ]
    assert fake.tenants["t-1"]["status"] == "active"


def test_unmapped_plan_is_rejected_and_not_marked_processed(client, fake):
    _purchased(client, fake)
    r = send(client, event("e1", "subscription.plan_changed", plan="unknown-tariff"))
    assert r.status_code == 422
    assert fake.paths() == []


def test_older_event_does_not_undo_newer(client, fake):
    """Вебхуки приходят не по порядку: отмена (10:05) раньше покупки (10:00)."""
    send(client, event("e2", "subscription.cancelled", at="2026-10-14T10:05:00Z"))
    r = send(client, event("e1", "subscription.purchased", at="2026-10-14T10:00:00Z"))
    assert r.status_code == 200 and r.json() == {"status": "stale"}
    assert fake.tenants["t-1"]["status"] == "frozen"


def test_tirek_failure_gives_502_and_retry_succeeds(client, fake):
    fake.down = True
    assert send(client, event("e1", "subscription.purchased")).status_code == 502
    fake.down = False
    r = send(client, event("e1", "subscription.purchased"))
    assert r.status_code == 200 and r.json() == {"status": "processed"}


def test_bbmarket_adapter_is_used(make_client, fake):
    """Заглушка адаптера BB-маркета; поля уточнить по их спецификации."""
    client = make_client(platform="bbmarket")
    body = {
        "id": "bb-1",
        "event": "subscription.created",
        "company_id": "c-9",
        "tariff": "pro",
        "created_at": "2026-10-14T10:00:00Z",
    }
    assert send(client, body).status_code == 200
    assert fake.paths() == [
        ("POST", T, {"external_account_id": ext("c-9", "bbmarket"), "plan": "plugin_pro"})
    ]


def test_repurchase_with_other_tariff_patches_plan(client, fake):
    """Повторный POST тариф не меняет (контракт TIREK) — меняет PATCH."""
    _purchased(client, fake)
    send(client, event("e1", "subscription.cancelled", at="2026-10-14T09:30:00Z"))
    fake.paths()
    assert send(client, event("e2", "subscription.purchased", plan="pro")).status_code == 200
    assert fake.paths() == [
        ("POST", T, {"external_account_id": ext("acc-1"), "plan": "plugin_pro"}),
        ("PATCH", f"{T}/t-1", {"plan": "plugin_pro", "status": "active"}),
    ]
