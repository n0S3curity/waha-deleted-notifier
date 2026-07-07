"""Tests for _extract_deleted_message_id — the GOWS revoke ID reconstruction."""
import pytest
from app.handlers.revoke_event import _extract_deleted_message_id


def _make_before(
    before_id: str,
    before_from: str,
    proto_id: str = "",
    proto_remote_jid: str = "",
    proto_from_me: bool = False,
    proto_participant: str = "",
) -> dict:
    """Build a minimal GOWS 'before' payload."""
    proto_key: dict = {}
    if proto_id:
        proto_key["ID"] = proto_id
    if proto_remote_jid:
        proto_key["remoteJID"] = proto_remote_jid
    if proto_from_me:
        proto_key["fromMe"] = proto_from_me
    if proto_participant:
        proto_key["participant"] = proto_participant

    return {
        "id": before_id,
        "from": before_from,
        "_data": {
            "Message": {
                "protocolMessage": {
                    "key": proto_key,
                    "type": 0,
                }
            }
        },
    }


# ---------------------------------------------------------------------------
# GOWS DM case (the Omer scenario from real logs)
# before.from = "54864080023667@lid"   ← chat JID stored in DB
# proto_key.remoteJID = "128866366558324@lid"  ← sender's LID (DIFFERENT)
# proto_key.fromMe = True  (from Omer's perspective)
# Stored in DB as: false_54864080023667@lid_AC13096736D0E2B6135D12BDC83A08C7
# ---------------------------------------------------------------------------
def test_gows_dm_uses_before_from_not_remote_jid():
    before = _make_before(
        before_id="false_54864080023667@lid_ACC5113617B4D5F22C50524B3D08F397",
        before_from="54864080023667@lid",
        proto_id="AC13096736D0E2B6135D12BDC83A08C7",
        proto_remote_jid="128866366558324@lid",   # different from before.from!
        proto_from_me=True,
    )
    primary, fallbacks = _extract_deleted_message_id(before)

    # Primary uses proto_from_me=True → "true_"
    assert primary == "true_54864080023667@lid_AC13096736D0E2B6135D12BDC83A08C7"
    # Fallback flips fromMe → "false_" — this is the one stored in the DB
    assert fallbacks == ["false_54864080023667@lid_AC13096736D0E2B6135D12BDC83A08C7"]


def test_gows_dm_fallback_matches_stored_id():
    """Verify the fallback ID matches what was actually stored for Omer's message."""
    before = _make_before(
        before_id="false_54864080023667@lid_ACC5113617B4D5F22C50524B3D08F397",
        before_from="54864080023667@lid",
        proto_id="AC13096736D0E2B6135D12BDC83A08C7",
        proto_remote_jid="128866366558324@lid",
        proto_from_me=True,
    )
    primary, fallbacks = _extract_deleted_message_id(before)
    stored_id = "false_54864080023667@lid_AC13096736D0E2B6135D12BDC83A08C7"
    assert stored_id in [primary] + fallbacks


# ---------------------------------------------------------------------------
# GOWS group case (bar deleted someone's message)
# before.from == proto_key.remoteJID for groups (both are the group chat ID)
# ---------------------------------------------------------------------------
def test_gows_group_with_participant():
    before = _make_before(
        before_id="false_120363020929796752@g.us_3A7DA493F66B5E67E9C7_23579420836037@lid",
        before_from="120363020929796752@g.us",
        proto_id="3A50B18995908A57705F",
        proto_remote_jid="120363020929796752@g.us",   # same as before.from for groups
        proto_from_me=False,
        proto_participant="4201115549922@lid",
    )
    primary, fallbacks = _extract_deleted_message_id(before)

    assert primary == "false_120363020929796752@g.us_3A50B18995908A57705F_4201115549922@lid"
    assert fallbacks == ["true_120363020929796752@g.us_3A50B18995908A57705F_4201115549922@lid"]


# ---------------------------------------------------------------------------
# WEBJS / no protocolMessage key — must fall back to before.id unchanged
# ---------------------------------------------------------------------------
def test_webjs_no_proto_key_returns_before_id():
    before = {
        "id": "false_972585884133@c.us_3A37A7B2C8D1EF0887FD",
        "from": "972585884133@c.us",
        "_data": {},
    }
    primary, fallbacks = _extract_deleted_message_id(before)
    assert primary == "false_972585884133@c.us_3A37A7B2C8D1EF0887FD"
    assert fallbacks == []


# ---------------------------------------------------------------------------
# WEBJS DM case — before=null so code uses payload.after as 'before'.
# payload.after.id is the revoke protocol message ID (wrong to use).
# Correct short ID lives in _data.protocolMessageKey.id.
# Real example from logs: sender דודו קוטלר, chat 221740571598905@lid
# ---------------------------------------------------------------------------
def test_webjs_dm_uses_protocol_message_key():
    # 'before' here is actually payload.after (the revoke protocol message)
    before = {
        "id": "false_221740571598905@lid_AC910F73E5A8B5294D4A8E4739B0B208",  # wrong (revoke msg)
        "from": "221740571598905@lid",
        "fromMe": False,
        "_data": {
            "protocolMessageKey": {
                "fromMe": False,
                "remote": "221740571598905@lid",
                "id": "3EB0DE8F4944B9F3356031",  # the original deleted message short ID
            }
        },
    }
    primary, fallbacks = _extract_deleted_message_id(before)
    stored_id = "false_221740571598905@lid_3EB0DE8F4944B9F3356031"
    assert stored_id in [primary] + fallbacks


# ---------------------------------------------------------------------------
# NOWEB DM case — real payload from a Raspberry Pi / WAHA Core NOWEB session.
# 'before' is null so the code uses payload.after (the revoke protocol
# message) as 'before'. The original message's short ID lives in
# _data.message.protocolMessage.key.id (lowercase 'message', unlike GOWS'
# capitalised 'Message').
# ---------------------------------------------------------------------------
def test_noweb_dm_uses_lowercase_message_protocol_key():
    before = {
        "id": "false_9453608898815@lid_AC56D49E8482819978F4FC1D35E4968D",  # wrong (revoke msg)
        "from": "9453608898815@lid",
        "_data": {
            "key": {
                "remoteJid": "9453608898815@lid",
                "fromMe": False,
                "id": "AC56D49E8482819978F4FC1D35E4968D",
            },
            "message": {
                "protocolMessage": {
                    "key": {
                        "remoteJid": "13490660061418@lid",  # bot's own lid, not the chat
                        "fromMe": True,
                        "id": "AC400851E62C2108632BD5A5A2F781CA",  # original message's short ID
                    },
                    "type": "REVOKE",
                }
            },
        },
    }
    primary, fallbacks = _extract_deleted_message_id(before)
    stored_id = "false_9453608898815@lid_AC400851E62C2108632BD5A5A2F781CA"
    assert stored_id in [primary] + fallbacks


def test_proto_key_missing_short_id_falls_back_to_before_id():
    before = _make_before(
        before_id="false_972585884133@c.us_3A37A7B2C8D1EF0887FD",
        before_from="972585884133@c.us",
        proto_id="",                    # no short ID
        proto_remote_jid="972585884133@c.us",
    )
    primary, fallbacks = _extract_deleted_message_id(before)
    assert primary == "false_972585884133@c.us_3A37A7B2C8D1EF0887FD"
    assert fallbacks == []
