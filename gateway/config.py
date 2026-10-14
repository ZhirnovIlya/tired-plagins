"""Настройки шлюза. Всё — из переменных окружения (имя переменной = имя поля в верхнем регистре)."""

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Формат событий и токенов площадки: neutral — наша схема (docs/), bbmarket — адаптер.
    platform: Literal["neutral", "bbmarket"] = "neutral"

    # Вебхуки площадки
    webhook_secret: SecretStr
    # TODO: согласовать с площадкой. 300 с — значение по умолчанию у Stripe, не калибровка.
    webhook_tolerance_seconds: int = 300

    # Единый вход (JWT от площадки)
    sso_jwt_algorithm: Literal["HS256", "RS256"] = "HS256"
    sso_jwt_secret: SecretStr | None = None  # для HS256
    sso_jwt_public_key: str | None = None  # PEM открытого ключа площадки, для RS256
    sso_audience: str
    # TODO: согласовать с площадкой. 0 — значение PyJWT по умолчанию; поднять, если часы расходятся.
    sso_leeway_seconds: int = 0

    # Партнёрский API основного продукта
    tirek_api_url: str
    tirek_partner_key: SecretStr
    tirek_cabinet_url: str

    # Ключ, с которым площадка забирает учёт (GET /usage)
    platform_api_key: SecretStr

    # Тариф площадки → тариф TIREK, JSON: {"pro": "plugin_pro"}.
    # TODO: заполнить по тарифной сетке площадки. Покупка без сопоставления → тариф plugin.
    plan_map: dict[str, str] = {}

    db_path: str = "gateway.sqlite3"

    @model_validator(mode="after")
    def _key_for_algorithm(self):
        if self.sso_jwt_algorithm == "HS256" and not self.sso_jwt_secret:
            raise ValueError("для HS256 нужен SSO_JWT_SECRET")
        if self.sso_jwt_algorithm == "RS256" and not self.sso_jwt_public_key:
            raise ValueError("для RS256 нужен SSO_JWT_PUBLIC_KEY")
        return self

    def jwt_key(self) -> str:
        if self.sso_jwt_algorithm == "HS256":
            return self.sso_jwt_secret.get_secret_value()
        return self.sso_jwt_public_key
