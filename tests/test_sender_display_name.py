"""Tests for _sender_from_payload — display name extraction across engines."""
from app.handlers.revoke_event import _sender_from_payload


def test_noweb_extracts_pushname_camelcase():
    """NOWEB puts the push name at _data.pushName (camelCase), not _data.pushname.

    Real payload shape from a WAHA Core NOWEB session — before this fix,
    the lowercase-only lookup missed it and every notification showed
    "Unknown" as the sender.
    """
    before = {
        "from": "9453608898815@lid",
        "_data": {
            "pushName": "חנוך משה💛🖤",
            "key": {"remoteJid": "9453608898815@lid", "fromMe": False},
        },
    }
    jid, display = _sender_from_payload(before, "9453608898815@lid", group=False)
    assert display == "חנוך משה💛🖤"


def test_gows_still_extracts_pushname_from_info():
    before = {
        "from": "54864080023667@lid",
        "_data": {"Info": {"PushName": "Omer"}},
    }
    jid, display = _sender_from_payload(before, "54864080023667@lid", group=False)
    assert display == "Omer"


def test_no_pushname_anywhere_returns_none():
    before = {"from": "555@c.us", "_data": {}}
    jid, display = _sender_from_payload(before, "555@c.us", group=False)
    assert display is None
