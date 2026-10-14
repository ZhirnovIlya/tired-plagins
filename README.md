# TIREK Partner Gateway

Шлюз для продажи TIREK как плагина на сторонних площадках (первая — маркетплейс BB-маркет).

Шлюз — отдельный сервис. Он принимает события подписки площадки (вебхуки), единый вход
пользователя площадки и отдаёт площадке учёт использования. Всё, что касается расчёта, данных
клиентов и тарифов, остаётся в основном продукте TIREK (репозиторий tirek-ai) — шлюз обращается
к нему только через партнёрский API.

Техническая схема и вопросы к площадке — документ «TIREK × маркетплейс: техническая схема подключения».

## Схема

```
           вебхуки подписки (HMAC)          партнёрский API (Bearer)
площадка ───────────────────────► шлюз ─────────────────────────► TIREK
 браузер ── /sso?token=JWT ──────►  │   POST /v1/partner/tenants
         ◄─ 303 кабинет/#/login?code │   PATCH /v1/partner/tenants/{id}
площадка ── GET /usage (ключ) ──────►│   POST /v1/partner/login-codes
                                     │   GET  /v1/partner/usage
                                  SQLite: принятые события,
                                  аккаунт площадки → арендатор TIREK
```

| Вход шлюза | Для кого | Описание |
|---|---|---|
| `POST /webhooks` | площадка | события подписки — [docs/WEBHOOKS.md](docs/WEBHOOKS.md) |
| `GET /sso?token=…` | браузер пользователя | единый вход — [docs/SSO.md](docs/SSO.md) |
| `GET /usage?from=ГГГГ-ММ-ДД&to=ГГГГ-ММ-ДД` | площадка, заголовок `Authorization: Bearer <PLATFORM_API_KEY>` | учёт использования за период, ответ TIREK без изменений |
| `GET /healthz` | мониторинг | `{"status": "ok"}` |

Формат площадки → нейтральный формат шлюза: `gateway/adapters/` (`neutral.py` — наш формат,
`bbmarket.py` — заглушка до спецификации BB-маркета, все места для уточнения помечены `TODO`).

## Контракт с TIREK

Источник — `docs/PARTNER_API.md` основного продукта (ветка `feat/partner-plans`). Все запросы —
с заголовком `Authorization: Bearer <TIREK_PARTNER_KEY>`; ошибки — `{"detail": {"code", "message"}}`.

**Номера площадки в TIREK не уходят.** Вместо номера аккаунта и пользователя шлюз передаёт
`sha256("<PLATFORM>:<номер>")`, первые 32 символа hex (строчные). Хэш детерминированный —
создание арендатора остаётся идемпотентным; TIREK отклоняет номера, похожие на ПДн
(11–12 цифр, e-mail), а такой хэш принимает. Поэтому `PLATFORM` после запуска не меняют:
иначе у тех же аккаунтов будут новые хэши и новые арендаторы. Соответствие номер площадки →
арендатор хранится в SQLite шлюза.

| Запрос | Тело / параметры | Ответ |
|---|---|---|
| `POST /v1/partner/tenants` | `{external_account_id: хэш, plan}` | 201 новый / 200 повтор: `{tenant_id, plan, status}`; повтор тариф не меняет |
| `PATCH /v1/partner/tenants/{tenant_id}` | `{plan?, status?: active\|frozen\|cancelled}` | 200 `{tenant_id, plan, status}`; 404 `tenant/not-found` |
| `POST /v1/partner/login-codes` | `{tenant_id, external_user_id: хэш}` | 201 `{code, expires_in}`; 403 `tenant/frozen`, 404 `tenant/not-found` → «Подписка не активна» |
| `GET /v1/partner/usage` | `?from=&to=` | `{from, to, tenants: [{tenant_id, plan, status, external_account_id, projects, runs}]}` — шлюз заменяет хэш в `external_account_id` на номер аккаунта площадки |

Кабинет принимает `#/login?code=…`.

| Событие площадки | Вызов TIREK |
|---|---|
| покупка, продление | `POST tenants` (тариф по `PLAN_MAP`, иначе `plugin`); если арендатор уже был — `PATCH` статуса `active` и тарифа из события, когда они отличаются |
| смена тарифа | `PATCH plan` (по `PLAN_MAP`); арендатор удалён в TIREK (404 `tenant/not-found`) — `POST tenants` заново и снова `PATCH` |
| отмена, возврат | `PATCH status=frozen`; арендатор удалён в TIREK — событие записано, ответ 200 `gone` |

## Переменные окружения

| Переменная | Обязательна | Смысл |
|---|---|---|
| `PLATFORM` | нет (`neutral`) | формат площадки: `neutral` или `bbmarket` |
| `WEBHOOK_SECRET` | да | общий секрет подписи вебхуков |
| `WEBHOOK_TOLERANCE_SECONDS` | нет (300) | окно метки времени вебхука; согласовать с площадкой |
| `SSO_JWT_ALGORITHM` | нет (`HS256`) | `HS256` или `RS256` |
| `SSO_JWT_SECRET` | для HS256 | общий секрет JWT |
| `SSO_JWT_PUBLIC_KEY` | для RS256 | открытый ключ площадки, PEM |
| `SSO_AUDIENCE` | да | наш `aud` в JWT |
| `SSO_LEEWAY_SECONDS` | нет (0) | допуск расхождения часов для `exp`/`iat` |
| `TIREK_API_URL` | да | адрес API основного продукта |
| `TIREK_PARTNER_KEY` | да | партнёрский ключ TIREK |
| `TIREK_CABINET_URL` | да | адрес кабинета TIREK (куда перенаправлять после входа) |
| `PLATFORM_API_KEY` | да | ключ, с которым площадка забирает `/usage` |
| `PLAN_MAP` | нет (`{}`) | JSON: тариф площадки → тариф TIREK, например `{"pro": "plugin_pro"}` |
| `DB_PATH` | нет (`gateway.sqlite3`; в Docker `/data/gateway.sqlite3`) | файл SQLite |

Значения секретов вписывает владелец на сервере; в репозитории их нет. Пустая переменная
считается незаданной.

## Запуск

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m ruff check . && .venv/bin/python -m pytest -q   # проверки

# локально (переменные — в окружении)
.venv/bin/python -m uvicorn --factory gateway.app:create_app --no-access-log

# Docker
cp docker-compose.example.yml docker-compose.yml   # значения — в .env рядом, не в git
docker compose up -d --build
```

Наружу шлюз выставляется через обратный прокси с TLS. Журнал доступа выключен
(`--no-access-log`): в строке запроса `/sso` лежит токен площадки.

## Логи и данные

В логах и в SQLite — только id событий, аккаунтов (компаний) и арендаторов; в TIREK — только хэши номеров. Ключи, секреты,
токены, коды входа и id пользователей не пишутся (тест `tests/test_usage_and_logs.py`).
Персональные данные шлюзу не нужны и не принимаются.

## Тесты

`tests/conftest.py` — поддельный партнёрский API TIREK (по контракту выше), записывает
каждый вызов. Проверяется: подпись вебхука (верная, неверная, изменённое тело, без заголовков,
метка вне окна, повтор), каждый тип события → нужный вызов API, порядок доставки, сбой TIREK →
502 и успешный повтор; единый вход (новый и известный аккаунт, просрочен, чужой `aud`,
неверная подпись, нет полей, нет подписки, RS256 и подмена алгоритма); учёт по ключу; логи
без секретов и ПДн.
