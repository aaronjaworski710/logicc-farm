# logicc-farm — авторег + gateway для app.logicc.com

Полный комбо-набор для Logicc (Clerk-based AI-платформа): массовая регистрация аккаунтов через чистый HTTP и OpenAI-совместимый gateway с пулом аккаунтов.

## Что внутри

```
autoreg/logicc_reg.py   — авторегистратор (чистый stdlib Python, 0 зависимостей)
gateway/                — logicc2api (MIT, JamesZhaoY) — OpenAI-compat прокси с пулом аккаунтов
```

## Как работает авторег

Logicc использует Clerk (`clerk.logicc.com`) для аутентификации. Весь флоу реверснут в чистые HTTP-запросы, браузер не нужен:

1. **Turnstile** (sitekey `0x4AAAAAAAWXJGBD7bONzLBd`) решается через 2captcha (`method=turnstile`, ~10с, ~$0.002/шт)
2. `POST /v1/client/sign_ups` — email + password + captcha_token → `su_*` sign-up id
3. Код подтверждения читается с почты по IMAP (t-online работает; доставка кода занимает до 5-6 минут — таймаут 540с)
4. `POST /v1/client/sign_ups/{su_id}/attempt_first_factor` (`strategy=email_code`) → сессия
5. Из cookie-jar забираются `__client` + `session_id` + JWT — это готовые креды для gateway

Важно: Clerk банит temporary-email домены (`temporary email services are not allowed`), поэтому нужны реальные почтовые ящики (t-online и подобные).

### Запуск

```bash
# env-переменные (опционально):
#   LG_POOL  — файл почты, формат email:password построчно
#   LG_KEYS  — JSON с ключами 2captcha: [{"key": "...", "label": "..."}]
python autoreg/logicc_reg.py 5      # зарегистрировать 5 аккаунтов
```

Результат — `autoreg/logicc_bank.jsonl`, каждая строка:

```json
{"email": "...", "password": "***", "client_cookie": "...", "session_id": "sess_...", "jwt": "...", "status": "complete"}
```

## Gateway

`gateway/` = [logicc2api](https://github.com/JamesZhaoY/logicc2api) (MIT). Поднимает OpenAI-совместимый `/v1/chat/completions` + `/v1/models`, пул до 200 аккаунтов, round-robin, cooldown на 429/401, авто-обновление `__client` cookie.

```bash
cd gateway && npm install && node server.mjs
# или docker compose up -d
```

Импорт аккаунтов из банка: консоль гейтвея принимает batch-import JSON —
`[{"name": "acc1", "token": "<jwt>"}]` (JWT-only) или `[{"clientCookie": "...", "sessionId": "sess_..."}]`.

Защита `/v1/*` — `LOGICC_API_KEY` в `.env`.

## Дисклеймер

Материалы для исследования API-аутентификации Clerk. Используйте на свой страх и риск.

---
[AlStack](https://t.me/AlStack) — больше разборов и тулкитов
