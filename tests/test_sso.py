import time

import pytest
from conftest import CABINET, event, send, token
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

T = "/v1/partner/tenants"
CODES = "/v1/partner/login-codes"


def test_valid_token_new_account_creates_tenant_and_redirects(client, fake):
    r = client.get("/sso", params={"token": token()})
    assert r.status_code == 303
    assert r.headers["location"] == f"{CABINET}/#/login?code=code-1"
    assert fake.paths() == [
        ("POST", T, {"external_account_id": "acc-1", "plan": "plugin"}),
        ("POST", CODES, {"tenant_id": "t-1", "external_user_id": "user-7"}),
    ]


def test_known_account_goes_straight_to_login_code(client, fake):
    send(client, event("e1", "subscription.purchased"))
    fake.paths()
    r = client.get("/sso", params={"token": token()})
    assert r.status_code == 303
    assert fake.paths() == [("POST", CODES, {"tenant_id": "t-1", "external_user_id": "user-7"})]


now = int(time.time())


@pytest.mark.parametrize(
    "bad",
    [
        token(exp=now - 10),  # просрочен
        token(aud="someone-else"),  # чужая аудитория
        token(secret="wrong-secret-0123456789abcdef0123"),  # неверная подпись
        token(account_id=None),  # нет id аккаунта
        token(sub=None),  # нет id пользователя
        token(iat=None),  # нет iat
        "not-a-jwt",
    ],
)
def test_bad_token_gives_401_page_in_russian(client, fake, bad):
    r = client.get("/sso", params={"token": bad})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("text/html")
    assert "Вернитесь на площадку" in r.text
    assert fake.paths() == []


def test_missing_token_gives_page(client, fake):
    r = client.get("/sso")
    assert r.status_code == 400
    assert "Вернитесь на площадку" in r.text


def test_new_account_without_active_subscription(client, fake):
    fake.new_status = "frozen"
    r = client.get("/sso", params={"token": token()})
    assert r.status_code == 403
    assert "Подписка на TIREK не активна" in r.text
    assert [c[1] for c in fake.paths()] == [T]


def test_cancelled_account_cannot_log_in(client, fake):
    send(client, event("e1", "subscription.purchased", at="2026-10-14T09:00:00Z"))
    send(client, event("e2", "subscription.cancelled"))
    r = client.get("/sso", params={"token": token()})
    assert r.status_code == 403
    assert "Подписка на TIREK не активна" in r.text


def test_tirek_down_gives_502_page(client, fake):
    fake.down = True
    r = client.get("/sso", params={"token": token()})
    assert r.status_code == 502
    assert "временно недоступен" in r.text


def test_rs256_with_platform_public_key(make_client, fake):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    client = make_client(sso_jwt_algorithm="RS256", sso_jwt_secret=None, sso_jwt_public_key=pem)
    assert client.get("/sso", params={"token": token(secret=key, algorithm="RS256")}).status_code == 303
    # HS256-токен при настройке RS256 не принимается (подмена алгоритма)
    assert client.get("/sso", params={"token": token()}).status_code == 401
