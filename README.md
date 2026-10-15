# logicc-farm — авторег + gateway для app.logicc.com

Полный комбо-набор для Logicc (Clerk-based AI-платформа): массовая регистрация аккаунтов через чистый HTTP и OpenAI-совместимый gateway с пулом аккаунтов.

## Что внутри

```
autoreg/logicc_reg.py   — авторегистратор + авто-активация (stdlib + curl_cffi)
autoreg/activate.py     — отдельный активатор аккаунтов из банка
gateway/                — logicc2api (MIT, JamesZhaoY) + патч заголовков — OpenAI-compat прокси с пулом
```

## Как работает авторег

Logicc использует Clerk (`clerk.logicc.com`) для аутентификации. Весь флоу реверснут в чистые HTTP-запросы, браузер не нужен:

1. **Turnstile** (sitekey `0x4AAAAAAAWXJGBD7bONzLBd`) решается через 2captcha (`method=turnstile`, ~10с, ~$0.002/шт)
2. `POST /v1/client/sign_ups` — email + password + `legal_accepted=true` + captcha_token → `su_*` sign-up id
3. `POST /v1/client/sign_ups/{su_id}/prepare_verification` (`strategy=email_code`) — триггерит письмо с кодом
4. Код читается с почты по IMAP (t-online работает; доставка до 5-6 минут)
5. `POST /v1/client/sign_ups/{su_id}/attempt_verification` (`strategy=email_code`) → сессия
6. Из cookie-jar забираются `__client` + `session_id` — готовые креды
7. **Авто-активация tenant'а** (см. ниже) — без неё все API отдают 403

Важно: Clerk банит temporary-email домены (`temporary email services are not allowed`), поэтому нужны реальные почтовые ящики (t-online и подобные).

### Запуск

```bash
# env-переменные (опционально):
#   LG_POOL  — файл почты, формат email:password построчно
#   LG_KEYS  — JSON с ключами 2captcha: [{"key": "...", "label": "..."}]
pip install curl_cffi
python autoreg/logicc_reg.py 5      # зарегистрировать+активировать 5 аккаунтов
python autoreg/activate.py          # доактивировать оставшиеся из банка
```

Результат — `autoreg/logicc_bank.jsonl`, каждая строка:

```json
{"email": "...", "password": "***", "client_cookie": "...", "session_id": "sess_...", "jwt": "...", "status": "complete"}
```

## Активация tenant'а (КЛЮЧЕВОЙ ШАГ)

Свежий Clerk-аккаунт имеет JWT c `ent: null` — все API (`/api/models`, `/api/chats`, `/api/billing/*`) возвращают **403**. Единственный доступный эндпоинт — `GET /api/users/me` (`isMerlinTenant:false, workspaceEnabled:false`). Logicc = white-label Merlin (dookapi).

Активация (реверс onboarding-чанка `onboarding-page`):

1. `POST https://app.logicc.com/api/tenants` c Bearer JWT:
   `{"name": "Research Workspace", "industry": "information-technology", "size": "medium", "lastAdAttribution": null}`
   - `industry`: information-technology | legal | finance | healthcare | industry | retail | real-estate | consulting | government | other
   - `size`: single | extra-small | small | medium | large | extra-large | extra-extra-large | gigantic
   - ответ: `"org_..."` — id организации
2. `POST https://clerk.logicc.com/v1/client/sessions/{sid}/touch` c `active_organization_id=org_...` (form-encoded) — активная организация на сессию
3. Перевыпустить JWT (`POST /v1/client/sessions/{sid}/tokens`) — в claims появляются `o: {id, rol: admin}` → 403 исчезают

`logicc_reg.py` активирует автоматически после реги; `activate.py` — для уже лежащих в банке.

### Питфолы

- **Clerk ротирует cookie `__client` при каждом refresh JWT** — новую куку обязательно сохранять обратно, иначе аккаунт теряется. Не минтить JWT вручную мимо скрипта/gateway.
- **Cloudflare error 1010** на clerk.logicc.com для Python-urllib (TLS-фингерпринт). Лечится `curl_cffi` c `impersonate="chrome131"`. Node fetch проходит CF нормально.
- JWT живёт ~60 секунд — gateway перевыпускает сам по cookie+sessionId.

### Проверено E2E (10.2026)

7/7 аккаунтов активированы; gateway: models 0.7s, chat 3/3 (2.6-7.3s).
Модели: `gpt-6-luna`, `gpt-6.1-sol`, `gemini-3.8-flash`, `gemini-2.5-pro`, `claude-4.5-haiku`, `claude-5.5-sonnet`, `claude-5.5-opus`, `mistral-medium-3.5`.


## Gateway

`gateway/` = [logicc2api](https://github.com/JamesZhaoY/logicc2api) (MIT) + патч заголовков (Referer/UA на Clerk token refresh и upstream — без них 403). Поднимает OpenAI-совместимый `/v1/chat/completions` + `/v1/models`, пул до 200 аккаунтов, round-robin, cooldown на 429/401, авто-обновление `__client` cookie.

```bash
cd gateway && npm install && node server.mjs
# или docker compose up -d
```

Импорт: `POST /api/accounts/import` c заголовком `X-Logicc-Admin: 1`, тело
`{"accounts": [{"name": "acc1", "clientCookie": "...", "sessionId": "sess_..."}]}`.
Только активированные аккаунты (`activated: true`)! Неактивированный = 403 на всём.

Защита `/v1/*` — `LOGICC_API_KEY` в `.env`.

## Дисклеймер

Материалы для исследования API-аутентификации Clerk. Используйте на свой страх и риск.

---
[AlStack](https://t.me/AlStack) — больше разборов и тулкитов
