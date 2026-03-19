# Fase 2: Boleta + Bienvenida — Implementation Plan (Templates)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an operator creates an order in GAR and the boleta is emitted via Rapifac, the customer receives a WhatsApp message (welcome or thank you) followed by the boleta PDF. Messages use pre-defined templates — no LLM tokens consumed.

**Architecture:** GAR renders the template in the Edge Function using `template_sugerido.contenido_renderizado` → Nanobot receives it, skips LLM, forwards text + PDF via Evolution API. Template rotation via `ultimo_indice_plantilla` prevents spam.

**Tech Stack:** Python 3.11+ (aiohttp, httpx), TypeScript/Deno (Supabase Edge Functions), Evolution API v2.3.7

**Spec:** `docs/superpowers/specs/2026-03-18-fase2-boleta-bienvenida-design.md`

---

## File Structure

| Repo | File | Responsibility |
|------|------|----------------|
| Nanobot | `nanobot/webhook/routes.py` | Modify: forward `boleta` and `es_primer_pedido` in metadata |
| Nanobot | `nanobot/channels/evolution.py` | Modify: URL passthrough in `_send_media()`, reorder `send()` text-first |
| Nanobot | `nanobot/agent/loop.py` | Modify: attach PDF media to last outbound chunk |
| Nanobot | `workspace/agents/lavanderia/SOUL.md` | Modify: strengthen `|||` instruction |
| Nanobot | `tests/test_crm_webhook.py` | Modify: add boleta metadata forwarding test |
| GAR | `supabase/functions/notify-nanobot/index.ts` | Modify: add `boleta_emitida` case with template rendering |
| GAR | Hook that calls `emitir-boleta` | Modify: trigger notification after successful emission |
| GAR | `supabase/migrations/` | Create: update unique index on `crm_mensajes` |

---

## Task 1: Forward boleta metadata in webhook handler

**Files:**
- Modify: `nanobot/webhook/routes.py:218-228`
- Test: `tests/test_crm_webhook.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_crm_webhook.py`:

```python
def _make_boleta_payload(es_primer_pedido=True, crm_id="test-boleta-uuid"):
    return {
        "event": "boleta_emitida",
        "timestamp": "2026-03-18T14:30:00.000Z",
        "sucursal_id": "test-sucursal",
        "data": {
            "cliente": {
                "cliente_id": "C-000001",
                "nombre": "María López",
                "nombre_preferido": "Marita",
                "telefono_whatsapp": "+51987654321",
                "whatsapp_opt_in": True,
            },
            "pedido": {
                "codigo": "B001-4",
                "importe": 45.00,
                "fecha_entrega": "2026-03-20",
            },
            "boleta": {
                "serie": "B001",
                "correlativo": "00001234",
                "codigo_completo": "B001-00001234",
                "enlace_pdf": "https://rapifac.com/boletas/xxx.pdf",
            },
            "es_primer_pedido": es_primer_pedido,
            "crm_mensaje_id": crm_id,
            "template_sugerido": {
                "contenido_renderizado": "¡Hola Marita! Bienvenida a El Chinito Veloz\n|||\nAquí tienes tu boleta B001-00001234"
            },
        },
    }


class TestBoletaEmitida:
    """Test boleta_emitida event handling."""

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
    async def test_boleta_forwards_metadata(self, aiohttp_client, app, bus):
        crm_id = f"test-boleta-{uuid.uuid4()}"
        client = await aiohttp_client(app)
        await client.post(
            "/webhook/crm",
            json=_make_boleta_payload(crm_id=crm_id),
            headers={"Authorization": "Bearer test-secret"},
        )
        msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert msg.metadata["event_type"] == "boleta_emitida"
        assert msg.metadata["boleta"]["enlace_pdf"] == "https://rapifac.com/boletas/xxx.pdf"
        assert msg.metadata["es_primer_pedido"] is True

    @pytest.mark.asyncio
    async def test_boleta_uses_template_as_content(self, aiohttp_client, app, bus):
        crm_id = f"test-boleta-{uuid.uuid4()}"
        client = await aiohttp_client(app)
        await client.post(
            "/webhook/crm",
            json=_make_boleta_payload(crm_id=crm_id),
            headers={"Authorization": "Bearer test-secret"},
        )
        msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        # Template should be used as content (no LLM needed)
        assert "Bienvenida a El Chinito Veloz" in msg.content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crm_webhook.py::TestBoletaEmitida -v`
Expected: FAIL — `boleta` and `es_primer_pedido` not in metadata

- [ ] **Step 3: Add boleta/es_primer_pedido to metadata in `handle_crm_webhook()`**

In `nanobot/webhook/routes.py`, modify the metadata dict (lines 218-228):

```python
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
            "boleta": data.get("boleta", {}),
            "es_primer_pedido": data.get("es_primer_pedido", False),
            "template_sugerido": (data.get("template_sugerido") or {}).get(
                "contenido_renderizado"
            ),
        },
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_crm_webhook.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add nanobot/webhook/routes.py tests/test_crm_webhook.py
git commit -m "feat: forward boleta and es_primer_pedido in CRM webhook metadata"
```

---

## Task 2: EvolutionChannel — URL passthrough + text-before-media

**Files:**
- Modify: `nanobot/channels/evolution.py:51-74` (send) and `102-152` (_send_media)
- Test: `tests/test_evolution.py`

- [ ] **Step 1: Write test for URL passthrough**

Add to `tests/test_evolution.py`:

```python
class TestEvolutionMediaURL:
    """Test _send_media with URL passthrough."""

    def _make_channel(self):
        from nanobot.channels.evolution import EvolutionChannel
        from nanobot.config.schema import WhatsAppConfig
        from nanobot.bus.queue import MessageBus
        config = WhatsAppConfig(
            enabled=True, provider="evolution",
            evolution_api_url="", evolution_api_key="test",
            evolution_instance="test",
        )
        return EvolutionChannel(config, MessageBus())

    @pytest.mark.asyncio
    async def test_send_media_url_mock(self):
        channel = self._make_channel()
        result = await channel._send_media("51987654321", "https://rapifac.com/boletas/test.pdf")
        assert result == ""  # Mock mode logs, returns empty

    @pytest.mark.asyncio
    async def test_send_text_before_media(self):
        """send() should not crash with URL media in mock mode."""
        channel = self._make_channel()
        from nanobot.bus.events import OutboundMessage
        msg = OutboundMessage(
            channel="whatsapp",
            chat_id="51987654321@s.whatsapp.net",
            content="Hello",
            media=["https://rapifac.com/test.pdf"],
        )
        await channel.send(msg)  # Should not crash
```

- [ ] **Step 2: Modify `_send_media()` for URL passthrough**

Replace `_send_media()` in `nanobot/channels/evolution.py` (lines 102-152):

```python
async def _send_media(self, number: str, media_path: str) -> str:
    """Send a media file (PDF, image, etc.) via Evolution API.

    Supports both local file paths (read as base64) and public URLs (passed directly).
    """
    if self._mock_mode:
        logger.info("[EvolutionChannel MOCK] media → {}: {}", number, media_path)
        return ""

    import mimetypes
    import os

    is_url = media_path.startswith("http://") or media_path.startswith("https://")

    if is_url:
        mime = "application/pdf"
        filename = media_path.split("/")[-1].split("?")[0]
        media_value = media_path
    else:
        mime, _ = mimetypes.guess_type(media_path)
        mime = mime or "application/octet-stream"
        filename = os.path.basename(media_path)
        try:
            import base64
            with open(media_path, "rb") as f:
                raw = base64.b64encode(f.read()).decode()
            media_value = f"data:{mime};base64,{raw}"
        except FileNotFoundError:
            logger.error("Media file not found: {}", media_path)
            return ""

    if mime.startswith("image/"):
        media_type = "image"
    elif mime.startswith("audio/"):
        media_type = "audio"
    elif mime.startswith("video/"):
        media_type = "video"
    else:
        media_type = "document"

    try:
        resp = await self._client.post(
            f"/message/sendMedia/{self.config.evolution_instance}",
            json={
                "number": number,
                "mediatype": media_type,
                "mimetype": mime,
                "fileName": filename,
                "media": media_value,
            },
        )
        if resp.status_code not in (200, 201):
            logger.error(
                "Evolution API media error {}: {}", resp.status_code, resp.text[:200]
            )
            return ""
        data = resp.json()
        return data.get("key", {}).get("id", "")
    except Exception as e:
        logger.error("Failed to send media via Evolution API: {}", e)
        return ""
```

- [ ] **Step 3: Reorder `send()` — text first, media last**

Replace `send()` in `nanobot/channels/evolution.py` (lines 51-74):

```python
async def send(self, msg: OutboundMessage) -> None:
    """Send message via Evolution API: text chunks first, then media attachments."""
    number = self._jid_to_number(msg.chat_id)

    # 1. Send text content first
    chunks = self._split_message(msg.content)
    last_msg_id = ""
    for chunk in chunks:
        msg_id = await self._send_text(number, chunk)
        if msg_id:
            last_msg_id = msg_id
        if len(chunks) > 1:
            await asyncio.sleep(0.8)

    # 2. Send media attachments after text (PDFs, images, etc.)
    for media_path in (msg.media or []):
        if chunks:
            await asyncio.sleep(0.8)
        msg_id = await self._send_media(number, media_path)
        if msg_id:
            last_msg_id = msg_id

    # Store Evolution message ID in metadata for CRM tracking
    if last_msg_id:
        msg.metadata["evolution_msg_id"] = last_msg_id
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_evolution.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add nanobot/channels/evolution.py tests/test_evolution.py
git commit -m "feat: URL passthrough in _send_media + text-before-media ordering"
```

---

## Task 3: AgentLoop — attach PDF to last outbound chunk

**Files:**
- Modify: `nanobot/agent/loop.py:196-206`

- [ ] **Step 1: Modify chunk dispatch to include media on last chunk**

In `nanobot/agent/loop.py`, replace lines 196-206:

```python
            try:
                response = await self._process_message(msg)
                if response:
                    chunks = _split_chunks(response.content)
                    boleta_pdf = (msg.metadata.get("boleta") or {}).get("enlace_pdf", "")
                    media_list = [boleta_pdf] if boleta_pdf else []

                    for i, chunk in enumerate(chunks):
                        if i > 0:
                            await asyncio.sleep(0.8)
                        is_last = (i == len(chunks) - 1)
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=response.channel,
                            chat_id=response.chat_id,
                            content=chunk,
                            media=media_list if is_last else [],
                            metadata=response.metadata,
                        ))
```

- [ ] **Step 2: Run tests for regression**

Run: `pytest tests/ --ignore=tests/test_commands.py --ignore=tests/test_consolidate_offset.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add nanobot/agent/loop.py
git commit -m "feat: attach PDF media to last outbound chunk in agent loop"
```

---

## Task 4: Strengthen `|||` instruction in SOUL.md

**Files:**
- Modify: `workspace/agents/lavanderia/SOUL.md:46-48`

- [ ] **Step 1: Update format section**

Replace the `## Formato` section (line 46-48):

```markdown
## Formato

Siempre divide tu respuesta en bloques breves separados por `|||`. Cada bloque se envía como mensaje individual de WhatsApp. Reglas:
- Cada bloque debe ser corto (1-3 oraciones). No envíes párrafos largos.
- Separa temas diferentes en bloques distintos (ej: saludo + información + despedida).
- No dividas un mismo tema en múltiples bloques.
- Máximo 3 bloques por respuesta.
- Si tu respuesta es una sola oración, no uses `|||`.
```

- [ ] **Step 2: Commit**

```bash
git add workspace/agents/lavanderia/SOUL.md
git commit -m "feat: strengthen ||| message splitting instruction in SOUL.md"
```

---

## Task 5: GAR — Migration to update crm_mensajes unique index

**Files:**
- Create: `C:\Users\fanny\OneDrive\Documentos\GitHub\gar\supabase\migrations\20260318_crm_mensajes_multi_evento.sql`

- [ ] **Step 1: Create the migration**

```sql
-- Allow multiple pending CRM messages per pedido (one per event type)
DROP INDEX IF EXISTS idx_crm_mensajes_pedido_unico;

CREATE UNIQUE INDEX idx_crm_mensajes_pedido_evento_unico
ON crm_mensajes(pedido_id, tipo)
WHERE estado_envio = 'pendiente' AND pedido_id IS NOT NULL;
```

- [ ] **Step 2: Apply via Supabase CLI or Dashboard**

- [ ] **Step 3: Commit in GAR repo**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
git add supabase/migrations/20260318_crm_mensajes_multi_evento.sql
git commit -m "feat: allow multiple pending crm_mensajes per pedido by event type"
```

---

## Task 6: GAR — Edge Function `boleta_emitida` with templates

**Files:**
- Modify: `C:\Users\fanny\OneDrive\Documentos\GitHub\gar\supabase\functions\notify-nanobot\index.ts`

- [ ] **Step 1: Add templates and boleta_emitida handler**

Templates defined in the Edge Function (simple, no DB table needed yet):

```typescript
const TEMPLATES_BIENVENIDA = [
  "¡Hola {nombre}! Bienvenido/a a El Chinito Veloz 😊 Nos alegra que confíes en nosotros para el cuidado de tus prendas.\n|||\nAquí tienes tu boleta electrónica {boleta_codigo} por S/{importe}.",
  "¡Hola {nombre}! Qué gusto tenerte como nuevo/a cliente de El Chinito Veloz 🙌\n|||\nTe enviamos tu boleta {boleta_codigo} por S/{importe}. ¡Gracias por preferirnos!",
  "¡Bienvenido/a {nombre}! En El Chinito Veloz cuidamos tus prendas como si fueran nuestras 👕✨\n|||\nAquí está tu boleta electrónica {boleta_codigo} — S/{importe}.",
];

const TEMPLATES_RECURRENTE = [
  "¡Hola {nombre}! Gracias por seguir confiando en El Chinito Veloz 😊\n|||\nTe enviamos tu boleta {boleta_codigo} por S/{importe}.",
  "¡Hola {nombre}! Un gusto verte de nuevo 🙌\n|||\nAquí tienes tu boleta electrónica {boleta_codigo} — S/{importe}.",
  "¡{nombre}! Gracias por tu preferencia ✨\n|||\nTu boleta {boleta_codigo} por S/{importe} está lista.",
];
```

Template rendering logic:

```typescript
function renderTemplate(templates: string[], indice: number, vars: Record<string, string>): { rendered: string; nextIndex: number } {
  const idx = indice % templates.length;
  let rendered = templates[idx];
  for (const [key, value] of Object.entries(vars)) {
    rendered = rendered.replaceAll(`{${key}}`, value);
  }
  return { rendered, nextIndex: idx + 1 };
}
```

In the handler, after gathering data:

```typescript
const templates = esPrimerPedido ? TEMPLATES_BIENVENIDA : TEMPLATES_RECURRENTE;
const { rendered, nextIndex } = renderTemplate(
  templates,
  cliente.ultimo_indice_plantilla || 0,
  {
    nombre: cliente.nombre_preferido || cliente.nombre,
    boleta_codigo: `${pedido.boleta_serie}-${pedido.boleta_correlativo}`,
    importe: (pedido.importe || 0).toFixed(2),
  }
);

// Update client's template index for rotation
await supabase
  .from('clientes')
  .update({ ultimo_indice_plantilla: nextIndex })
  .eq('cliente_id', cliente.cliente_id);

// Include rendered template in webhook payload
webhookPayload.data.template_sugerido = {
  contenido_renderizado: rendered,
};
```

- [ ] **Step 2: Deploy Edge Function**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
npx supabase functions deploy notify-nanobot --project-ref sxnfccqpjxoipptgsowu
```

- [ ] **Step 3: Commit in GAR repo**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
git add supabase/functions/notify-nanobot/index.ts
git commit -m "feat: add boleta_emitida event with template rendering and rotation"
```

---

## Task 7: GAR — Trigger notification after boleta emission

**Files:**
- Modify: The hook/component that calls `emitir-boleta` Edge Function

- [ ] **Step 1: Add notification call after boleta emission**

After boleta emission succeeds and URLs are stored, following the `useCompletarPedido` pattern:

```typescript
if (whatsappConfig?.modo_envio === 'api') {
  try {
    await supabase.functions.invoke('notify-nanobot', {
      body: { pedido_id: pedido.codigo, event: 'boleta_emitida' },
    });
  } catch (err) {
    console.error('Error notifying nanobot:', err);
  }
}
```

- [ ] **Step 2: Commit in GAR repo**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
git add <modified-file>
git commit -m "feat: trigger WhatsApp notification after boleta emission"
```

---

## Task 8: E2E verification

- [ ] **Step 1: Test boleta_emitida webhook with template (Nanobot only)**

```python
import httpx
payload = {
    "event": "boleta_emitida",
    "timestamp": "2026-03-19T14:00:00.000Z",
    "sucursal_id": "test-sucursal",
    "data": {
        "cliente": {"cliente_id": "TEST-001", "nombre": "Test", "nombre_preferido": "Testito",
                    "telefono_whatsapp": "+51999999999", "whatsapp_opt_in": True},
        "pedido": {"codigo": "TEST-003", "importe": 50.00, "fecha_entrega": "2026-03-21"},
        "boleta": {"serie": "B001", "correlativo": "00009999", "codigo_completo": "B001-00009999",
                   "enlace_pdf": "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"},
        "es_primer_pedido": True,
        "crm_mensaje_id": "TEST-BOLETA-TEMPLATE",
        "template_sugerido": {
            "contenido_renderizado": "¡Hola Testito! Bienvenido a El Chinito Veloz 😊\n|||\nAquí tu boleta B001-00009999 por S/50.00"
        },
    },
}
resp = httpx.post("http://localhost:18790/webhook/crm", json=payload,
    headers={"Authorization": "Bearer nanobot-gar-webhook-secret-2026"})
print(resp.status_code, resp.json())
```

Expected: 202, no LLM call, two WhatsApp messages + PDF

- [ ] **Step 2: Test full GAR flow**

Create pedido in GAR → boleta emits → WhatsApp arrives with template message + PDF

- [ ] **Step 3: Run all Nanobot tests**

```bash
pytest tests/ --ignore=tests/test_commands.py --ignore=tests/test_consolidate_offset.py -v
```
Expected: All PASS
