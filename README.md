# WAHA Deleted Message Notifier

A production-ready Python/FastAPI service that listens to [WAHA](https://waha.devlike.pro/) webhook events and sends Hebrew-language notifications to a WhatsApp group whenever a message is deleted.

> **WAHA version notice**
>
> This bot is designed to run fully on **WAHA Core (free)** with a single WhatsApp
> account and a single WAHA instance:
> - **One session** both is monitored (`WAHA_LISTEN_SESSION`) and sends alerts
>   (`WAHA_NOTIFY_SESSION`) — set them to the same value.
> - Notifications are **text-only** by default (`SEND_MEDIA_ATTACHMENTS=false`) —
>   deleted images/files are described in the alert text, not attached, since
>   `sendImage`/`sendFile` are Plus/Pro-only endpoints.
> - Recommended alert destination: your own **"Message Yourself"** self-chat
>   (your account's own `@c.us` JID), so alerts stay private no matter which
>   group or DM the deleted message came from.
>
> **WAHA Plus/Pro** additionally lets you run two sessions (separate listen/notify
> accounts) in one container and forward the actual deleted image/file by
> setting `SEND_MEDIA_ATTACHMENTS=true`.
>
> | Feature | Core (free) | Plus/Pro |
> |---|---|---|
> | Single session, self-notify | ✓ | ✓ |
> | Multiple sessions in one container | ✗ (run 2 instances) | ✓ |
> | Send images (`POST /api/sendImage`) | ✗ | ✓ (`SEND_MEDIA_ATTACHMENTS=true`) |
> | Send files (`POST /api/sendFile`) | ✗ | ✓ (`SEND_MEDIA_ATTACHMENTS=true`) |
>
> See the [WAHA pricing page](https://waha.devlike.pro/) for details.
>
> **Raspberry Pi / ARM64:** the default `devlikeapro/waha` image has no arm64
> manifest. Use `devlikeapro/waha:noweb-arm` instead (lightweight, no bundled
> Chromium — well suited to a Pi).

---

## What it does

1. **Captures** every message arriving on the listen session, saving metadata and downloading media (photos, files) to local storage.
2. **Detects deletions** (`message.revoked` events) and looks up the original message in the local database.
3. **Notifies** a designated WhatsApp group with:
   - Who deleted the message
   - Where it was deleted (group name or private chat)
   - The original content — text, image, or file
   - Hebrew-formatted notification text
4. **Suppresses or annotates** notifications from archived chats (configurable via admin panel).
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
messages(message_id, chat_id, sender_id, session, body,
         has_media, mime_type, filename, file_path, caption, created_at)

processed_events(event_id, processed_at)   -- idempotency
archived_chats(chat_id, is_archived, updated_at)
```

---

## Prerequisites

- Docker + Docker Compose
- A running WAHA instance (WEBJS, NOWEB, or GOWS engine)
- One WhatsApp account linked as a WAHA session, used for both:
  - **Listening** — its incoming messages are monitored for deletions
  - **Notifying** — it sends the deletion alerts to your chosen destination
  (WAHA Plus/Pro users may instead use two separate accounts/sessions.)

---

## Setup

### Step 1 — Fill in `.env`

```bash
cp .env.example .env
```

Open `.env` and fill in every field. Here is what each one means and where to find it:

| Variable | How to get it |
|---|---|
| `WAHA_BASE_URL` | URL of your WAHA server, e.g. `http://localhost:3000` |
| `WAHA_API_KEY` | Set in your WAHA `docker-compose.yml`/`docker run` as `WAHA_API_KEY`, or visible in the WAHA Dashboard top-right menu |
| `WAHA_LISTEN_SESSION` | The name you give your session (you choose this when creating it — e.g. `default`) |
| `WAHA_NOTIFY_SESSION` | On WAHA Core (free), set this to the **same value** as `WAHA_LISTEN_SESSION` |
| `NOTIFY_GROUP_ID` | Where alerts are sent — see below |
| `BOT_WEBHOOK_URL` | The URL WAHA uses to call this bot — see below |
| `SEND_MEDIA_ATTACHMENTS` | `false` for WAHA Core (free) — text-only. `true` requires Plus/Pro |

**Finding `NOTIFY_GROUP_ID`:**

Recommended: use your own account's JID (the session's own number) so alerts go to your "Message Yourself" self-chat — private, and independent of which group/DM the deletion happened in. Get it via:
```bash
curl -s http://localhost:3000/api/sessions/{WAHA_LISTEN_SESSION} \
  -H "X-Api-Key: YOUR_API_KEY" | python3 -c "import sys,json; print(json.load(sys.stdin)['me']['id'])"
```
That prints something like `972501234567@c.us` — paste it as `NOTIFY_GROUP_ID`.

Alternatively, to send alerts into a specific WhatsApp **group** instead, run:
```bash
curl -s http://localhost:3000/api/{WAHA_LISTEN_SESSION}/groups \
  -H "X-Api-Key: YOUR_API_KEY" | python3 -m json.tool | grep -A2 '"subject"'
```
Look for your group by name. The `"id"` field ending in `@g.us` is the JID.
Note: any group JID here broadcasts deletion alerts to everyone in that group.

**Finding `BOT_WEBHOOK_URL`:**

This is the URL that WAHA uses to call back into this bot. It must be reachable from the WAHA server — `localhost` will not work if WAHA is in Docker.

| Setup | Value |
|---|---|
| WAHA and bot on the same machine (different containers) | `http://<your-server-LAN-IP>:8001` |
| WAHA and bot in the same Docker Compose network | `http://bot:8000` |
| WAHA on a remote server, bot on your machine | `http://<your-public-IP-or-domain>:8001` |

Find your LAN IP: `ip route get 1.1.1.1 | awk '{print $7; exit}'`

---

### Step 2 — Start the bot

```bash
docker compose up -d --build
```

---

### Step 3 — Run the setup script

```bash
bash setup.sh
```

This script:
- Waits for WAHA to be ready
- Creates the listen session if it doesn't exist yet, or updates its webhook config if it does
- Registers the events `message.any`, `message.revoked`, and `chat.archive`
- Verifies the webhook was applied and prints a summary

If the session was just created you will be prompted to open the WAHA Dashboard and scan the QR code to link your phone.

---

### Step 4 — Authenticate WhatsApp sessions (if new)

Open the WAHA Dashboard at `http://localhost:3000/dashboard` and scan the QR code for each session that isn't yet linked. The listen session must be WORKING before the bot can capture messages.

---

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `WAHA_BASE_URL` | `http://localhost:3000` | WAHA server URL |
| `WAHA_API_KEY` | _(required)_ | WAHA API key (`X-Api-Key` header) |
| `WAHA_LISTEN_SESSION` | `listener` | Session that receives webhook events |
| `WAHA_NOTIFY_SESSION` | `default` | Session that sends notifications — same as `WAHA_LISTEN_SESSION` on WAHA Core |
| `NOTIFY_GROUP_ID` | _(required)_ | JID that receives alerts — your own `@c.us` JID (self-chat, recommended) or a group `@g.us` JID |
| `SEND_MEDIA_ATTACHMENTS` | `false` | `true` attaches deleted images/files (requires WAHA Plus/Pro); `false` describes them in text |
| `BOT_WEBHOOK_URL` | _(required)_ | URL WAHA uses to call this bot, e.g. `http://192.168.1.10:8001` |
| `DAYS_TO_SAVE_FILES` | `7` | Days to keep captured media before cleanup |
| `MEDIA_DIR` | `/data/media` | Path for downloaded media (inside Docker) |
| `DB_PATH` | `/data/bot.db` | SQLite database path (inside Docker) |
| `WEBHOOK_DEDUP_ENABLED` | `true` | Ignore duplicate webhook calls |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `ADMIN_PASSWORD_HASH` | _(optional)_ | bcrypt hash to enable the web admin panel |
| `SECRET_KEY` | _(optional)_ | Random hex string for admin session cookies |

---

## Admin Panel

A web admin panel is available at `http://localhost:8001/admin/` showing message stats, an archived-chat notification toggle, and an incident log.

To enable it, set `ADMIN_PASSWORD_HASH` and `SECRET_KEY` in `.env`:

```bash
# Generate password hash
python3 -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('yourpassword'))"

# Generate secret key
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Notification Format (Hebrew)

With the default `SEND_MEDIA_ATTACHMENTS=false`, every scenario below — including deleted images/files — is sent as a single **text** message; the image/file rows describe the attachment in words rather than forwarding the actual file.

| Scenario | Example |
|---|---|
| Text – group | `הודעה נמחקה ע"י Moshe בקבוצה "Family" עם התוכן: שלום` |
| Text – DM | `הודעה נמחקה ע"י Moshe בשיחה פרטית עם התוכן: שלום` |
| Image – no caption | `ההודעה נמחקה ע"י Moshe בקבוצה "Family" עם התמונה הבאה: (מצורף) ללא תוכן נלווה` |
| Image – with caption | `ההודעה נמחקה ע"י Moshe בקבוצה "Family" עם התמונה הבאה: (מצורף) תוכן: caption` |
| File | `הודעה נמחקה ע"י Moshe בקבוצה "Family" עם הקובץ הבא: report.pdf (מצורף)` |
| Not found in DB | `הודעה נמחקה ע"י Moshe בשיחה פרטית (התוכן לא זמין)` |

---

## Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

Live integration tests (require two connected WAHA sessions) are in `wet_tests/` — see [`wet_tests/README.md`](wet_tests/README.md).

---

## WAHA API Endpoints Used

| Purpose | Method | Path |
|---|---|---|
| Send text notification | `POST` | `/api/sendText` |
| Attach deleted photo _(`SEND_MEDIA_ATTACHMENTS=true`, Plus/Pro)_ | `POST` | `/api/sendImage` |
| Attach deleted file _(`SEND_MEDIA_ATTACHMENTS=true`, Plus/Pro)_ | `POST` | `/api/sendFile` |
| Verify contact exists | `GET` | `/api/contacts/check-exists` |
| Fetch contact name | `GET` | `/api/contacts` |
| Fetch group name | `GET` | `/api/{session}/groups/{id}` |
| Check archived status | `GET` | `/api/{session}/chats/overview` |
| Resolve LID → phone | `GET` | `/api/{session}/lids/lid/{lid}` |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| No notifications at all | Confirm `NOTIFY_GROUP_ID` is correct and the notify session is connected in WAHA Dashboard |
| "Content unavailable" in notifications | `message.any` must fire before `message.revoked` — check WAHA webhook events include `message.any` |
| Deleted image/file shows as text, not attached | Expected on WAHA Core with the default `SEND_MEDIA_ATTACHMENTS=false`. To attach real files instead, set `SEND_MEDIA_ATTACHMENTS=true` (requires WAHA Plus/Pro) |
| `WAHA error 401` | Wrong or missing `WAHA_API_KEY` |
| Duplicate notifications | Confirm `WEBHOOK_DEDUP_ENABLED=true` (default) |
| Notifications from archived chats | Subscribe to `chat.archive` in WAHA webhook config (done automatically by `setup.sh`) |
| WAHA can't reach the bot | Set `BOT_WEBHOOK_URL` to the bot's LAN/public IP — not `localhost` |

---

## Notes

1. **`message.any` before `message.revoked`** — WAHA always fires the capture event first. If a race occurs the bot falls back to "content unavailable".
2. **Archived chats** — Tracked via `chat.archive` events. Chats archived before the bot started are not marked until the next archive/unarchive event.
3. **Media URLs** — Downloaded from `message.media.url`, valid only while WAHA is running. Restarting WAHA may invalidate pending downloads.
4. **NOWEB/GOWS engine** — Sends `@lid` JIDs in some fields. The bot resolves these automatically via `_data.Info.SenderAlt` and `/api/{session}/lids/lid/`.

---

## License

MIT
