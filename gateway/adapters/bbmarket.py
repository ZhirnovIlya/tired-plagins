"""Адаптер BB-маркета: их формат → нейтральная схема шлюза (gateway/adapters/neutral.py).

TODO: уточнить по спецификации BB-маркета — её ещё нет, ниже ЗАГЛУШКА с предполагаемыми
именами. Точно будут: id события, тип, id аккаунта/компании, тариф, время. Уточнить:
  1. имена полей события и их форматы (id — строка или число, время — ISO 8601 или unix);
  2. названия типов событий (TYPES) и что делать с типами, которых нет в списке
     (сейчас — 400; если площадка шлёт и другие события, лучше отвечать 200 и пропускать);
  3. схему подписи вебхука: какие заголовки и что подписывается (сейчас — наша,
     docs/WEBHOOKS.md; если у BB-маркета своя — проверку перенести сюда);
  4. имена полей JWT единого входа (сейчас — как в docs/SSO.md);
  5. тарифную сетку → переменная PLAN_MAP.
"""

from gateway.adapters import neutral
from gateway.adapters.neutral import Event, EventType

# TODO: уточнить по спецификации BB-маркета.
TYPES = {
    "subscription.created": EventType.purchased,
    "subscription.renewed": EventType.renewed,
    "subscription.tariff_changed": EventType.plan_changed,
    "subscription.cancelled": EventType.cancelled,
    "subscription.refunded": EventType.refunded,
}


def to_event(p: dict) -> Event:
    # TODO: уточнить по спецификации BB-маркета (имена полей предположительные).
    return Event(
        event_id=str(p["id"]),
        type=TYPES[p["event"]],
        account_id=str(p["company_id"]),
        plan=p.get("tariff"),
        occurred_at=p["created_at"],
    )


# TODO: уточнить по спецификации BB-маркета имена полей JWT; пока как в нейтральной схеме.
sso_ids = neutral.sso_ids
