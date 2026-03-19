# tests/test_crm_webhook.py
"""Tests for CRM webhook integration."""

import asyncio
import uuid

import pytest
from aiohttp import web

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import GatewayConfig


class TestCrossChannelRouting:
    """Test that reply_channel metadata overrides outbound channel."""

    def test_outbound_uses_reply_channel_when_present(self):
        """OutboundMessage should use reply_channel from metadata."""
        msg = InboundMessage(
            channel="crm_event",
            sender_id="crm_system",
            chat_id="51987654321@s.whatsapp.net",
            content="Test CRM event",
            metadata={"reply_channel": "whatsapp"},
        )
        out_channel = msg.metadata.get("reply_channel", msg.channel)
        assert out_channel == "whatsapp"

    def test_outbound_falls_back_to_channel_when_no_reply_channel(self):
        """Without reply_channel, should use original channel."""
        msg = InboundMessage(
            channel="whatsapp",
            sender_id="user123",
            chat_id="51987654321@s.whatsapp.net",
            content="Hello",
        )
        out_channel = msg.metadata.get("reply_channel", msg.channel)
        assert out_channel == "whatsapp"


def _make_crm_payload(event="prenda_terminada", phone="+51987654321", crm_id="test-uuid"):
    return {
        "event": event,
        "timestamp": "2026-03-17T14:30:00.000Z",
        "sucursal_id": "test-sucursal",
        "data": {
            "cliente": {
                "cliente_id": "C-000001",
                "nombre": "María López",
                "nombre_preferido": "Marita",
                "telefono_whatsapp": phone,
                "whatsapp_opt_in": True,
                "ultimo_indice_plantilla": 0,
            },
            "pedido": {
                "codigo": "B001-4",
                "importe": 45.00,
                "pago_efectivo": 20.00,
                "pago_yape": 0.00,
                "pago_credito": 0.00,
                "saldo": 25.00,
                "fecha_entrega": "2026-03-18",
            },
            "prendas": [
                {"prenda_id": "PRE-0001", "servicio": "lavado", "cantidad": 2},
            ],
            "crm_mensaje_id": crm_id,
        },
    }


class TestCRMWebhook:
    """Test the /webhook/crm endpoint."""

    @pytest.fixture
    def bus(self):
        return MessageBus()

    @pytest.fixture
    def app(self, bus):
        from nanobot.webhook.routes import setup_routes
        application = web.Application()
        application["bus"] = bus
        application["channels"] = {}
        application["config"] = GatewayConfig(webhook_secret="test-secret")
        setup_routes(application)
        return application

    @pytest.mark.asyncio
    async def test_valid_crm_event_returns_202(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        crm_id = f"test-{uuid.uuid4()}"
        resp = await client.post(
            "/webhook/crm",
            json=_make_crm_payload(crm_id=crm_id),
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status == 202

    @pytest.mark.asyncio
    async def test_valid_crm_event_publishes_to_bus(self, aiohttp_client, app, bus):
        crm_id = f"test-{uuid.uuid4()}"
        client = await aiohttp_client(app)
        await client.post(
            "/webhook/crm",
            json=_make_crm_payload(crm_id=crm_id),
            headers={"Authorization": "Bearer test-secret"},
        )
        msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert msg.channel == "crm_event"
        assert msg.sender_id == "crm_system"
        assert msg.chat_id == "51987654321@s.whatsapp.net"
        assert "prenda_terminada" in msg.content
        assert "Marita" in msg.content
        assert msg.metadata["event_type"] == "prenda_terminada"
        assert msg.metadata["crm_mensaje_id"] == crm_id
        assert msg.metadata["reply_channel"] == "whatsapp"

    @pytest.mark.asyncio
    async def test_duplicate_crm_event_returns_200(self, aiohttp_client, app):
        """Duplicate crm_mensaje_id should be ignored with 200."""
        crm_id = f"test-dedup-{uuid.uuid4()}"
        client = await aiohttp_client(app)
        # First request: accepted
        resp1 = await client.post(
            "/webhook/crm",
            json=_make_crm_payload(crm_id=crm_id),
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp1.status == 202
        # Second request: duplicate
        resp2 = await client.post(
            "/webhook/crm",
            json=_make_crm_payload(crm_id=crm_id),
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp2.status == 200
        data = await resp2.json()
        assert data["status"] == "duplicate"

    @pytest.mark.asyncio
    async def test_missing_auth_returns_401(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.post("/webhook/crm", json=_make_crm_payload())
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_wrong_auth_returns_401(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.post(
            "/webhook/crm",
            json=_make_crm_payload(),
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_no_secret_configured_returns_401(self, aiohttp_client):
        """When webhook_secret is empty, all requests are rejected (fail-closed)."""
        from nanobot.webhook.routes import setup_routes
        application = web.Application()
        application["bus"] = MessageBus()
        application["channels"] = {}
        application["config"] = GatewayConfig(webhook_secret="")
        setup_routes(application)
        client = await aiohttp_client(application)
        resp = await client.post("/webhook/crm", json=_make_crm_payload())
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_missing_phone_returns_400(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        payload = _make_crm_payload(crm_id=f"test-{uuid.uuid4()}")
        del payload["data"]["cliente"]["telefono_whatsapp"]
        resp = await client.post(
            "/webhook/crm",
            json=payload,
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_missing_crm_mensaje_id_returns_400(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        payload = _make_crm_payload(crm_id=f"test-{uuid.uuid4()}")
        del payload["data"]["crm_mensaje_id"]
        resp = await client.post(
            "/webhook/crm",
            json=payload,
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        resp = await client.post(
            "/webhook/crm",
            data=b"not json",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-secret",
            },
        )
        assert resp.status == 400


class TestFormatCRMEvent:
    """Test the format_crm_event helper."""

    def test_format_without_template(self):
        from nanobot.webhook.routes import format_crm_event
        payload = _make_crm_payload()
        result = format_crm_event(payload)
        assert "prenda_terminada" in result
        assert "Marita" in result
        assert "B001-4" in result
        assert "S/25.00" in result
        assert "lavado" in result

    def test_format_with_template(self):
        from nanobot.webhook.routes import format_crm_event
        payload = _make_crm_payload()
        payload["data"]["template_sugerido"] = {
            "contenido_renderizado": "Hola Marita! Tus prendas están listas."
        }
        result = format_crm_event(payload)
        assert result == "Hola Marita! Tus prendas están listas."

    def test_format_without_nombre_preferido(self):
        from nanobot.webhook.routes import format_crm_event
        payload = _make_crm_payload()
        payload["data"]["cliente"]["nombre_preferido"] = None
        result = format_crm_event(payload)
        assert "María López" in result
