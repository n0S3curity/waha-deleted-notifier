#!/usr/bin/env bash
# setup.sh — Configure WAHA webhook for the Deleted Message Notifier.
#
# Run this once after "docker compose up -d --build".
# Reads config from .env, then creates or updates the listen session in
# WAHA so it points at this bot's webhook endpoint.

set -euo pipefail

# ── Load .env ────────────────────────────────────────────────────────────────
# Deliberately NOT sourcing .env directly: values like a bcrypt ADMIN_PASSWORD_HASH
# contain '$' (e.g. $2b$12$...), which bash would try to expand as positional
# parameters if the file were sourced. Instead, read only the specific keys we
# need as literal text.
_env_get() {
    [ -f .env ] || return 0
    grep -E "^$1=" .env | tail -n1 | cut -d '=' -f2-
}

# ── Required variables ───────────────────────────────────────────────────────
WAHA_BASE_URL="${WAHA_BASE_URL:-$(_env_get WAHA_BASE_URL)}"
WAHA_BASE_URL="${WAHA_BASE_URL:-http://localhost:3000}"
WAHA_API_KEY="${WAHA_API_KEY:-$(_env_get WAHA_API_KEY)}"
WAHA_API_KEY="${WAHA_API_KEY:?ERROR: WAHA_API_KEY is not set in .env}"
WAHA_LISTEN_SESSION="${WAHA_LISTEN_SESSION:-$(_env_get WAHA_LISTEN_SESSION)}"
WAHA_LISTEN_SESSION="${WAHA_LISTEN_SESSION:-listener}"
BOT_WEBHOOK_URL="${BOT_WEBHOOK_URL:-$(_env_get BOT_WEBHOOK_URL)}"
BOT_WEBHOOK_URL="${BOT_WEBHOOK_URL:?ERROR: BOT_WEBHOOK_URL is not set in .env (e.g. http://192.168.1.10:8001)}"

FULL_WEBHOOK_URL="${BOT_WEBHOOK_URL%/}/webhook/waha"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   WAHA Deleted Notifier — Setup              ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  WAHA URL      : $WAHA_BASE_URL"
echo "  Listen session: $WAHA_LISTEN_SESSION"
echo "  Bot webhook   : $FULL_WEBHOOK_URL"
echo ""

# ── Wait for WAHA ─────────────────────────────────────────────────────────────
echo "[1/3] Waiting for WAHA API to be ready..."
n=0
while true; do
    n=$((n + 1))
    if [ "$n" -gt 30 ]; then
        echo "ERROR: WAHA not reachable at $WAHA_BASE_URL after 150 s. Check that WAHA is running."
        exit 1
    fi
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "X-Api-Key: $WAHA_API_KEY" \
        "$WAHA_BASE_URL/api/sessions" 2>/dev/null || echo "000")
    if [ "$HTTP" = "200" ]; then
        echo "      WAHA is ready."
        break
    fi
    printf "      Attempt %d: HTTP %s — retrying in 5 s...\r" "$n" "$HTTP"
    sleep 5
done

# ── Webhook config payload ───────────────────────────────────────────────────
WEBHOOK_CONFIG=$(cat <<JSON
{
    "url": "$FULL_WEBHOOK_URL",
    "events": ["message.any", "message.revoked", "chat.archive"],
    "retries": {"attempts": 5, "delaySeconds": 2, "policy": "exponential"}
}
JSON
)

# ── Check if session already exists ─────────────────────────────────────────
echo "[2/3] Checking session '$WAHA_LISTEN_SESSION'..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "X-Api-Key: $WAHA_API_KEY" \
    "$WAHA_BASE_URL/api/sessions/$WAHA_LISTEN_SESSION")

if [ "$STATUS" = "404" ]; then
    # ── Create new session ───────────────────────────────────────────────────
    echo "      Session not found — creating it now..."
    HTTP=$(curl -s -o /tmp/waha_setup_resp.json -w "%{http_code}" \
        -X POST "$WAHA_BASE_URL/api/sessions" \
        -H "X-Api-Key: $WAHA_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"$WAHA_LISTEN_SESSION\",
            \"config\": {\"webhooks\": [$WEBHOOK_CONFIG]},
            \"start\": true
        }")
    if [ "$HTTP" = "200" ] || [ "$HTTP" = "201" ]; then
        echo "      Session '$WAHA_LISTEN_SESSION' created."
        NEEDS_QR=true
    else
        echo "ERROR: Failed to create session (HTTP $HTTP):"
        cat /tmp/waha_setup_resp.json 2>/dev/null || true
        echo ""
        exit 1
    fi
else
    # ── Update existing session's webhook config ──────────────────────────────
    echo "      Session exists (HTTP $STATUS) — updating webhook config..."
    HTTP=$(curl -s -o /tmp/waha_setup_resp.json -w "%{http_code}" \
        -X PUT "$WAHA_BASE_URL/api/sessions/$WAHA_LISTEN_SESSION" \
        -H "X-Api-Key: $WAHA_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"config\": {\"webhooks\": [$WEBHOOK_CONFIG]}}")
    if [ "$HTTP" = "200" ] || [ "$HTTP" = "201" ]; then
        echo "      Webhook updated."
        NEEDS_QR=false
    else
        echo "ERROR: Session update returned HTTP $HTTP:"
        cat /tmp/waha_setup_resp.json 2>/dev/null || true
        echo ""
        exit 1
    fi
fi

# ── Verify webhook was applied ───────────────────────────────────────────────
echo "[3/3] Verifying configuration..."
SESS_JSON=$(curl -s \
    -H "X-Api-Key: $WAHA_API_KEY" \
    "$WAHA_BASE_URL/api/sessions/$WAHA_LISTEN_SESSION")
APPLIED_URL=$(echo "$SESS_JSON" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    hooks = d.get('config', {}).get('webhooks', [])
    print(hooks[0].get('url', '') if hooks else '')
except Exception:
    print('')
" 2>/dev/null || true)

if [ "$APPLIED_URL" = "$FULL_WEBHOOK_URL" ]; then
    echo "      Webhook verified ✓"
else
    echo "      WARNING: Could not verify webhook URL (got: '$APPLIED_URL')."
    echo "      Check the WAHA dashboard to confirm."
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Setup complete!                            ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Session : $WAHA_LISTEN_SESSION"
echo "  Webhook : $FULL_WEBHOOK_URL"
echo "  Events  : message.any, message.revoked, chat.archive"
echo ""

if [ "${NEEDS_QR:-false}" = "true" ]; then
    echo "  ⚠  The session was just created and needs to be authenticated."
    echo "     Open the WAHA dashboard and scan the QR code:"
    echo "     $WAHA_BASE_URL/dashboard"
    echo ""
fi

echo "  The bot is ready to catch deleted messages."
echo ""
