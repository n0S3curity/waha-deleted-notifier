"""Tests for text-only media notifications (WAHA Core / free tier default)."""
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app import database as db
from app.config import settings
from app.handlers.revoke_event import handle_revoke


@pytest_asyncio.fixture(autouse=True)
async def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "media_dir", str(tmp_path / "media"))
    monkeypatch.setattr(settings, "notify_group_id", "123@g.us")
    monkeypatch.setattr(settings, "send_media_attachments", False)
    await db.init_db()
    yield


def _revoke_event(chat_id: str, msg_id: str = "msg1") -> dict:
    return {
        "event": "message.revoked",
        "id": "evt_001",
        "session": "adiami",
        "payload": {
            "before": {
                "id": msg_id,
                "from": chat_id,
                "body": "",
                "hasMedia": True,
            }
        },
    }


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.get_contact = AsyncMock(return_value=None)
    client.lid_to_phone = AsyncMock(return_value=None)
    client.get_group = AsyncMock(return_value=None)
    client.send_text = AsyncMock()
    client.send_image = AsyncMock()
    client.send_file = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_deleted_image_sends_text_not_attachment_when_disabled():
    """With send_media_attachments=False, a deleted image message must fall back
    to a text description instead of attempting sendImage — otherwise the free
    tier (WAHA Core, no sendImage support) would silently drop the notification.
    """
    await db.upsert_message(
        message_id="msg1",
        chat_id="555@c.us",
        sender_id="555@c.us",
        session="adiami",
        body="",
        has_media=True,
        mime_type="image/jpeg",
        filename="photo.jpg",
        file_path="/data/media/msg1.jpeg",  # never actually created on disk
        caption="nice pic",
    )

    client = _mock_client()
    await handle_revoke(_revoke_event("555@c.us"), client)

    client.send_text.assert_called_once()
    client.send_image.assert_not_called()
    client.send_file.assert_not_called()
    sent_text = client.send_text.call_args[0][1]
    assert "nice pic" in sent_text


@pytest.mark.asyncio
async def test_deleted_file_sends_text_not_attachment_when_disabled():
    await db.upsert_message(
        message_id="msg1",
        chat_id="555@c.us",
        sender_id="555@c.us",
        session="adiami",
        body="",
        has_media=True,
        mime_type="application/pdf",
        filename="report.pdf",
        file_path="/data/media/msg1.pdf",
        caption=None,
    )

    client = _mock_client()
    await handle_revoke(_revoke_event("555@c.us"), client)

    client.send_text.assert_called_once()
    client.send_image.assert_not_called()
    client.send_file.assert_not_called()
    sent_text = client.send_text.call_args[0][1]
    assert "report.pdf" in sent_text
