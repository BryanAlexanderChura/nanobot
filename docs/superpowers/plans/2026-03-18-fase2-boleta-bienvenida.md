# Fase 2: Boleta + Bienvenida — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an operator creates an order in GAR and the boleta is emitted via Rapifac, the customer receives a WhatsApp message (welcome or thank you) followed by the boleta PDF.

**Architecture:** GAR emits `boleta_emitida` event via Edge Function → Nanobot webhook formats prompt → AgentLoop generates multi-block message with `|||` → EvolutionChannel sends text chunks then PDF via `sendMedia` with URL passthrough.

**Tech Stack:** Python 3.11+ (aiohttp, httpx), TypeScript/Deno (Supabase Edge Functions), Evolution API v2.3.7

**Spec:** `docs/superpowers/specs/2026-03-18-fase2-boleta-bienvenida-design.md`

---

## File Structure

| Repo | File | Responsibility |
|------|------|----------------|
| Nanobot | `nanobot/webhook/routes.py` | Modify: add `boleta_emitida` branch in `format_crm_event()`, forward `boleta`/`es_primer_pedido` in metadata |
| Nanobot | `nanobot/channels/evolution.py` | Modify: URL passthrough in `_send_media()`, reorder `send()` text-first |
| Nanobot | `nanobot/agent/loop.py` | Modify: attach PDF media to last outbound chunk |
| Nanobot | `workspace/agents/lavanderia/SOUL.md` | Modify: strengthen `|||` instruction for all message types |
| Nanobot | `tests/test_crm_webhook.py` | Modify: add `boleta_emitida` format tests |
| Nanobot | `tests/test_evolution.py` | Modify: add URL media test |
| GAR | `supabase/functions/notify-nanobot/index.ts` | Modify: add `boleta_emitida` event case |
| GAR | Hook that calls `emitir-boleta` | Modify: trigger notification after successful emission |
| GAR | `supabase/migrations/` | Create: migration to update unique index on `crm_mensajes` |

---

## Task 1: `format_crm_event()` — boleta_emitida branch

**Files:**
- Modify: `nanobot/webhook/routes.py:24-50`
- Test: `tests/test_crm_webhook.py`

- [ ] **Step 1: Write failing tests**

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
        },
    }


class TestFormatBoletaEmitida:
    """Test format_crm_event for boleta_emitida events."""

    def test_primer_pedido_includes_bienvenida(self):
        from nanobot.webhook.routes import format_crm_event
        result = format_crm_event(_make_boleta_payload(es_primer_pedido=True))
        assert "boleta_emitida" in result
        assert "Marita" in result
        assert "BIENVENIDA" in result
        assert "B001-00001234" in result
        assert "|||" in result

    def test_cliente_recurrente_includes_agradecimiento(self):
        from nanobot.webhook.routes import format_crm_event
        result = format_crm_event(_make_boleta_payload(es_primer_pedido=False))
        assert "AGRADECIMIENTO" in result
        assert "Marita" in result
        assert "B001-00001234" in result

    def test_boleta_includes_importe(self):
        from nanobot.webhook.routes import format_crm_event
        result = format_crm_event(_make_boleta_payload())
        assert "45.00" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crm_webhook.py::TestFormatBoletaEmitida -v`
Expected: FAIL — `format_crm_event()` has no `boleta_emitida` branch, will produce wrong output

- [ ] **Step 3: Add boleta_emitida branch to `format_crm_event()`**

In `nanobot/webhook/routes.py`, modify `format_crm_event()` (line 24-50). Add at the top of the function, after `nombre` is derived (line 32), before the `prendas_txt` line:

```python
def format_crm_event(payload: dict) -> str:
    """Format CRM event payload into a prompt for the agent."""
    data = payload["data"]
    cliente = data["cliente"]
    pedido = data["pedido"]
    nombre = cliente.get("nombre_preferido") or cliente["nombre"]

    # Route by event type
    if payload["event"] == "boleta_emitida":
        boleta = data.get("boleta", {})
        es_primer_pedido = data.get("es_primer_pedido", False)
        tipo_cliente = "BIENVENIDA (primer pedido)" if es_primer_pedido else "AGRADECIMIENTO (cliente recurrente)"
        return (
            f"Genera el mensaje para el cliente. Tu respuesta se enviará automáticamente por WhatsApp.\n\n"
            f"Evento: boleta_emitida\n"
            f"Cliente: {nombre} ({cliente['nombre']})\n"
            f"Tipo: {tipo_cliente}\n"
            f"Pedido: {pedido['codigo']} — S/{pedido.get('importe', 0):.2f}\n"
            f"Boleta: {boleta.get('codigo_completo', '')}\n"
            f"Entrega estimada: {pedido.get('fecha_entrega', 'no asignada')}\n\n"
            f"Usa ||| para separar en dos bloques:\n"
            f"Bloque 1: {'Bienvenida cálida al nuevo cliente' if es_primer_pedido else 'Agradecimiento por su preferencia'}.\n"
            f"Bloque 2: Referencia breve a la boleta (código y monto). El PDF se adjunta automáticamente después."
        )

    # Default: prenda_terminada (existing logic)
    prendas = data.get("prendas", [])
    template = data.get("template_sugerido")
    prendas_txt = ", ".join(f"{p['cantidad']}x {p['servicio']}" for p in prendas)
    saldo_txt = f"S/{pedido['saldo']:.2f}" if pedido.get("saldo") else "pagado"

    if template and template.get("contenido_renderizado"):
        return template["contenido_renderizado"]

    return (
        f"Genera el mensaje para el cliente. Tu respuesta se enviará automáticamente por WhatsApp.\n\n"
        f"Evento: {payload['event']}\n"
        f"Cliente: {nombre} ({cliente['nombre']})\n"
        f"Pedido {pedido['codigo']}: {prendas_txt}\n"
        f"Saldo pendiente: {saldo_txt}\n"
        f"Entrega: {pedido.get('fecha_entrega', 'no asignada')}\n\n"
        f"Escribe un mensaje natural y amigable avisando que sus prendas están listas "
        f"para recoger. Usa su nombre preferido. Si hay saldo pendiente, menciónalo con tacto."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crm_webhook.py -v`
Expected: All PASS (new + existing)

- [ ] **Step 5: Commit**

```bash
git add nanobot/webhook/routes.py tests/test_crm_webhook.py
git commit -m "feat: add boleta_emitida branch in format_crm_event"
```

---

## Task 2: Forward boleta metadata in webhook handler

**Files:**
- Modify: `nanobot/webhook/routes.py:218-228`
- Test: `tests/test_crm_webhook.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_crm_webhook.py` in `TestCRMWebhook`:

```python
@pytest.mark.asyncio
async def test_boleta_event_forwards_metadata(self, aiohttp_client, app, bus):
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_crm_webhook.py::TestCRMWebhook::test_boleta_event_forwards_metadata -v`
Expected: FAIL — `boleta` and `es_primer_pedido` not in metadata

- [ ] **Step 3: Add boleta/es_primer_pedido to metadata in `handle_crm_webhook()`**

In `nanobot/webhook/routes.py`, modify the metadata dict in `InboundMessage` (lines 218-228). Add two fields:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crm_webhook.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add nanobot/webhook/routes.py tests/test_crm_webhook.py
git commit -m "feat: forward boleta and es_primer_pedido in CRM webhook metadata"
```

---

## Task 3: EvolutionChannel — URL passthrough in `_send_media()` + reorder `send()`

**Files:**
- Modify: `nanobot/channels/evolution.py:51-74` (send) and `102-152` (_send_media)
- Test: `tests/test_evolution.py`

- [ ] **Step 1: Write failing test for URL passthrough**

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
    async def test_send_media_url_logs_in_mock(self):
        channel = self._make_channel()
        result = await channel._send_media("51987654321", "https://rapifac.com/boletas/test.pdf")
        # Mock mode just logs, returns empty
        assert result == ""

    @pytest.mark.asyncio
    async def test_send_order_text_before_media(self):
        """send() should send text before media."""
        channel = self._make_channel()
        from nanobot.bus.events import OutboundMessage
        msg = OutboundMessage(
            channel="whatsapp",
            chat_id="51987654321@s.whatsapp.net",
            content="Hello",
            media=["https://rapifac.com/test.pdf"],
        )
        # In mock mode this just logs — verify no crash
        await channel.send(msg)
```

- [ ] **Step 2: Run tests to verify baseline**

Run: `pytest tests/test_evolution.py::TestEvolutionMediaURL -v`
Expected: May pass (mock mode), but serves as regression baseline

- [ ] **Step 3: Modify `_send_media()` for URL passthrough**

In `nanobot/channels/evolution.py`, replace `_send_media()` (lines 102-152):

```python
async def _send_media(self, number: str, media_path: str) -> str:
    """Send a media file (PDF, image, etc.) via Evolution API. Returns message ID.

    Supports both local file paths (read as base64) and public URLs (passed directly).
    """
    if self._mock_mode:
        logger.info("[EvolutionChannel MOCK] media → {}: {}", number, media_path)
        return ""

    import mimetypes
    import os

    is_url = media_path.startswith("http://") or media_path.startswith("https://")

    if is_url:
        mime = "application/pdf"  # Default for URLs; Evolution detects from content
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

    # Determine media type
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

- [ ] **Step 4: Reorder `send()` — text first, media last**

In `nanobot/channels/evolution.py`, replace `send()` (lines 51-74):

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

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_evolution.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add nanobot/channels/evolution.py tests/test_evolution.py
git commit -m "feat: URL passthrough in _send_media + text-before-media ordering"
```

---

## Task 4: AgentLoop — attach PDF to last outbound chunk

**Files:**
- Modify: `nanobot/agent/loop.py:196-206`

- [ ] **Step 1: Modify chunk dispatch to include media on last chunk**

In `nanobot/agent/loop.py`, replace lines 196-206 (the chunk dispatch block inside `_handle_message`):

```python
            try:
                response = await self._process_message(msg)
                if response:
                    chunks = _split_chunks(response.content)
                    # Collect media from metadata (e.g., boleta PDF URL)
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

- [ ] **Step 2: Run existing tests for regression**

Run: `pytest tests/ --ignore=tests/test_commands.py --ignore=tests/test_consolidate_offset.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add nanobot/agent/loop.py
git commit -m "feat: attach PDF media to last outbound chunk in agent loop"
```

---

## Task 5: Strengthen `|||` instruction in SOUL.md

**Files:**
- Modify: `workspace/agents/lavanderia/SOUL.md:46-48`

- [ ] **Step 1: Update SOUL.md format section**

Replace the current `## Formato` section (line 46-48) with:

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

## Task 6: GAR — Migration to update crm_mensajes unique index

**Files:**
- Create: `C:\Users\fanny\OneDrive\Documentos\GitHub\gar\supabase\migrations\20260318_crm_mensajes_multi_evento.sql`

- [ ] **Step 1: Create the migration**

```sql
-- Allow multiple pending CRM messages per pedido (one per event type)
-- Previously: only 1 pending message per pedido
-- Now: 1 pending per pedido+tipo combination (boleta_emitida + prenda_terminada can coexist)

DROP INDEX IF EXISTS idx_crm_mensajes_pedido_unico;

CREATE UNIQUE INDEX idx_crm_mensajes_pedido_evento_unico
ON crm_mensajes(pedido_id, tipo)
WHERE estado_envio = 'pendiente' AND pedido_id IS NOT NULL;
```

- [ ] **Step 2: Apply via Supabase CLI or Dashboard**

Option A (CLI): `cd gar && npx supabase db push`
Option B (Dashboard): Copy SQL into Supabase SQL Editor and execute

- [ ] **Step 3: Verify index exists**

Run in Supabase SQL Editor:
```sql
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'crm_mensajes';
```
Expected: `idx_crm_mensajes_pedido_evento_unico` visible with `(pedido_id, tipo)` columns

- [ ] **Step 4: Commit in GAR repo**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
git add supabase/migrations/20260318_crm_mensajes_multi_evento.sql
git commit -m "feat: allow multiple pending crm_mensajes per pedido by event type"
```

---

## Task 7: GAR — Edge Function `boleta_emitida` event

**Files:**
- Modify: `C:\Users\fanny\OneDrive\Documentos\GitHub\gar\supabase\functions\notify-nanobot\index.ts`

- [ ] **Step 1: Add `boleta_emitida` handler to Edge Function**

The Edge Function currently handles `prenda_terminada`. Add a new case for `boleta_emitida`. The function should accept a payload like:

```typescript
interface BoletaPayload {
  pedido_id: string;
  event: 'boleta_emitida';
}
```

Inside the handler, after fetching pedido and cliente (reuse existing logic):

```typescript
// Count previous pedidos for this client to determine es_primer_pedido
const { count: pedidosCount } = await supabase
  .from('pedidos')
  .select('*', { count: 'exact', head: true })
  .eq('cliente_id', cliente.cliente_id);

const esPrimerPedido = (pedidosCount || 0) <= 1;

// Build payload for boleta_emitida
const webhookPayload = {
  event: 'boleta_emitida',
  timestamp: new Date().toISOString(),
  sucursal_id: pedido.sucursal_id,
  data: {
    cliente: {
      cliente_id: cliente.cliente_id,
      nombre: cliente.nombre,
      nombre_preferido: cliente.nombre_preferido || null,
      telefono_whatsapp: phone,
      whatsapp_opt_in: cliente.whatsapp_opt_in ?? true,
    },
    pedido: {
      codigo: pedido.codigo,
      importe: pedido.importe || 0,
      fecha_entrega: pedido.fecha_entrega || null,
    },
    boleta: {
      serie: pedido.boleta_serie,
      correlativo: pedido.boleta_correlativo,
      codigo_completo: `${pedido.boleta_serie}-${pedido.boleta_correlativo}`,
      enlace_pdf: pedido.boleta_enlace_pdf,
    },
    es_primer_pedido: esPrimerPedido,
    crm_mensaje_id: crmMsg.id,
  },
};
```

- [ ] **Step 2: Deploy Edge Function**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
npx supabase functions deploy notify-nanobot --project-ref sxnfccqpjxoipptgsowu
```

- [ ] **Step 3: Test manually**

Via Supabase Dashboard or curl — call the Edge Function with a test pedido that has boleta data.

- [ ] **Step 4: Commit in GAR repo**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
git add supabase/functions/notify-nanobot/index.ts
git commit -m "feat: add boleta_emitida event to notify-nanobot Edge Function"
```

---

## Task 8: GAR — Trigger notification after boleta emission

**Files:**
- Modify: The hook/component that calls `emitir-boleta` Edge Function (likely in `useTicketActions.ts` or the pedido creation flow)

- [ ] **Step 1: Identify the trigger point**

Find where `emitir-boleta` is called and returns success. After it stores the boleta URLs in `pedidos`, call `notify-nanobot` with `event: 'boleta_emitida'`.

Follow the pattern from `useCompletarPedido.ts`: check `whatsappConfig.modo_envio === 'api'` before calling.

- [ ] **Step 2: Add the notification call**

After boleta emission succeeds and URLs are stored:

```typescript
// Notify via Nanobot if modo_envio is 'api'
if (whatsappConfig?.modo_envio === 'api') {
  try {
    await supabase.functions.invoke('notify-nanobot', {
      body: { pedido_id: pedido.codigo, event: 'boleta_emitida' },
    });
  } catch (err) {
    console.error('Error notifying nanobot:', err);
    // Non-blocking: don't fail the boleta emission
  }
}
```

- [ ] **Step 3: Test E2E**

Create a pedido in GAR → verify boleta emits → verify WhatsApp message arrives with text + PDF

- [ ] **Step 4: Commit in GAR repo**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
git add <modified-file>
git commit -m "feat: trigger WhatsApp notification after boleta emission"
```

---

## Task 9: E2E verification

- [ ] **Step 1: Start all services** (see `reference_local_startup.md` in memory)

```bash
# 1. Evolution API
docker compose -f docker-compose.evolution.yml up -d
# 2. Nanobot (wait 15s for Evolution)
PYTHONIOENCODING=utf-8 uv run nanobot gateway
# 3. Cloudflare tunnel
cloudflared tunnel --url http://localhost:18790
# 4. Update tunnel URL in Supabase (user does manually)
# 5. GAR
cd gar && npm run dev
```

- [ ] **Step 2: Test boleta_emitida webhook directly**

```python
import httpx
payload = {
    "event": "boleta_emitida",
    "timestamp": "2026-03-18T20:00:00.000Z",
    "sucursal_id": "test-sucursal",
    "data": {
        "cliente": {
            "cliente_id": "TEST-CLI-001",
            "nombre": "Cliente de Prueba",
            "nombre_preferido": "Pruebita",
            "telefono_whatsapp": "+51999999999",
            "whatsapp_opt_in": True,
        },
        "pedido": {"codigo": "TEST-002", "importe": 35.00, "fecha_entrega": "2026-03-20"},
        "boleta": {
            "serie": "B001", "correlativo": "00009999",
            "codigo_completo": "B001-00009999",
            "enlace_pdf": "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
        },
        "es_primer_pedido": True,
        "crm_mensaje_id": "TEST-BOLETA-E2E",
    },
}
resp = httpx.post("http://localhost:18790/webhook/crm", json=payload,
    headers={"Authorization": "Bearer nanobot-gar-webhook-secret-2026"})
print(resp.status_code, resp.json())
```

Expected: 202 + agent generates welcome message with `|||` + PDF sent via Evolution

- [ ] **Step 3: Verify in Nanobot logs**

Check for:
1. `CRM webhook accepted: event=boleta_emitida`
2. AgentLoop processing with `|||` split
3. `Outbound → whatsapp:` with text chunk 1
4. `Outbound → whatsapp:` with text chunk 2 (or media in same message)
5. `_send_media` log with PDF URL

- [ ] **Step 4: Test full GAR flow**

Create a real pedido in GAR (localhost:8080) → boleta emits → WhatsApp message arrives with welcome + PDF

- [ ] **Step 5: Run all Nanobot tests**

```bash
pytest tests/ --ignore=tests/test_commands.py --ignore=tests/test_consolidate_offset.py -v
```
Expected: All PASS
