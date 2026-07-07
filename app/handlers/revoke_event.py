"""Handle 'message.revoked' webhook events.

Flow:
1. Parse revoked payload (before/after).
2. Look up stored message in DB.
3. Check archived status – skip notification if chat is archived.
4. Resolve sender name + group name.
5. Format Hebrew notification text.
6. Send media attachment (if available) + text to notify group.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import structlog

from app import database as db
from app.config import settings
from app.services import contact_service as contacts
from app.services import formatter
from app.waha.client import WAHAClient, WAHAError

logger = structlog.get_logger(__name__)


def _parse_revoke_payload(event: dict[str, Any]) -> dict:
    """Extract the 'before' snapshot of the revoked message.

    WAHA sends: { event, payload: { before: WAMessage, after: WAMessage } }
    """
    raw: dict = event.get("payload") or {}
    before: dict = raw.get("before") or {}
    # Fallback for engines that put the data directly in payload
    if not before:
        before = raw.get("after") or raw
    return before


def _is_group(chat_id: str) -> bool:
    return isinstance(chat_id, str) and chat_id.endswith("@g.us")


def _extract_deleted_message_id(before: dict) -> tuple[str, list[str]]:
    """Return (primary_id, fallback_ids) for the deleted message DB lookup.

    GOWS engine sends the revoke *protocol message* as the event payload —
    the actual deleted message's ID is buried in:
        _data.Message.protocolMessage.key.{ID, fromMe, participant}

    NOWEB engine uses the same idea but a differently-cased path:
        _data.message.protocolMessage.key.{id, fromMe, participant}

    Key insight: always use before.from (the outer chat JID) as the chat portion
    of the reconstructed ID — NOT proto_key.remoteJID.  For DMs, remoteJID is the
    *sender's* @lid, which differs from before.from (the chat @lid stored in DB).
    For groups they happen to be the same, so using before.from is safe for both.

    proto_key.fromMe reflects the sender/deleter's perspective, not the bot's,
    so we try both true_ and false_ prefixes.

    WEBJS puts the deleted message directly in 'before', so before.id is already
    the correct ID — proto_key won't have a valid short ID and we fall through.
    """
    _data: dict = before.get("_data") or {}
    # GOWS: _data.Message.protocolMessage.key
    proto_key: dict = (
        _data.get("Message", {})
        .get("protocolMessage", {})
        .get("key", {})
    )
    # NOWEB: _data.message.protocolMessage.key (lowercase 'message')
    if not proto_key:
        proto_key = (
            _data.get("message", {})
            .get("protocolMessage", {})
            .get("key", {})
        )
    # WEBJS: _data.protocolMessageKey (before == payload.after; before is null)
    if not proto_key:
        proto_key = _data.get("protocolMessageKey") or {}

    short_id: str = proto_key.get("ID") or proto_key.get("id") or ""

    if not short_id:
        # WEBJS or unknown format — before.id is already the deleted message id
        return before.get("id") or "", []

    # Use the outer chat JID (before.from) — correct for both groups and DMs.
    chat_jid: str = before.get("from") or before.get("chatId") or ""
    if not chat_jid:
        return before.get("id") or "", []

    from_me: bool = proto_key.get("fromMe", False)
    participant: str = proto_key.get("participant") or ""

    from_me_str = "true" if from_me else "false"
    alt_from_me_str = "false" if from_me else "true"

    if participant:
        primary = f"{from_me_str}_{chat_jid}_{short_id}_{participant}"
        fallback = f"{alt_from_me_str}_{chat_jid}_{short_id}_{participant}"
    else:
        primary = f"{from_me_str}_{chat_jid}_{short_id}"
        fallback = f"{alt_from_me_str}_{chat_jid}_{short_id}"

    return primary, [fallback]


def _normalize_jid(jid: str, info: dict) -> str:
    """Resolve a @lid JID to a phone-based @c.us JID using SenderAlt from GOWS _data.Info.

    GOWS still sends @lid JIDs in the 'from'/'participant' fields but also
    provides the real phone JID in _data.Info.SenderAlt / _data.Info.RecipientAlt.
    Normalising to @c.us lets get_contact() find the address-book contact name.

    @s.whatsapp.net JIDs may carry a device suffix (':N') — we strip it.
    Non-@lid JIDs are returned unchanged.
    """
    if not (jid and jid.endswith("@lid")):
        return jid
    sender_alt: str = info.get("SenderAlt") or ""
    if sender_alt and "@" in sender_alt:
        phone_part = sender_alt.split("@")[0].split(":")[0]  # strip :N device suffix
        if phone_part.isdigit() and 7 <= len(phone_part) <= 15:
            return f"{phone_part}@c.us"
    return jid


def _sender_from_payload(p: dict, chat_id: str, group: bool) -> tuple[str, Optional[str]]:
    """Return (sender_jid, display_name_or_None).

    sender_jid is normalised to @c.us whenever possible so that get_contact()
    can find the address-book contact name rather than just the pushname.

    WEBJS uses 'participant'; GOWS uses 'author' for group message senders.
    GOWS buries the phone JID and push-name inside _data.Info.
    """
    raw_data: dict = p.get("_data") or {}
    info: dict = raw_data.get("Info") or {}

    if group:
        jid = p.get("participant") or p.get("author") or p.get("from") or chat_id
        if jid and jid.endswith("@g.us"):
            jid = chat_id
    else:
        jid = p.get("from") or chat_id

    jid = _normalize_jid(jid, info)

    display = (
        p.get("senderName")
        or p.get("notifyName")
        or p.get("pushname")
        or raw_data.get("notifyName")
        or raw_data.get("pushname")
        or info.get("PushName")     # GOWS: push-name lives here
        or None
    )
    return jid, display


async def handle_revoke(event: dict[str, Any], client: WAHAClient) -> None:
    """Process a message.revoked webhook event."""
    session: str = event.get("session", settings.waha_listen_session)
    before = _parse_revoke_payload(event)

    message_id: str
    fallback_ids: list[str]
    message_id, fallback_ids = _extract_deleted_message_id(before)
    chat_id: str = before.get("from") or before.get("chatId") or ""

    if not message_id or not chat_id:
        logger.warning("revoke event missing id/chat_id", event_keys=list(before.keys()))
        return

    # Skip WhatsApp status updates (status@broadcast) and broadcast lists
    if chat_id == "status@broadcast" or chat_id.endswith("@broadcast"):
        logger.info("revoke skipped – status/broadcast", chat_id=chat_id)
        return

    is_grp = _is_group(chat_id)
    sender_jid, display_name = _sender_from_payload(before, chat_id, is_grp)

    logger.info(
        "revoke received",
        message_id=message_id,
        chat_id=chat_id,
        is_group=is_grp,
        sender=sender_jid,
        display_name=display_name,
    )
    logger.info("revoke before payload (full)", payload=before)

    # ------------------------------------------------------------------
    # Archived check — notify but annotate (not suppress)
    # ------------------------------------------------------------------
    is_archived = await db.is_chat_archived(chat_id)
    if not is_archived:
        # Also query WAHA live — catches chats archived before the bot started
        try:
            overview = await client.get_chat_overview(chat_id, session)
            if overview:
                # WAHA may use isArchived, archived, or nest it under _chat
                chat_obj = overview.get("_chat") or overview
                live_archived = bool(
                    overview.get("isArchived")
                    or overview.get("archived")
                    or chat_obj.get("isArchived")
                    or chat_obj.get("archived")
                )
                if live_archived:
                    is_archived = True
                    await db.set_chat_archived(chat_id, True)
                    logger.info("archived state learned from WAHA overview", chat_id=chat_id)
        except Exception as exc:
            logger.warning("get_chat_overview failed", chat_id=chat_id, error=str(exc))
    if is_archived:
        notify_archived = await db.get_setting("notify_archived", "true")
        if notify_archived.lower() != "true":
            logger.info("archived notification suppressed by setting", chat_id=chat_id)
            return
        logger.info("chat is archived – will annotate notification", chat_id=chat_id)

    # ------------------------------------------------------------------
    # Resolve names
    # ------------------------------------------------------------------
    sender_name = await contacts.resolve_sender_name(
        sender_jid, display_name, client, session
    )
    group_name: Optional[str] = None
    if is_grp:
        group_name = await contacts.resolve_group_name(chat_id, client, session)

    logger.info(
        "names resolved",
        sender_name=sender_name,
        group_name=group_name,
        is_group=is_grp,
    )

    notify_target = settings.notify_group_id
    if not notify_target:
        logger.error("NOTIFY_GROUP_ID not configured – cannot send notification")
        await db.create_incident(
            incident_type="not_sent",
            message_id=message_id,
            chat_id=chat_id,
            sender_jid=sender_jid,
            error_detail="NOTIFY_GROUP_ID not configured",
        )
        return

    # ------------------------------------------------------------------
    # Look up stored message
    # ------------------------------------------------------------------
    stored = await db.get_message(message_id)
    if stored is None and fallback_ids:
        for fid in fallback_ids:
            stored = await db.get_message(fid)
            if stored:
                logger.info("stored message found via fallback id", fallback_id=fid)
                message_id = fid
                break

    if stored is None:
        logger.warning(
            "stored message NOT found — notification will have no content",
            primary_id=message_id,
            fallback_ids=fallback_ids,
            chat_id=chat_id,
            sender_jid=sender_jid,
        )
    else:
        body_val = stored.get("body") or ""
        logger.info(
            "stored message lookup",
            message_id=message_id,
            found=True,
            has_media=stored.get("has_media"),
            has_file=bool(stored.get("file_path")),
            file_path=stored.get("file_path"),
            caption=stored.get("caption"),
            mime_type=stored.get("mime_type"),
            body_preview=body_val[:200] if body_val else None,
            is_archived=is_archived,
        )

    if stored is None:
        # Nothing captured – send text-only fallback
        text = formatter.format_unavailable(sender_name, is_grp, group_name, is_archived)
        logger.info("sending unavailable fallback notification", notify_target=notify_target)
        ok = await _safe_send_text(client, notify_target, text, session)
        if not ok:
            await db.create_incident(
                incident_type="error",
                message_id=message_id,
                chat_id=chat_id,
                sender_jid=sender_jid,
                notification_text=text,
                error_detail="send_text failed (fallback – no stored message)",
            )
        return

    has_media: bool = bool(stored.get("has_media"))
    file_path: Optional[str] = stored.get("file_path")
    mime_type: Optional[str] = stored.get("mime_type") or ""
    filename: Optional[str] = stored.get("filename")
    body: str = stored.get("body") or ""
    caption: Optional[str] = stored.get("caption") or None

    # ------------------------------------------------------------------
    # Compose + send
    # ------------------------------------------------------------------
    ok = True
    if has_media and settings.send_media_attachments and file_path and Path(file_path).exists():
        is_image = bool(mime_type and "image" in mime_type.lower())
        if is_image:
            text = formatter.format_image_deleted(sender_name, caption, is_grp, group_name, is_archived)
            logger.info("sending image notification", notify_target=notify_target, caption=caption, file_path=file_path)
            ok = await _safe_send_image(client, notify_target, file_path, mime_type, filename or "image", text, session)
        else:
            text = formatter.format_file_deleted(sender_name, filename, caption, is_grp, group_name, is_archived)
            logger.info("sending file notification", notify_target=notify_target, filename=filename, mime_type=mime_type, file_path=file_path)
            ok = await _safe_send_file(client, notify_target, file_path, mime_type, filename or "file", text, session)
    elif has_media:
        # Text-only description of the deleted media — default on WAHA Core (free),
        # since sendImage/sendFile require Plus/Pro. Also used as a fallback when
        # attachments are enabled but the file failed to download.
        if "image" in (mime_type or "").lower():
            text = formatter.format_image_deleted(sender_name, caption, is_grp, group_name, is_archived)
        elif mime_type:
            text = formatter.format_file_deleted(sender_name, filename, caption, is_grp, group_name, is_archived)
        else:
            text = formatter.format_unavailable(sender_name, is_grp, group_name, is_archived)
        logger.info("sending text-only media notification", notify_target=notify_target, mime_type=mime_type, caption=caption)
        ok = await _safe_send_text(client, notify_target, text, session)
    elif body:
        text = formatter.format_text_deleted(sender_name, body, is_grp, group_name, is_archived)
        logger.info("sending text notification", notify_target=notify_target, body_preview=body[:200])
        ok = await _safe_send_text(client, notify_target, text, session)
    else:
        text = formatter.format_unavailable(sender_name, is_grp, group_name, is_archived)
        logger.info("sending unavailable notification (no body, no media)", notify_target=notify_target)
        ok = await _safe_send_text(client, notify_target, text, session)

    if not ok:
        await db.create_incident(
            incident_type="error",
            message_id=message_id,
            chat_id=chat_id,
            sender_jid=sender_jid,
            notification_text=text,
            error_detail="WAHA send failed",
        )


# ------------------------------------------------------------------
# Safe wrappers that log errors instead of raising
# ------------------------------------------------------------------

async def _safe_send_text(
    client: WAHAClient, chat_id: str, text: str, session: str
) -> bool:
    try:
        await client.send_text(chat_id, text, session=settings.waha_notify_session)
        logger.info("notification text sent", chat_id=chat_id)
        return True
    except WAHAError as exc:
        logger.error("send_text failed", error=str(exc))
        return False


async def _safe_send_image(
    client: WAHAClient,
    chat_id: str,
    file_path: str,
    mime_type: str,
    filename: str,
    caption: str,
    session: str,
) -> bool:
    try:
        await client.send_image(
            chat_id, file_path, mime_type, filename, caption,
            session=settings.waha_notify_session
        )
        logger.info("notification image sent", chat_id=chat_id)
        return True
    except WAHAError as exc:
        logger.error("send_image failed", error=str(exc))
        return False


async def _safe_send_file(
    client: WAHAClient,
    chat_id: str,
    file_path: str,
    mime_type: str,
    filename: str,
    caption: str,
    session: str,
) -> bool:
    try:
        await client.send_file(
            chat_id, file_path, mime_type, filename, caption,
            session=settings.waha_notify_session
        )
        logger.info("notification file sent", chat_id=chat_id)
        return True
    except WAHAError as exc:
        logger.error("send_file failed", error=str(exc))
        return False
