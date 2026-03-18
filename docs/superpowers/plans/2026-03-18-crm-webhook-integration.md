# CRM Webhook Integration — Fase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an operator marks a garment as "terminado" in GAR CRM, the customer automatically receives a natural WhatsApp message and GAR sees the delivery status updated.

**Architecture:** CRM events enter Nanobot via `/webhook/crm` endpoint, flow through the MessageBus as `crm_event` channel, get processed by AgentLoop (which generates or uses a template message), exit via EvolutionChannel to WhatsApp, and update `crm_mensajes` in Supabase. On the GAR side, a DB trigger fires an Edge Function that gathers data and POSTs to Nanobot.

**Tech Stack:** Python 3.11+ (aiohttp, httpx), TypeScript/Deno (Supabase Edge Functions), PostgreSQL (triggers, pg_net)

**Spec:** `docs/superpowers/specs/2026-03-17-crm-webhook-integration-design.md`

---

## File Structure

| Repo | File | Responsibility |
|------|------|----------------|
| Nanobot | `nanobot/agent/loop.py` | Modify: add cross-channel routing via `metadata["reply_channel"]` |
| Nanobot | `nanobot/webhook/routes.py` | Modify: add `/webhook/crm` route + `phone_to_jid` helper |
| Nanobot | `nanobot/webhook/server.py` | Modify: inject `config` into app context for auth |
| Nanobot | `nanobot/integrations/__init__.py` | Create: empty package |
| Nanobot | `nanobot/integrations/supabase.py` | Create: lightweight Supabase client for crm_mensajes updates |
| Nanobot | `nanobot/channels/manager.py` | Modify: wire Supabase callback after outbound send |
| Nanobot | `workspace/agents/lavanderia/agent.yaml` | Modify: add `crm_event` to channels |
| Nanobot | `tests/test_crm_webhook.py` | Create: tests for CRM webhook + routing |
| Nanobot | `tests/test_supabase_integration.py` | Create: tests for Supabase client |
| GAR | `supabase/functions/notify-nanobot/index.ts` | Create: Edge Function |
| GAR | `supabase/migrations/20260318_nanobot_integration.sql` | Create: enums + trigger |

---

## Task 1: Cross-channel routing in AgentLoop

**Files:**
- Modify: `nanobot/agent/loop.py:331-334`
- Test: `tests/test_crm_webhook.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crm_webhook.py
"""Tests for CRM webhook integration."""

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage


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
```

- [ ] **Step 2: Run test to verify it passes (data-only test)**

Run: `pytest tests/test_crm_webhook.py::TestCrossChannelRouting -v`
Expected: PASS (this tests the pattern, not the loop code yet)

- [ ] **Step 3: Modify `_process_message()` in loop.py**

In `nanobot/agent/loop.py`, change lines 331-334 from:

```python
return OutboundMessage(
    channel=msg.channel,
    chat_id=msg.chat_id,
    content=final_content
)
```

To:

```python
out_channel = msg.metadata.get("reply_channel", msg.channel)
return OutboundMessage(
    channel=out_channel,
    chat_id=msg.chat_id,
    content=final_content,
    metadata=msg.metadata,
)
```

- [ ] **Step 4: Fix error handler in `_handle_message()` (same file, lines 206-212)**

Change the error handler from:

```python
except Exception as e:
    logger.error(f"Error processing message: {e}")
    await self.bus.publish_outbound(OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=f"Sorry, I encountered an error: {str(e)}"
    ))
```

To:

```python
except Exception as e:
    logger.error(f"Error processing message: {e}")
    out_channel = msg.metadata.get("reply_channel", msg.channel)
    await self.bus.publish_outbound(OutboundMessage(
        channel=out_channel,
        chat_id=msg.chat_id,
        content=f"Sorry, I encountered an error: {str(e)}"
    ))
```

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add nanobot/agent/loop.py tests/test_crm_webhook.py
git commit -m "feat: add cross-channel routing via reply_channel metadata"
```

---

## Task 2: Inject config into webhook server

**Files:**
- Modify: `nanobot/webhook/server.py:37-39`

The CRM webhook route needs access to `config.webhook_secret` for auth. Currently only `bus` and `channels` are injected.

- [ ] **Step 1: Modify server.py to inject config**

In `nanobot/webhook/server.py`, after line 39 (`app["channels"] = channels`), add:

```python
app["config"] = config
```

- [ ] **Step 2: Run existing tests to verify no regression**

Run: `pytest tests/test_evolution.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add nanobot/webhook/server.py
git commit -m "feat: inject gateway config into webhook app context"
```

---

## Task 3: CRM webhook route with auth

**Files:**
- Modify: `nanobot/webhook/routes.py`
- Test: `tests/test_crm_webhook.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_crm_webhook.py`:

```python
import asyncio
import pytest
from aiohttp import web
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import GatewayConfig


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
        resp = await client.post(
            "/webhook/crm",
            json=_make_crm_payload(),
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status == 202

    @pytest.mark.asyncio
    async def test_valid_crm_event_publishes_to_bus(self, aiohttp_client, app, bus):
        client = await aiohttp_client(app)
        await client.post(
            "/webhook/crm",
            json=_make_crm_payload(),
            headers={"Authorization": "Bearer test-secret"},
        )
        msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert msg.channel == "crm_event"
        assert msg.sender_id == "crm_system"
        assert msg.chat_id == "51987654321@s.whatsapp.net"
        assert "prenda_terminada" in msg.content
        assert "Marita" in msg.content
        assert msg.metadata["event_type"] == "prenda_terminada"
        assert msg.metadata["crm_mensaje_id"] == "test-uuid"
        assert msg.metadata["reply_channel"] == "whatsapp"

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
    async def test_missing_phone_returns_400(self, aiohttp_client, app):
        client = await aiohttp_client(app)
        payload = _make_crm_payload()
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
        payload = _make_crm_payload()
        del payload["data"]["crm_mensaje_id"]
        resp = await client.post(
            "/webhook/crm",
            json=payload,
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status == 400

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
        assert "Hola Marita! Tus prendas están listas." in result
        assert "mensaje exacto" in result

    def test_format_without_nombre_preferido(self):
        from nanobot.webhook.routes import format_crm_event
        payload = _make_crm_payload()
        payload["data"]["cliente"]["nombre_preferido"] = None
        result = format_crm_event(payload)
        assert "María López" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crm_webhook.py::TestCRMWebhook -v`
Expected: FAIL (route doesn't exist yet)

- [ ] **Step 3: Add `phone_to_jid` helper and CRM route to routes.py**

In `nanobot/webhook/routes.py`, add:

1. Import `InboundMessage` from `nanobot.bus.events`
2. Add `phone_to_jid()` helper function
3. Add `format_crm_event()` to build the content string for the agent
4. Add `handle_crm_webhook()` handler
5. Register route in `setup_routes()`

```python
# Add to imports
from nanobot.bus.events import InboundMessage

# Helper functions
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
        return (
            f"EVENTO CRM: {payload['event']}. "
            f"Envía este mensaje exacto por WhatsApp al cliente: "
            f"{template['contenido_renderizado']}"
        )

    return (
        f"EVENTO CRM: {payload['event']}. "
        f"Cliente: {nombre} ({cliente['nombre']}), tel: {cliente['telefono_whatsapp']}. "
        f"Pedido {pedido['codigo']}: {prendas_txt}. "
        f"Saldo pendiente: {saldo_txt}. "
        f"Entrega: {pedido.get('fecha_entrega', 'no asignada')}. "
        f"Envía un mensaje WhatsApp natural y amigable avisando que sus prendas están listas "
        f"para recoger. Usa su nombre preferido. Si hay saldo pendiente, menciónalo con tacto."
    )


# In setup_routes(), add:
app.router.add_post("/webhook/crm", handle_crm_webhook)


async def handle_crm_webhook(request: web.Request) -> web.Response:
    """Handle incoming CRM events from GAR."""
    # Auth check (fail-closed: reject if no secret configured)
    config = request.app.get("config")
    secret = config.webhook_secret if config else ""
    auth = request.headers.get("Authorization", "")
    if not secret or auth != f"Bearer {secret}":
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crm_webhook.py -v`
Expected: All PASS

- [ ] **Step 5: Run all tests for regression**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add nanobot/webhook/routes.py tests/test_crm_webhook.py
git commit -m "feat: add /webhook/crm endpoint with auth and validation"
```

---

## Task 4: Supabase integration module

**Files:**
- Create: `nanobot/integrations/__init__.py`
- Create: `nanobot/integrations/supabase.py`
- Test: `tests/test_supabase_integration.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_supabase_integration.py
"""Tests for Supabase CRM integration client."""

import pytest
import respx
import httpx


class TestSupabaseCRMClient:
    """Test the Supabase client for crm_mensajes updates."""

    def _make_client(self):
        from nanobot.integrations.supabase import SupabaseCRMClient
        return SupabaseCRMClient(
            url="https://test.supabase.co",
            service_key="test-service-key",
        )

    def test_client_creation(self):
        client = self._make_client()
        assert client is not None

    @respx.mock
    @pytest.mark.asyncio
    async def test_mark_sent_updates_crm_mensaje(self):
        client = self._make_client()
        route = respx.patch(
            "https://test.supabase.co/rest/v1/crm_mensajes",
        ).mock(return_value=httpx.Response(200, json=[{}]))

        await client.mark_sent(
            crm_mensaje_id="uuid-123",
            evolution_msg_id="BAE123",
            mensaje_generado="Hola Marita!",
        )
        await client.close()

        assert route.called
        request = route.calls[0].request
        assert request.headers["apikey"] == "test-service-key"
        assert "uuid-123" in str(request.url)

    @respx.mock
    @pytest.mark.asyncio
    async def test_mark_failed_updates_crm_mensaje(self):
        client = self._make_client()
        route = respx.patch(
            "https://test.supabase.co/rest/v1/crm_mensajes",
        ).mock(return_value=httpx.Response(200, json=[{}]))

        await client.mark_failed(
            crm_mensaje_id="uuid-456",
            error="Evolution API error: 400",
            retry_count=2,
        )
        await client.close()

        assert route.called

    @respx.mock
    @pytest.mark.asyncio
    async def test_mark_sent_handles_api_error_gracefully(self):
        client = self._make_client()
        respx.patch(
            "https://test.supabase.co/rest/v1/crm_mensajes",
        ).mock(return_value=httpx.Response(500, text="Internal error"))

        # Should not raise — errors are logged, not propagated
        await client.mark_sent(
            crm_mensaje_id="uuid-789",
            evolution_msg_id="BAE456",
            mensaje_generado="Test",
        )
        await client.close()

    def test_disabled_when_no_url(self):
        from nanobot.integrations.supabase import SupabaseCRMClient
        client = SupabaseCRMClient(url="", service_key="")
        assert client.enabled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_supabase_integration.py -v`
Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Create the integrations package**

Create `nanobot/integrations/__init__.py` (empty file).

- [ ] **Step 4: Implement the Supabase client**

Create `nanobot/integrations/supabase.py`:

```python
"""Lightweight Supabase client for updating crm_mensajes."""

from datetime import datetime, timezone

import httpx
from loguru import logger


class SupabaseCRMClient:
    """Updates crm_mensajes table via Supabase REST API.

    Uses httpx to avoid adding supabase-py as a heavy dependency.
    All operations are fire-and-forget (errors logged, not raised).
    """

    def __init__(self, url: str, service_key: str):
        self.enabled = bool(url and service_key)
        if self.enabled:
            self._client = httpx.AsyncClient(
                base_url=f"{url.rstrip('/')}/rest/v1",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                timeout=15.0,
            )
        else:
            self._client = None
            logger.info("SupabaseCRMClient disabled (no SUPABASE_URL configured)")

    async def mark_sent(
        self,
        crm_mensaje_id: str,
        evolution_msg_id: str,
        mensaje_generado: str,
    ) -> None:
        """Mark a crm_mensajes record as successfully sent."""
        if not self.enabled:
            return
        now = datetime.now(timezone.utc).isoformat()
        await self._update(crm_mensaje_id, {
            "estado_envio": "enviado_api",
            "mensaje_renderizado": mensaje_generado,
            "metadata": {
                "source": "nanobot",
                "agent": "lavanderia",
                "generation_mode": "llm",
                "evolution_msg_id": evolution_msg_id,
                "sent_at": now,
            },
        })

    async def mark_failed(
        self,
        crm_mensaje_id: str,
        error: str,
        retry_count: int = 0,
    ) -> None:
        """Mark a crm_mensajes record as failed."""
        if not self.enabled:
            return
        now = datetime.now(timezone.utc).isoformat()
        await self._update(crm_mensaje_id, {
            "estado_envio": "fallido",
            "detalle_error": error,
            "metadata": {
                "source": "nanobot",
                "error_at": now,
                "retry_count": retry_count,
            },
        })

    async def _update(self, crm_mensaje_id: str, data: dict) -> None:
        """Update a crm_mensajes record by ID."""
        try:
            resp = await self._client.patch(
                "/crm_mensajes",
                params={"id": f"eq.{crm_mensaje_id}"},
                json=data,
            )
            if resp.status_code not in (200, 204):
                logger.error(
                    "Supabase update failed for {}: {} {}",
                    crm_mensaje_id, resp.status_code, resp.text[:200],
                )
        except Exception as e:
            logger.error("Supabase request failed for {}: {}", crm_mensaje_id, e)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_supabase_integration.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add nanobot/integrations/__init__.py nanobot/integrations/supabase.py tests/test_supabase_integration.py
git commit -m "feat: add Supabase client for crm_mensajes status updates"
```

---

## Task 5: Wire Supabase client into outbound dispatch

**Files:**
- Modify: `nanobot/channels/manager.py:187-211`
- Modify: `nanobot/channels/manager.py` (constructor — accept config)
- Modify: `nanobot/cli/commands.py` (pass config to ChannelManager)

The SupabaseCRMClient must be called after EvolutionChannel.send() succeeds or fails. The cleanest hook is in `_dispatch_outbound()`, which already handles send + error logging.

- [ ] **Step 1: Modify ChannelManager to accept and store Supabase config**

In `nanobot/channels/manager.py`, modify `__init__` to accept optional supabase config and create the client:

```python
from nanobot.integrations.supabase import SupabaseCRMClient

# In __init__, after existing init:
self._crm_client = SupabaseCRMClient(
    url=config.tools.supabase.url if hasattr(config, 'tools') else "",
    service_key=config.tools.supabase.service_key if hasattr(config, 'tools') else "",
)
```

Note: Check how `ChannelManager.__init__` currently receives config and add the supabase client init accordingly.

- [ ] **Step 2: Modify `_dispatch_outbound()` to call Supabase after CRM sends**

In `_dispatch_outbound()`, after the `channel.send(msg)` call (line 202), add the Supabase callback:

```python
channel = self.channels.get(msg.channel)
if channel:
    try:
        logger.info(f"Outbound → {msg.channel}:{msg.chat_id} | {msg.content[:200]}")
        await channel.send(msg)

        # Update crm_mensajes if this was a CRM-triggered message
        crm_id = (msg.metadata or {}).get("crm_mensaje_id")
        if crm_id and self._crm_client.enabled:
            await self._crm_client.mark_sent(
                crm_mensaje_id=crm_id,
                evolution_msg_id="",  # Evolution doesn't return ID in current impl
                mensaje_generado=msg.content,
            )

    except Exception as e:
        logger.error(f"Error sending to {msg.channel}: {e}")

        # Mark CRM message as failed
        crm_id = (msg.metadata or {}).get("crm_mensaje_id")
        if crm_id and self._crm_client.enabled:
            await self._crm_client.mark_failed(
                crm_mensaje_id=crm_id,
                error=str(e),
            )
```

- [ ] **Step 3: Run all tests to verify no regression**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add nanobot/channels/manager.py
git commit -m "feat: wire Supabase crm_mensajes updates into outbound dispatch"
```

---

## Task 6: Update lavanderia agent config (was Task 5)

**Files:**
- Modify: `workspace/agents/lavanderia/agent.yaml`

- [ ] **Step 1: Add crm_event to channels**

Change `workspace/agents/lavanderia/agent.yaml` from:

```yaml
channels: [whatsapp]
```

To:

```yaml
channels: [whatsapp, crm_event]
```

- [ ] **Step 2: Verify agent discovery still works**

Run: `uv run nanobot status` or `python -c "from nanobot.agent.factory import discover_agents; from nanobot.config.schema import Config; c = Config(); print(discover_agents(c.workspace_path))"`
Expected: lavanderia agent discovered with channels including crm_event

- [ ] **Step 3: Commit**

```bash
git add workspace/agents/lavanderia/agent.yaml
git commit -m "feat: add crm_event channel to lavanderia agent"
```

---

## Task 7: GAR migration — enums + trigger

**Files:**
- Create: `C:\Users\fanny\OneDrive\Documentos\GitHub\gar\supabase\migrations\20260318_nanobot_integration.sql`

- [ ] **Step 1: Create the migration file**

```sql
-- Migration: Add nanobot integration support
-- Adds enum values for automated messaging and DB trigger for prenda_terminada

-- 1. Add new enum values for send_status
ALTER TYPE send_status ADD VALUE IF NOT EXISTS 'enviado_api';
ALTER TYPE send_status ADD VALUE IF NOT EXISTS 'fallido';

-- 2. Add new enum value for message_type
ALTER TYPE message_type ADD VALUE IF NOT EXISTS 'automatico_nanobot';

-- 3. Enable pg_net extension (for HTTP calls from triggers)
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

-- 4. Function that calls the notify-nanobot Edge Function
CREATE OR REPLACE FUNCTION notify_prenda_terminada()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.estado = 'terminado' AND (OLD.estado IS NULL OR OLD.estado != 'terminado') THEN
        PERFORM net.http_post(
            url := CONCAT(
                current_setting('app.supabase_url', true),
                '/functions/v1/notify-nanobot'
            ),
            body := json_build_object(
                'prenda_id', NEW.prenda_id,
                'pedido_id', NEW.pedido_id
            )::text,
            headers := json_build_object(
                'Content-Type', 'application/json',
                'Authorization', CONCAT(
                    'Bearer ',
                    current_setting('app.supabase_anon_key', true)
                )
            )::jsonb
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 5. Trigger on prendas table
DROP TRIGGER IF EXISTS trg_prenda_terminada ON prendas;

CREATE TRIGGER trg_prenda_terminada
AFTER UPDATE OF estado ON prendas
FOR EACH ROW
WHEN (NEW.estado = 'terminado')
EXECUTE FUNCTION notify_prenda_terminada();
```

- [ ] **Step 2: Apply migration locally or via Supabase dashboard**

Option A (CLI): `cd gar && supabase db push`
Option B (Dashboard): Copy SQL into Supabase SQL Editor and execute

- [ ] **Step 3: Verify enum values exist**

Run in Supabase SQL Editor:
```sql
SELECT enum_range(NULL::send_status);
SELECT enum_range(NULL::message_type);
```
Expected: `enviado_api` and `fallido` in send_status, `automatico_nanobot` in message_type

- [ ] **Step 4: Commit in GAR repo**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
git add supabase/migrations/20260318_nanobot_integration.sql
git commit -m "feat: add nanobot integration enums and prenda_terminada trigger"
```

---

## Task 8: GAR Edge Function — notify-nanobot

**Files:**
- Create: `C:\Users\fanny\OneDrive\Documentos\GitHub\gar\supabase\functions\notify-nanobot\index.ts`

- [ ] **Step 1: Create the Edge Function**

```typescript
/**
 * Edge Function: notify-nanobot
 *
 * Called by DB trigger when prenda.estado changes to 'terminado'.
 * Gathers client/order/garment data, creates a crm_mensajes record,
 * and POSTs the event to Nanobot's /webhook/crm endpoint.
 */

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2.56.1';

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};

interface TriggerPayload {
  prenda_id: string;
  pedido_id: string;
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response(null, { headers: corsHeaders });
  }

  if (req.method !== 'POST') {
    return new Response(
      JSON.stringify({ success: false, error: 'Método no permitido' }),
      { status: 405, headers: { ...corsHeaders, 'Content-Type': 'application/json; charset=utf-8' } }
    );
  }

  try {
    const body: TriggerPayload = await req.json();
    const { prenda_id, pedido_id } = body;

    if (!prenda_id || !pedido_id) {
      throw new Error('prenda_id y pedido_id son requeridos');
    }

    console.log(`📦 notify-nanobot: prenda=${prenda_id}, pedido=${pedido_id}`);

    // Connect to Supabase
    const supabase = createClient(
      Deno.env.get('SUPABASE_URL') ?? '',
      Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? ''
    );

    // 1. Get the pedido with cliente info
    const { data: pedido, error: pedidoError } = await supabase
      .from('pedidos')
      .select('*, clientes(*)')
      .eq('codigo', pedido_id)
      .single();

    if (pedidoError || !pedido) {
      console.error('❌ Pedido no encontrado:', pedidoError);
      throw new Error(`Pedido ${pedido_id} no encontrado`);
    }

    const cliente = pedido.clientes;
    if (!cliente) {
      throw new Error(`Cliente no encontrado para pedido ${pedido_id}`);
    }

    // 2. Check opt-in and phone
    const phone = cliente.telefono_whatsapp || cliente.telefono;
    if (!phone) {
      console.log('⚠️ Cliente sin teléfono, omitiendo notificación');
      return new Response(
        JSON.stringify({ success: true, skipped: true, reason: 'no_phone' }),
        { headers: { ...corsHeaders, 'Content-Type': 'application/json; charset=utf-8' } }
      );
    }

    if (cliente.whatsapp_opt_in === false) {
      console.log('⚠️ Cliente con opt-out, omitiendo notificación');
      return new Response(
        JSON.stringify({ success: true, skipped: true, reason: 'opt_out' }),
        { headers: { ...corsHeaders, 'Content-Type': 'application/json; charset=utf-8' } }
      );
    }

    // 3. Get all finished prendas for this pedido
    const { data: prendas } = await supabase
      .from('prendas')
      .select('prenda_id, servicio, cantidad')
      .eq('pedido_id', pedido_id)
      .eq('estado', 'terminado');

    // 4. Calculate saldo
    const importe = pedido.importe || 0;
    const pagoEfectivo = pedido.pago_efectivo || 0;
    const pagoYape = pedido.pago_yape || 0;
    const pagoCredito = pedido.pago_credito || 0;
    const saldo = importe - pagoEfectivo - pagoYape - pagoCredito;

    // 5. Create crm_mensajes record as 'pendiente'
    const { data: crmMsg, error: crmError } = await supabase
      .from('crm_mensajes')
      .insert({
        cliente_id: cliente.cliente_id,
        pedido_id: pedido_id,
        sucursal_id: pedido.sucursal_id,
        canal: 'whatsapp',
        tipo: 'automatico_nanobot',
        mensaje_renderizado: '',  // Will be filled by Nanobot
        estado_envio: 'pendiente',
        telefono_destino: phone,
        metadata: {
          auto_generated: true,
          generated_at: new Date().toISOString(),
          event_type: 'prenda_terminada',
          cliente_nombre: cliente.nombre,
        },
      })
      .select('id')
      .single();

    if (crmError) {
      console.error('❌ Error creando crm_mensajes:', crmError);
      throw new Error(`Error al crear registro CRM: ${crmError.message}`);
    }

    // 6. POST to Nanobot
    const nanobotUrl = Deno.env.get('NANOBOT_WEBHOOK_URL');
    const nanobotSecret = Deno.env.get('NANOBOT_WEBHOOK_SECRET');

    if (!nanobotUrl) {
      console.warn('⚠️ NANOBOT_WEBHOOK_URL no configurado, registro queda pendiente');
      return new Response(
        JSON.stringify({ success: true, crm_mensaje_id: crmMsg.id, nanobot: 'skipped' }),
        { headers: { ...corsHeaders, 'Content-Type': 'application/json; charset=utf-8' } }
      );
    }

    const webhookPayload = {
      event: 'prenda_terminada',
      timestamp: new Date().toISOString(),
      sucursal_id: pedido.sucursal_id,
      data: {
        cliente: {
          cliente_id: cliente.cliente_id,
          nombre: cliente.nombre,
          nombre_preferido: cliente.nombre_preferido || null,
          telefono_whatsapp: phone,
          whatsapp_opt_in: cliente.whatsapp_opt_in ?? true,
          ultimo_indice_plantilla: cliente.ultimo_indice_plantilla || 0,
        },
        pedido: {
          codigo: pedido.codigo,
          importe: importe,
          pago_efectivo: pagoEfectivo,
          pago_yape: pagoYape,
          pago_credito: pagoCredito,
          saldo: saldo,
          fecha_entrega: pedido.fecha_entrega || null,
        },
        prendas: prendas || [],
        crm_mensaje_id: crmMsg.id,
      },
    };

    try {
      const nanobotResp = await fetch(nanobotUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${nanobotSecret}`,
        },
        body: JSON.stringify(webhookPayload),
      });

      if (!nanobotResp.ok) {
        const errorText = await nanobotResp.text();
        console.error(`❌ Nanobot respondió ${nanobotResp.status}: ${errorText}`);
      } else {
        console.log(`✅ Nanobot aceptó evento: ${crmMsg.id}`);
      }
    } catch (fetchError) {
      console.error('❌ Error conectando a Nanobot:', fetchError);
      // Record stays as 'pendiente' for manual send
    }

    return new Response(
      JSON.stringify({
        success: true,
        crm_mensaje_id: crmMsg.id,
      }),
      { headers: { ...corsHeaders, 'Content-Type': 'application/json; charset=utf-8' } }
    );

  } catch (error: unknown) {
    const errorMessage = error instanceof Error ? error.message : 'Error desconocido';
    console.error('❌ Error en notify-nanobot:', errorMessage);

    return new Response(
      JSON.stringify({ success: false, error: errorMessage }),
      {
        status: 400,
        headers: { ...corsHeaders, 'Content-Type': 'application/json; charset=utf-8' },
      }
    );
  }
});
```

- [ ] **Step 2: Configure secrets in Supabase**

In the Supabase dashboard → Edge Functions → Secrets, add:
- `NANOBOT_WEBHOOK_URL`: URL where Nanobot is reachable (e.g., `http://<server-ip>:18790/webhook/crm`)
- `NANOBOT_WEBHOOK_SECRET`: Shared secret matching `NANOBOT_GATEWAY__WEBHOOK_SECRET`

- [ ] **Step 3: Deploy Edge Function**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
supabase functions deploy notify-nanobot
```

- [ ] **Step 4: Test Edge Function manually**

Via Supabase dashboard or curl:
```bash
curl -X POST https://otdihcovzputyvfblbmz.supabase.co/functions/v1/notify-nanobot \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <ANON_KEY>" \
  -d '{"prenda_id": "PRE-TEST-001", "pedido_id": "B001-4"}'
```
Expected: JSON response with `success: true` and `crm_mensaje_id`

- [ ] **Step 5: Commit in GAR repo**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
git add supabase/functions/notify-nanobot/index.ts
git commit -m "feat: add notify-nanobot Edge Function for prenda_terminada events"
```

---

## Task 9: Configure env vars and end-to-end test

**Files:**
- Modify: Nanobot `.env`

- [ ] **Step 1: Add webhook secret to .env**

Add to `.env`:
```bash
NANOBOT_GATEWAY__WEBHOOK_SECRET=<generate-a-strong-secret>
```

Verify Supabase config already present:
```bash
SUPABASE_URL=https://otdihcovzputyvfblbmz.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
```

Note: The Supabase config in `.env` uses standalone env vars. Map them to Nanobot's config path if needed:
```bash
NANOBOT_TOOLS__SUPABASE__URL=https://otdihcovzputyvfblbmz.supabase.co
NANOBOT_TOOLS__SUPABASE__SERVICE_KEY=eyJ...
```

- [ ] **Step 2: Start Nanobot gateway locally**

```bash
uv run nanobot gateway
```
Expected: Webhook server listening on port 18790

- [ ] **Step 3: Test CRM webhook with curl**

```bash
curl -X POST http://localhost:18790/webhook/crm \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-secret>" \
  -d '{
    "event": "prenda_terminada",
    "timestamp": "2026-03-18T14:30:00.000Z",
    "sucursal_id": "test-sucursal",
    "data": {
      "cliente": {
        "cliente_id": "C-000001",
        "nombre": "María López",
        "nombre_preferido": "Marita",
        "telefono_whatsapp": "+51987654321",
        "whatsapp_opt_in": true,
        "ultimo_indice_plantilla": 0
      },
      "pedido": {
        "codigo": "B001-4",
        "importe": 45.00,
        "pago_efectivo": 20.00,
        "pago_yape": 0.00,
        "pago_credito": 0.00,
        "saldo": 25.00,
        "fecha_entrega": "2026-03-18"
      },
      "prendas": [
        {"prenda_id": "PRE-0001", "servicio": "lavado", "cantidad": 2}
      ],
      "crm_mensaje_id": "test-uuid-1234"
    }
  }'
```
Expected: `{"status": "accepted", "crm_mensaje_id": "test-uuid-1234"}` with HTTP 202

- [ ] **Step 4: Verify agent processes the event**

Check Nanobot logs for:
1. "CRM webhook accepted: event=prenda_terminada"
2. AgentLoop processing message from crm_event channel
3. OutboundMessage sent to whatsapp channel
4. EvolutionChannel sending (or mock logging) the WhatsApp message

- [ ] **Step 5: Test full trigger flow (when Evolution API is connected)**

In GAR, update a prenda to terminado → verify WhatsApp message arrives → verify crm_mensajes updated to `enviado_api`

- [ ] **Step 6: Run all Nanobot tests**

```bash
pytest tests/ -v
```
Expected: All PASS
