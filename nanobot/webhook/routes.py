"""Webhook route handlers."""

import hmac
import json
from collections import OrderedDict

from aiohttp import web
from loguru import logger

from nanobot.bus.events import InboundMessage

# Deduplication buffer: Evolution API may send the same message multiple times
_processed_ids: OrderedDict[str, None] = OrderedDict()
_MAX_DEDUP = 1000


def phone_to_jid(phone: str) -> str:
    """Convert E.164 phone to WhatsApp JID. '+51987654321' → '51987654321@s.whatsapp.net'"""
    number = phone.replace("+", "").replace(" ", "").replace("-", "")
    return f"{number}@s.whatsapp.net"


def format_crm_event(payload: dict) -> str:
    """Format CRM event payload into a prompt for the agent."""
    data = payload["data"]
    cliente = data["cliente"]
    pedido = data["pedido"]
    prendas = data.get("prendas", [])
    template = data.get("template_sugerido")

    nombre = cliente.get("nombre_preferido") or cliente["nombre"]
    prendas_txt = ", ".join(
        f"{p['cantidad']}x {p['servicio']}" for p in prendas
    )
    saldo_txt = f"S/{pedido['saldo']:.2f}" if pedido.get("saldo") else "pagado"

    if template and template.get("contenido_renderizado"):
        return template["contenido_renderizado"]

    return (
        f"[SISTEMA] Genera SOLO el texto del mensaje para el cliente, sin usar herramientas. "
        f"Tu respuesta será enviada automáticamente por WhatsApp.\n\n"
        f"Evento: {payload['event']}\n"
        f"Cliente: {nombre} ({cliente['nombre']})\n"
        f"Pedido {pedido['codigo']}: {prendas_txt}\n"
        f"Saldo pendiente: {saldo_txt}\n"
        f"Entrega: {pedido.get('fecha_entrega', 'no asignada')}\n\n"
        f"Escribe un mensaje natural y amigable avisando que sus prendas están listas "
        f"para recoger. Usa su nombre preferido. Si hay saldo pendiente, menciónalo con tacto. "
        f"NO uses la herramienta message. Solo responde con el texto."
    )


def setup_routes(app: web.Application) -> None:
    """Register all webhook routes."""
    app.router.add_post("/webhook/evolution", handle_evolution_webhook)
    app.router.add_post("/webhook/crm", handle_crm_webhook)


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

    # Normalize event name: v2.2 uses "MESSAGES_UPSERT", v2.3+ uses "messages.upsert"
    event_normalized = event.upper().replace(".", "_")

    # Log non-message events for operational visibility
    if event_normalized in ("CONNECTION_UPDATE", "QRCODE_UPDATED"):
        logger.info("Evolution webhook event: {} | {}", event, payload.get("data", {}))
        return web.json_response({"status": "ok"})

    # Only process message events
    if event_normalized != "MESSAGES_UPSERT":
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

    # Deduplicate: Evolution API often sends the same message multiple times
    msg_id = key.get("id", "")
    if msg_id:
        if msg_id in _processed_ids:
            return web.json_response({"status": "duplicate"})
        _processed_ids[msg_id] = None
        while len(_processed_ids) > _MAX_DEDUP:
            _processed_ids.popitem(last=False)

    # Extract text content from different message types
    content = (
        message.get("conversation")
        or (message.get("extendedTextMessage") or {}).get("text")
        or ""
    )

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
            "message_id": msg_id,
            "push_name": data.get("pushName", ""),
            "instance": payload.get("instance", ""),
            "message_type": message_type,
            "timestamp": data.get("messageTimestamp", 0),
        },
    )

    return web.json_response({"status": "ok"})


async def handle_crm_webhook(request: web.Request) -> web.Response:
    """Handle incoming CRM events from GAR."""
    # Auth check (fail-closed: reject if no secret configured)
    config = request.app.get("config")
    secret = config.webhook_secret if config else ""
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {secret}"
    if not secret or not hmac.compare_digest(auth, expected):
        return web.json_response(
            {"status": "error", "error": "Invalid webhook secret"},
            status=401,
        )

    # Parse JSON
    try:
        payload = await request.json()
    except Exception:
        return web.json_response(
            {"status": "error", "error": "Invalid JSON"},
            status=400,
        )

    # Validate required fields
    data = payload.get("data", {})
    cliente = data.get("cliente", {})
    phone = cliente.get("telefono_whatsapp")
    crm_mensaje_id = data.get("crm_mensaje_id")

    if not phone:
        return web.json_response(
            {"status": "error", "error": "Missing required field: data.cliente.telefono_whatsapp"},
            status=400,
        )
    if not crm_mensaje_id:
        return web.json_response(
            {"status": "error", "error": "Missing required field: data.crm_mensaje_id"},
            status=400,
        )

    # Build InboundMessage
    event_type = payload.get("event", "unknown")
    content = format_crm_event(payload)

    msg = InboundMessage(
        channel="crm_event",
        sender_id="crm_system",
        chat_id=phone_to_jid(phone),
        content=content,
        metadata={
            "event_type": event_type,
            "crm_mensaje_id": crm_mensaje_id,
            "reply_channel": "whatsapp",
            "cliente": cliente,
            "pedido": data.get("pedido", {}),
            "prendas": data.get("prendas", []),
            "template_sugerido": (data.get("template_sugerido") or {}).get(
                "contenido_renderizado"
            ),
        },
    )

    bus = request.app["bus"]
    await bus.publish_inbound(msg)

    logger.info("CRM webhook accepted: event={} crm_id={}", event_type, crm_mensaje_id)

    return web.json_response(
        {"status": "accepted", "crm_mensaje_id": crm_mensaje_id},
        status=202,
    )
