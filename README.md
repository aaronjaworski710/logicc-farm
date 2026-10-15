# logicc-farm — авторег + gateway для app.logicc.com

Полный комбо-набор для Logicc (Clerk-based AI-платформа): массовая регистрация аккаунтов через чистый HTTP и OpenAI-совместимый gateway с пулом аккаунтов.

## Что внутри

```
autoreg/logicc_reg.py   — авторегистратор + авто-активация (stdlib + curl_cffi)
autoreg/activate.py     — отдельный активатор аккаунтов из банка
autoreg/dashboard.py    — control-plane дашборд (stdlib, порт 8560)
autoreg/index.html      — UI дашборда (dark theme, авто-обновление 5с)
gateway/                — logicc2api (MIT, JamesZhaoY) + патч заголовков — OpenAI-compat прокси с пулом
```

## Дашборд

```bash
python autoreg/dashboard.py     # http://127.0.0.1:8560
```

- **Обзор**: банк (total/complete/activated/dead), статус волны (pid-детект через CIM), gateway UP/модели, почта в пуле, диск, балансы 2captcha
- **Волна**: старт N аккаунтов (detached-процесс, лог в reg_run.log) / стоп (taskkill дерева)
- **Действия**: активировать 1 акк, поштучный импорт в gateway (skip 409-дублей), рестарт gateway
- **Gateway**: список моделей + тест chat completion с замером секунд
- **Банк**: таблица аккаунтов (email маскируется), **Лог**: tail reg_run.log с подсветкой

API для агентов: `GET /api/status`, `GET /api/bank`, `POST /api/wave/start {n}`, `/api/wave/stop`, `/api/activate`, `/api/gateway/import`, `/api/gateway/restart`, `/api/chat/test {model}`.

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

## v1.2 — Multi-mail, MCP, one-click

### Почтовые вариации (mail_providers.py + config.json)
Цепочка провайдеров в `config.json → mail.providers` (первый исчерпан → следующий):
- **catchall** — свой домен с catch-all forwarding (ForwardEmail/Cloudflare) → код читается из Gmail IMAP. Безлимит адресов `lg<rand>@yourdomain.com`. Clerk НЕ блокирует собственные домены. E2E проверено: vortexio-style домен → org активирован → auto-import 201.
- **imap** — любой свой ящик (Gmail app-password / Outlook / свой сервер), plus/dot-алиасы.
- **tonline** — пул `email:pass` файл, IMAP secureimap.t-online.de.
- Temp-mail (voidash и т.п.) — **заблокирован Clerk** ("temporary email services are not allowed"), не использовать.

Секреты — только env (`GMAIL_APP_PW`) или локальный config.json (в репо только config.example.json).

### Авто-активация + мгновенный endpoint
Каждый новый акк в волне: регистрация → free captcha → код → **tenant активация** → **auto-import в gateway (201)** — endpoint `http://127.0.0.1:18789/v1` доступен сразу, без ручных шагов. Отключение: `config.json → gateway.auto_import=false`.

### MCP-сервер (тулзы видит любой агент)
`mcp_server.py` — stdio JSON-RPC (stdlib-only), 8 тулзов: `logicc_status, pc_stats, list_services, bank_list, wave_start, wave_stop, gateway_import, chat`.
Регистрация в Hermes `config.yaml`:
```yaml
mcp_servers:
  logicc:
    command: python
    args: ["<path>/mcp_server.py"]
```
Или Claude Desktop `claude_desktop_config.json` — тот же формат.

### One-click запуск
```
pip install -r requirements.txt   # curl_cffi
python start_all.py               # dashboard :8560 + gateway :18789 + sidecar ping
python start_all.py --stop        # остановить всё
```
Пути настраиваются env: `LG_HOME, LG_GW_DIR, LG_DASH_PORT, LG_POOL, GMAIL_APP_PW`.

### ПК-метрики в дашборде
`/api/status → pc {cpu_pct, ram_used_gb, ram_total_gb}` (ctypes GetSystemTimes/GlobalMemoryStatusEx, Linux fallback /proc) + `services {sidecar, gateway, dashboard, wave}`. В UI — карточки System (CPU/RAM/Disk бары) и Services (живые UP/DOWN точки).
