"""Webhook route handlers."""

import json

from aiohttp import web
from loguru import logger


def setup_routes(app: web.Application) -> None:
    """Register all webhook routes."""
    app.router.add_post("/webhook/evolution", handle_evolution_webhook)


async def handle_evolution_webhook(request: web.Request) -> web.Response:
    """Handle incoming webhooks from Evolution API.

    Processes MESSAGES_UPSERT events and delegates to the WhatsApp channel's
    _handle_message() method, which enforces allow_from permissions.
    """
    # Parse JSON
    try:
        payload = await request.json()
    except (json.JSONDecodeError, Exception):
        logger.warning("Evolution webhook: invalid JSON")
        return web.json_response({"error": "invalid JSON"}, status=400)

    event = payload.get("event", "")

    # Log non-message events for operational visibility
    if event in ("CONNECTION_UPDATE", "QRCODE_UPDATED"):
        logger.info("Evolution webhook event: {} | {}", event, payload.get("data", {}))
        return web.json_response({"status": "ok"})

    # Only process message events
    if event != "MESSAGES_UPSERT":
        return web.json_response({"status": "ignored"})

    # Validate required fields
    data = payload.get("data")
    if not data or not isinstance(data, dict):
        logger.warning("Evolution webhook: missing 'data' field")
        return web.json_response({"error": "missing data"}, status=400)

    key = data.get("key")
    message = data.get("message")
    if not key or not message:
        logger.warning("Evolution webhook: missing 'key' or 'message' in data")
        return web.json_response({"error": "missing key or message"}, status=400)

    # Skip own messages
    if key.get("fromMe", False):
        return web.json_response({"status": "ignored"})

    # Skip status broadcasts
    remote_jid = key.get("remoteJid", "")
    if remote_jid == "status@broadcast":
        return web.json_response({"status": "ignored"})

    # Extract content (Phase 1: only conversation text)
    content = message.get("conversation", "")

    # Determine message type from payload keys
    message_type = "conversation"
    for mtype in (
        "extendedTextMessage", "imageMessage", "videoMessage",
        "documentMessage", "audioMessage",
    ):
        if mtype in message:
            message_type = mtype
            break

    # Extract sender
    sender_id = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid

    # Get the WhatsApp channel and delegate
    channel = request.app["channels"].get("whatsapp")
    if not channel:
        logger.error("Evolution webhook: no 'whatsapp' channel registered")
        return web.json_response({"error": "channel not available"}, status=500)

    await channel._handle_message(
        sender_id=sender_id,
        chat_id=remote_jid,
        content=content,
        media=[],
        metadata={
            "message_id": key.get("id", ""),
            "push_name": data.get("pushName", ""),
            "instance": payload.get("instance", ""),
            "message_type": message_type,
            "timestamp": data.get("messageTimestamp", 0),
        },
    )

    return web.json_response({"status": "ok"})
