"""Нейтральная схема шлюза — то, что описано в docs/WEBHOOKS.md и docs/SSO.md.

Площадка, которая шлёт события и токены в этом формате, адаптера не требует.
"""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field


class EventType(StrEnum):
    purchased = "subscription.purchased"
    renewed = "subscription.renewed"
    plan_changed = "subscription.plan_changed"
    cancelled = "subscription.cancelled"
    refunded = "subscription.refunded"


class Event(BaseModel):
    event_id: str = Field(min_length=1)
    type: EventType
    account_id: str = Field(min_length=1)
    plan: str | None = None
    occurred_at: AwareDatetime


def to_event(payload: dict) -> Event:
    return Event.model_validate(payload)


def sso_ids(claims: dict) -> tuple[str, str]:
    """(id аккаунта, id пользователя) из проверенных полей JWT."""
    account, user = claims["account_id"], claims["sub"]
    if not (isinstance(account, str) and account and isinstance(user, str) and user):
        raise ValueError("account_id и sub должны быть непустыми строками")
    return account, user
