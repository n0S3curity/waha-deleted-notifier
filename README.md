# WAHA Deleted Message Notifier

A production-ready Python/FastAPI service that listens to [WAHA](https://waha.devlike.pro/) webhook events and sends Hebrew-language notifications to a WhatsApp group whenever a message is deleted.

> **Disclaimer — WAHA Plus/Pro recommended**
>
> WAHA Core (free) includes webhooks, media download, and all three engines (WEBJS, NOWEB, GOWS).
> However, **Core is limited to a single WhatsApp session**, and this bot requires two:
>
> | Session | Role |
> |---|---|
> | `WAHA_LISTEN_SESSION` | The account being monitored for deletions |
> | `WAHA_NOTIFY_SESSION` | The account that sends notifications to the group |
>
> Running both in one WAHA container requires **WAHA Plus or Pro**.
> Alternatively, you can run two separate WAHA Core instances (one per session) if you prefer to stay on the free tier.
>
> See the [WAHA pricing page](https://waha.devlike.pro/) for details.

---

## What it does

1. **Captures** every message arriving on the listen session (`WAHA_LISTEN_SESSION`), saving metadata and downloading media (photos, files) to local storage.
2. **Detects deletions** (`message.revoked` events) and looks up the original message in its local database.
3. **Notifies** a designated WhatsApp group (via `WAHA_NOTIFY_SESSION`) with:
   - Who deleted the message
   - Where it was deleted (group name or private chat)
   - The original content (text / image / file)
   - Hebrew-formatted notification text
4. **Suppresses or annotates** notifications from archived chats (configurable).
5. **Cleans up** old media files and DB rows after `DAYS_TO_SAVE_FILES` days.

---

## Architecture

```
app/
├── main.py               FastAPI app + webhook endpoint
├── config.py             Pydantic Settings (env vars)
├── database.py           SQLite schema + async CRUD
├── waha/
│   └── client.py         WAHA API client (httpx)
├── handlers/
│   ├── any_event.py      message.any → capture media
│   └── revoke_event.py   message.revoked → notify
├── services/
│   ├── contact_service.py  Contact/group name lookup
│   ├── formatter.py        Hebrew text formatting
│   └── media_store.py      File download + caching
└── tasks/
    └── cleanup.py          Periodic retention cleanup
```

### SQLite Schema

```sql
-- Captured messages (metadata + media file path)
messages(message_id, chat_id, sender_id, session, body,
         has_media, mime_type, filename, file_path, caption, created_at)

-- Idempotency: prevents duplicate processing
processed_events(event_id, processed_at)

-- Archived chat local state
archived_chats(chat_id, is_archived, updated_at)
```

---

## Prerequisites

- Docker + Docker Compose
- A running **WAHA Plus or Pro** instance (WEBJS or NOWEB engine)
- Two WhatsApp sessions configured in WAHA:
  - **Listener session** (`WAHA_LISTEN_SESSION`) — the account whose messages are monitored for deletions; receives webhook events
  - **Notify session** (`WAHA_NOTIFY_SESSION`) — sends the deletion notifications
- A WhatsApp group for notifications (`NOTIFY_GROUP_ID`)

---

## Quick Start

### 1. Copy and fill in `.env`

```bash
cp .env.example .env
# Edit .env — at minimum set: WAHA_BASE_URL, WAHA_API_KEY, NOTIFY_GROUP_ID
```

### 2. Build and run

```bash
docker compose up -d --build
```

### 3. Configure WAHA webhooks

In your WAHA dashboard, add a webhook for the **listener session**:

- **URL:** `http://<your-bot-host>:8001/webhook/waha`
- **Events:** `message.any`, `message.revoked`, `chat.archive`
- **downloadMedia:** `true` (required for image/file capture)

Via WAHA API:
```bash
curl -X PUT http://localhost:3000/api/listener/webhook \
  -H "X-Api-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://your-bot-host:8001/webhook/waha",
    "events": ["message.any", "message.revoked", "chat.archive"],
    "downloadMedia": true
  }'
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `WAHA_BASE_URL` | `http://localhost:3000` | WAHA server URL |
| `WAHA_API_KEY` | _(required)_ | WAHA API key (sent as `X-Api-Key` header) |
| `WAHA_LISTEN_SESSION` | `listener` | Session name that receives webhook events |
| `WAHA_NOTIFY_SESSION` | `default` | Session name that sends notifications |
| `NOTIFY_GROUP_ID` | _(required)_ | WA group JID, e.g. `120363000000000000@g.us` |
| `DAYS_TO_SAVE_FILES` | `7` | Retention period for captured media files |
| `MEDIA_DIR` | `/data/media` | Local path for downloaded media |
| `DB_PATH` | `/data/bot.db` | SQLite database path |
| `WEBHOOK_DEDUP_ENABLED` | `true` | Ignore duplicate webhook calls |
| `LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `ADMIN_PASSWORD_HASH` | _(optional)_ | bcrypt hash for the web admin panel |
| `SECRET_KEY` | _(optional)_ | Secret for signing admin session cookies |
| `INTERNAL_TOKEN` | _(optional)_ | Token for management-proxy bypass |

> **Finding your group JID:** Send a message in the target group, then call `GET /api/{session}/groups` on your WAHA instance and look for the `id` field ending in `@g.us`.

---

## Admin Panel

The service includes a lightweight web admin panel at `http://localhost:8001/admin/` showing:

- Live message stats (total, with media, last 24 h)
- Archived-chat notification toggle
- Incident log (failed/unsent notifications)

To enable it, generate a bcrypt password hash and set `ADMIN_PASSWORD_HASH` + `SECRET_KEY` in your `.env`:

```bash
python3 -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('yourpassword'))"
```

---

## Notification Format (Hebrew)

| Scenario | Example |
|---|---|
| Text – group | `הודעה נמחקה ע"י Moshe בקבוצה "Family" עם התוכן: שלום` |
| Text – DM | `הודעה נמחקה ע"י Moshe בשיחה פרטית עם התוכן: שלום` |
| Image – no caption | `ההודעה נמחקה ע"י Moshe בקבוצה "Family" עם התמונה הבאה: (מצורף) ללא תוכן נלווה` |
| Image – with caption | `ההודעה נמחקה ע"י Moshe בקבוצה "Family" עם התמונה הבאה: (מצורף) תוכן: caption here` |
| File | `הודעה נמחקה ע"י Moshe בקבוצה "Family" עם הקובץ הבא: report.pdf (מצורף) ללא תוכן נלווה` |
| Not found in DB | `הודעה נמחקה ע"י Moshe בשיחה פרטית (התוכן לא זמין)` |
| Archived chat | Notification is sent with an archived indicator (or suppressed — see admin toggle) |

---

## Running Tests

```bash
# Install dependencies (virtualenv recommended)
pip install -r requirements.txt

# Unit tests (no WAHA connection needed)
pytest tests/ -v
```

Live integration tests (require two connected WAHA sessions) are in `wet_tests/` — see [`wet_tests/README.md`](wet_tests/README.md).

---

## WAHA API Endpoints Used

| Purpose | Method | Path |
|---|---|---|
| Send text notification | `POST` | `/api/sendText` |
| Attach deleted photo | `POST` | `/api/sendImage` |
| Attach deleted file | `POST` | `/api/sendFile` |
| Verify contact exists | `GET` | `/api/contacts/check-exists` |
| Fetch contact name | `GET` | `/api/contacts` |
| Fetch group name | `GET` | `/api/{session}/groups/{id}` |
| Check archived status | `GET` | `/api/{session}/chats/overview` |
| Resolve LID → phone | `GET` | `/api/{session}/lids/lid/{lid}` |

---

## Troubleshooting

| Problem | Cause / Fix |
|---|---|
| No notifications at all | Check `NOTIFY_GROUP_ID` is correct and the notify session is connected |
| "Content unavailable" messages | `message.any` must be enabled in WAHA webhook config and fire before `message.revoked` |
| Media not attached | Set `downloadMedia: true` in WAHA webhook config; check WAHA storage engine |
| `WAHA error 401` | Wrong or missing `WAHA_API_KEY` |
| Duplicate notifications | Ensure `WEBHOOK_DEDUP_ENABLED=true` (default) |
| Notifications from archived chats | Subscribe to `chat.archive` events in WAHA webhook config |
| Bot unreachable from WAHA | Ensure bot is reachable from the WAHA host on port 8001 |

### Curl test payloads

**Simulate a text message capture then deletion:**
```bash
# 1. Capture
curl -s -X POST http://localhost:8001/webhook/waha \
  -H "Content-Type: application/json" \
  -d '{"id":"evt_1","event":"message.any","session":"listener","payload":{"id":"false_972501234567@c.us_AABBCC112233","from":"972501234567@c.us","body":"Hello world","hasMedia":false,"timestamp":1700000000}}'

# 2. Delete
curl -s -X POST http://localhost:8001/webhook/waha \
  -H "Content-Type: application/json" \
  -d '{"id":"evt_2","event":"message.revoked","session":"listener","payload":{"before":{"id":"false_972501234567@c.us_AABBCC112233","from":"972501234567@c.us","body":"Hello world","hasMedia":false,"timestamp":1700000000}}}'
```

Expected: notification sent to `NOTIFY_GROUP_ID`.

---

## Notes & Known Limitations

1. **`message.any` must fire before `message.revoked`** — WAHA always fires the `any` event first, giving the bot time to cache media. If a race occurs the bot falls back to "content unavailable".

2. **Archived chat state** — The bot tracks archived status via `chat.archive` events. Chats archived before the bot started will not be marked as archived until the next archive/unarchive event.

3. **Media download URLs** — Downloaded from the URL in `message.media.url`. Restarting WAHA may invalidate these URLs; the bot then sends a text-only notification.

4. **GOWS engine** — The NOWEB (GOWS) engine sends `@lid` JIDs instead of phone numbers in some fields. The bot resolves these automatically via `_data.Info.SenderAlt` and the `/lids/lid/` endpoint. This endpoint requires WAHA Plus/Pro.

---

## License

MIT
