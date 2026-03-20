# Templates desde DB + Ticket PDF — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate both CRM events (`prenda_terminada` and `boleta_emitida`) to DB-driven templates and attach internally-generated ticket PDFs for boleta notifications.

**Architecture:** `notify-nanobot` Edge Function reads templates from `whatsapp_templates` table instead of hardcoded strings, renders variables server-side, and invokes `generar-ticket-pdf` for base64 PDF. Nanobot receives rendered template + base64 PDF in webhook payload, writes PDF to temp file, and sends via Evolution API.

**Tech Stack:** TypeScript/Deno (Edge Functions), Python 3.11+ (Nanobot), Supabase (DB), Evolution API (WhatsApp)

**Repos:** Changes span two repos:
- **GAR:** `C:\Users\fanny\OneDrive\Documentos\GitHub\gar` (Edge Function)
- **Nanobot:** `C:\Users\fanny\OneDrive\Documentos\GitHub\nanobot` (webhook + agent loop)

**Spec:** `docs/superpowers/specs/2026-03-19-templates-y-ticket-pdf-design.md`

**Key context:**
- `generar-ticket-pdf` Edge Function expects a full `TicketRequestBody` (`pedido: PedidoData`, `sucursal: SucursalData`, `logoBase64: string`) — NOT just a `pedidoId`. Returns JSON `{ success: true, pdfBase64: "...", size: N }`.
- `whatsapp_templates` table uses `{{variable}}` (double brace) syntax. Old hardcoded templates used `{variable}` (single brace).
- Existing `whatsapp_templates.tipo` enum values: `prenda_lista`, `boleta`.

---

### Task 1: Seed templates in DB

**Files:**
- Create: `gar/supabase/migrations/20260319_seed_nanobot_templates.sql`

Insert 3 `prenda_lista` templates + 6 `boleta` templates (migrated from hardcoded, converted to `{{var}}` syntax).

- [ ] **Step 1: Write seed migration**

```sql
-- 20260319_seed_nanobot_templates.sql
-- Seed templates for automated WhatsApp notifications via Nanobot

-- prenda_lista: 3 templates with {{cliente_nombre}} and {{mensaje_pago}}
INSERT INTO whatsapp_templates (name, body, is_default, tipo)
VALUES
  ('PL1', '¡Hola {{cliente_nombre}}! 😊 Te informamos que tus prendas están listas para recoger. {{mensaje_pago}} ¡Te esperamos!', true, 'prenda_lista'),
  ('PL2', '¡Hola {{cliente_nombre}}! 👋 Tus prendas ya están listas. {{mensaje_pago}} Puedes pasar a recogerlas cuando gustes. 😊', false, 'prenda_lista'),
  ('PL3', '¡{{cliente_nombre}}! 🎉 ¡Buenas noticias! Tus prendas están listas para recoger. {{mensaje_pago}} ¡Te esperamos con gusto!', false, 'prenda_lista')
ON CONFLICT DO NOTHING;

-- boleta: 6 templates (3 bienvenida + 3 recurrente)
-- All use {{cliente_nombre}}, {{boleta_codigo}}, {{importe}}, {{mensaje_bienvenida}}
-- {{mensaje_bienvenida}} is rendered conditionally by the Edge Function
INSERT INTO whatsapp_templates (name, body, is_default, tipo)
VALUES
  ('BOL1', '¡Hola {{cliente_nombre}}! {{mensaje_bienvenida}} 😊 Nos alegra que confíes en nosotros para el cuidado de tus prendas.|||Aquí tienes tu boleta electrónica {{boleta_codigo}} por S/{{importe}}.', true, 'boleta'),
  ('BOL2', '¡Hola {{cliente_nombre}}! {{mensaje_bienvenida}} 🙌|||Te enviamos tu boleta {{boleta_codigo}} por S/{{importe}}. ¡Gracias por preferirnos!', false, 'boleta'),
  ('BOL3', '¡Hola {{cliente_nombre}}! {{mensaje_bienvenida}} 👕✨|||Aquí está tu boleta electrónica {{boleta_codigo}} — S/{{importe}}.', false, 'boleta'),
  ('BOL4', '¡Hola {{cliente_nombre}}! {{mensaje_bienvenida}} 😊|||Te enviamos tu boleta {{boleta_codigo}} por S/{{importe}}.', false, 'boleta'),
  ('BOL5', '¡Hola {{cliente_nombre}}! {{mensaje_bienvenida}} 🙌|||Aquí tienes tu boleta electrónica {{boleta_codigo}} — S/{{importe}}.', false, 'boleta'),
  ('BOL6', '¡{{cliente_nombre}}! {{mensaje_bienvenida}} ✨|||Tu boleta {{boleta_codigo}} por S/{{importe}} está lista.', false, 'boleta')
ON CONFLICT DO NOTHING;
```

Note: `|||` is the multi-message separator — Nanobot splits on this and sends as separate WhatsApp messages.

Note: `{{mensaje_bienvenida}}` unifies bienvenida/recurrente into a single template set. The Edge Function renders it as:
- Primer pedido: `"Bienvenido/a a El Chinito Veloz"`
- Recurrente: `"Gracias por seguir confiando en El Chinito Veloz"`

- [ ] **Step 2: Apply migration locally**

Apply via Supabase SQL Editor or CLI.

Verify: `SELECT name, tipo, is_default FROM whatsapp_templates WHERE tipo IN ('prenda_lista', 'boleta') ORDER BY name;` — debe retornar 9 filas.

- [ ] **Step 3: Commit**

```bash
cd C:\Users\fanny\OneDrive\Documentos\GitHub\gar
git add supabase/migrations/20260319_seed_nanobot_templates.sql
git commit -m "feat: seed prenda_lista and boleta templates for Nanobot notifications"
```

---

### Task 2: Refactor `notify-nanobot` — templates from DB + PDF generation

**Files:**
- Modify: `gar/supabase/functions/notify-nanobot/index.ts` (complete refactor)

Three changes:
1. Read templates from `whatsapp_templates` table (both event types)
2. Render variables and rotation server-side
3. Call `generar-ticket-pdf` for `boleta_emitida` and send base64

Also fixes bug at line 316: `crmMsgId = crmMsgId` → `crmMsgId = crmMsg!.id`.

- [ ] **Step 1: Replace hardcoded templates with DB helper function**

Delete the hardcoded template constants and `renderTemplate` function (lines 23-47). Replace with:

```typescript
interface TemplateResult {
  rendered: string;
  nextIndex: number;
}

async function fetchAndRenderTemplate(
  supabase: any,
  tipo: 'prenda_lista' | 'boleta',
  clienteIndex: number,
  vars: Record<string, string>
): Promise<TemplateResult | null> {
  const { data: templates, error } = await supabase
    .from('whatsapp_templates')
    .select('body')
    .eq('tipo', tipo)
    .order('name');

  if (error || !templates || templates.length === 0) {
    console.warn(`⚠️ No templates found for tipo=${tipo}`);
    return null;  // Caller falls back to sending without template_sugerido (LLM)
  }

  const idx = clienteIndex % templates.length;
  let rendered = templates[idx].body;
  for (const [key, value] of Object.entries(vars)) {
    rendered = rendered.replaceAll(`{{${key}}}`, value);
  }
  return { rendered, nextIndex: idx + 1 };
}
```

- [ ] **Step 2: Add helper function to generate ticket PDF as base64**

`generar-ticket-pdf` expects a full `TicketRequestBody` (pedido, sucursal, logoBase64) and returns JSON `{ pdfBase64: "..." }`. The helper must assemble the request body from data `notify-nanobot` already has.

Add after `fetchAndRenderTemplate`:

```typescript
async function generateTicketPdfBase64(
  supabase: any,
  pedido: any,
  prendas: any[],
  sucursalId: string
): Promise<{ base64: string; filename: string } | null> {
  const supabaseUrl = Deno.env.get('SUPABASE_URL') ?? '';
  const serviceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? '';

  try {
    // Get sucursal name
    const { data: sucursal } = await supabase
      .from('sucursales')
      .select('nombre')
      .eq('id', sucursalId)
      .single();

    // Get logo from config_empresa or use empty string
    const { data: configEmpresa } = await supabase
      .from('config_empresa')
      .select('logo_base64')
      .single();

    const logoBase64 = configEmpresa?.logo_base64 || '';

    // Assemble PedidoData for generar-ticket-pdf
    const importe = pedido.importe || 0;
    const pedidoData = {
      codigo: pedido.codigo,
      fecha: pedido.created_at || new Date().toISOString(),
      cliente: pedido.cliente_nombre || '',
      prendas: (prendas || []).map((p: any) => ({
        tipoServicio: p.servicio,
        cantidad: p.cantidad,
        precioUnitario: 0,
        subtotal: 0,
      })),
      subtotal: importe,
      total: importe,
      pagoEfectivo: pedido.pago_efectivo || 0,
      pagoYape: pedido.pago_yape || 0,
      pagoCredito: pedido.pago_credito || 0,
      saldoPendiente: importe - (pedido.pago_efectivo || 0) - (pedido.pago_yape || 0) - (pedido.pago_credito || 0),
      boleta_serie: pedido.boleta_serie,
      boleta_correlativo: pedido.boleta_correlativo,
      fechaEntrega: pedido.fecha_entrega,
      observaciones: pedido.observaciones,
    };

    const resp = await fetch(
      `${supabaseUrl}/functions/v1/generar-ticket-pdf`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${serviceKey}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          pedido: pedidoData,
          sucursal: { nombre: sucursal?.nombre || 'Sucursal' },
          logoBase64,
        }),
      }
    );

    if (!resp.ok) {
      console.error(`❌ generar-ticket-pdf error ${resp.status}: ${await resp.text()}`);
      return null;
    }

    // generar-ticket-pdf returns JSON { success, pdfBase64, size }
    const json = await resp.json();
    if (!json.pdfBase64) {
      console.error('❌ generar-ticket-pdf returned no pdfBase64');
      return null;
    }

    const filename = pedido.boleta_serie
      ? `Boleta-${pedido.boleta_serie}-${pedido.boleta_correlativo}.pdf`
      : `Ticket-${pedido.codigo}.pdf`;

    console.log(`📄 PDF generado: ${filename} (${json.size} bytes)`);
    return { base64: json.pdfBase64, filename };
  } catch (err) {
    console.error('❌ Error generating ticket PDF:', err);
    return null;  // Non-fatal: message sends without PDF
  }
}
```

**Important notes:**
- `generar-ticket-pdf` returns `{ pdfBase64: "..." }` already base64-encoded — no manual conversion needed.
- `config_empresa.logo_base64` stores the company logo. If the column doesn't exist, the function needs `logoBase64` passed as empty string and `generar-ticket-pdf` handles missing logo gracefully (or `empresa` fallback from DB kicks in).
- `prendas` pricing data (`precioUnitario`, `subtotal`) is set to 0 because notify-nanobot queries prendas with only `servicio, cantidad`. The PDF function will still render the ticket — pricing comes from `pedido.importe` total. If detailed pricing per prenda is needed, expand the prendas query.

- [ ] **Step 3: Refactor `boleta_emitida` handler to use DB templates + PDF**

Replace the boleta_emitida block (lines 133-263). Key changes:

1. **Guard condition** — change from `!pedido.boleta_enlace_pdf || !pedido.boleta_serie` to just `!pedido.boleta_serie` (PDF is now generated internally, not from SUNAT URL):
   ```typescript
   if (!pedido.boleta_serie) {
     console.warn('⚠️ Pedido sin boleta emitida, omitiendo');
     // ... return skipped
   }
   ```

2. **Template from DB** — replace hardcoded template selection:
   ```typescript
   const esPrimerPedido = (pedidosCount || 0) <= 1;
   const templateResult = await fetchAndRenderTemplate(
     supabase, 'boleta',
     cliente.ultimo_indice_plantilla || 0,
     {
       cliente_nombre: cliente.nombre_preferido || cliente.nombre,
       boleta_codigo: `${pedido.boleta_serie}-${pedido.boleta_correlativo}`,
       importe: (pedido.importe || 0).toFixed(2),
       mensaje_bienvenida: esPrimerPedido
         ? 'Bienvenido/a a El Chinito Veloz'
         : 'Gracias por seguir confiando en El Chinito Veloz',
     }
   );
   ```

3. **Rotation update** (same as existing code):
   ```typescript
   if (templateResult) {
     await supabase
       .from('clientes')
       .update({ ultimo_indice_plantilla: templateResult.nextIndex })
       .eq('cliente_id', cliente.cliente_id);
   }
   ```

4. **Generate PDF**:
   ```typescript
   const pdfResult = await generateTicketPdfBase64(
     supabase, pedido, prendas || [], pedido.sucursal_id
   );
   ```

5. **Payload** — replace `enlace_pdf` with `pdf_base64`:
   ```typescript
   boleta: {
     serie: pedido.boleta_serie,
     correlativo: pedido.boleta_correlativo,
     codigo_completo: `${pedido.boleta_serie}-${pedido.boleta_correlativo}`,
     pdf_base64: pdfResult?.base64 || null,
     pdf_filename: pdfResult?.filename || `Boleta-${pedido.codigo}.pdf`,
   },
   // template_sugerido only if templateResult exists:
   ...(templateResult ? {
     template_sugerido: { contenido_renderizado: templateResult.rendered },
   } : {}),
   ```

6. **crm_mensajes**: store `templateResult?.rendered || ''` in `mensaje_renderizado`.

- [ ] **Step 4: Refactor `prenda_terminada` handler to use DB templates**

Replace the prenda_terminada section (lines 264-385). Key changes:

1. **Calculate `mensaje_pago` and fetch template** (after saldo calculation at line 131):
   ```typescript
   const mensajePago = saldo > 0
     ? `Recuerda que tienes un saldo pendiente de S/${saldo.toFixed(2)}.`
     : '¡Ya está todo pagado!';

   const templateResult = await fetchAndRenderTemplate(
     supabase, 'prenda_lista',
     cliente.ultimo_indice_plantilla || 0,
     {
       cliente_nombre: cliente.nombre_preferido || cliente.nombre,
       mensaje_pago: mensajePago,
     }
   );

   // Update rotation index
   if (templateResult) {
     await supabase
       .from('clientes')
       .update({ ultimo_indice_plantilla: templateResult.nextIndex })
       .eq('cliente_id', cliente.cliente_id);
   }
   ```

2. **crm_mensajes**: store `templateResult?.rendered || ''` in `mensaje_renderizado`.

3. **Payload**: include `template_sugerido` only if `templateResult` exists:
   ```typescript
   // Add to webhookPayload.data:
   ...(templateResult ? {
     template_sugerido: { contenido_renderizado: templateResult.rendered },
   } : {}),
   ```

4. **Fix bug line 316**: `crmMsgId = crmMsgId` → `crmMsgId = crmMsg!.id`

- [ ] **Step 5: Test Edge Function locally**

Run: `cd C:\Users\fanny\OneDrive\Documentos\GitHub\gar && npx supabase functions serve notify-nanobot --no-verify-jwt`

Test prenda_terminada with a real pedido code that exists in the DB:
```bash
curl -X POST http://localhost:54321/functions/v1/notify-nanobot \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <anon-key>" \
  -d '{"pedido_id":"<real-pedido-code>","event":"prenda_terminada"}'
```

Expected log output:
- `⚠️ No templates found for tipo=prenda_lista` (if seeds not applied yet) OR
- Template rendered with values replaced
- No `{variable}` or `{{variable}}` remaining in output

Test boleta_emitida:
```bash
curl -X POST http://localhost:54321/functions/v1/notify-nanobot \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <anon-key>" \
  -d '{"pedido_id":"<real-pedido-code-with-boleta>","event":"boleta_emitida"}'
```

Expected: template rendered + `📄 PDF generado: Boleta-... (N bytes)` in logs.

- [ ] **Step 6: Commit**

```bash
cd C:\Users\fanny\OneDrive\Documentos\GitHub\gar
git add supabase/functions/notify-nanobot/index.ts
git commit -m "feat: notify-nanobot reads templates from DB + generates ticket PDF as base64

- Replace hardcoded templates with whatsapp_templates table lookup
- Both prenda_terminada and boleta_emitida use DB templates with rotation
- Invoke generar-ticket-pdf for base64 PDF (replaces enlace_pdf URL)
- Fallback to no-template (LLM) when DB has no templates
- Fix bug: crmMsgId self-assignment on line 316"
```

---

### Task 3: Update Nanobot tests for new payload shape

**Files:**
- Modify: `nanobot/tests/test_crm_webhook.py:73-103,289-314`

`routes.py` doesn't need code changes — it already passes `data.get("boleta", {})` to metadata (line 225). The new fields (`pdf_base64`, `pdf_filename`) flow through automatically. Only tests need updating to reflect the new payload.

- [ ] **Step 1: Update test fixture `_make_boleta_payload`**

In `tests/test_crm_webhook.py`, replace `_make_boleta_payload` (lines 73-103):

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
                "pdf_base64": "JVBER...",
                "pdf_filename": "Boleta-B001-00001234.pdf",
            },
            "es_primer_pedido": es_primer_pedido,
            "crm_mensaje_id": crm_id,
            "template_sugerido": {
                "contenido_renderizado": "¡Hola Marita! Bienvenida a El Chinito Veloz\n|||\nAquí tienes tu boleta B001-00001234"
            },
        },
    }
```

- [ ] **Step 2: Update boleta metadata test**

Replace `test_boleta_forwards_metadata` (lines 289-301):

```python
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
    assert msg.metadata["boleta"]["pdf_base64"] == "JVBER..."
    assert msg.metadata["boleta"]["pdf_filename"] == "Boleta-B001-00001234.pdf"
    assert msg.metadata["es_primer_pedido"] is True
```

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\fanny\OneDrive\Documentos\GitHub\nanobot && pytest tests/test_crm_webhook.py -v`

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
cd C:\Users\fanny\OneDrive\Documentos\GitHub\nanobot
git add tests/test_crm_webhook.py
git commit -m "test: update boleta fixtures to use pdf_base64 instead of enlace_pdf"
```

---

### Task 4: Update `loop.py` — base64 PDF to temp file

**Files:**
- Modify: `nanobot/nanobot/agent/loop.py:1-10` (imports)
- Modify: `nanobot/nanobot/agent/loop.py:210-212` (media logic)

Replace the `enlace_pdf` URL lookup with base64 decode to temp file.

- [ ] **Step 1: Add imports at top of loop.py**

Add `import base64` and `import tempfile` to the imports section (only if not already present).

- [ ] **Step 2: Replace media collection logic**

Replace lines 210-212 in `loop.py`:

Old:
```python
                    # Collect media from metadata (e.g., boleta PDF URL)
                    boleta_pdf = (msg.metadata.get("boleta") or {}).get("enlace_pdf", "")
                    media_list = [boleta_pdf] if boleta_pdf else []
```

New:
```python
                    # Collect media from metadata (e.g., boleta PDF as base64)
                    boleta = msg.metadata.get("boleta") or {}
                    pdf_b64 = boleta.get("pdf_base64")
                    media_list: list[str] = []
                    if pdf_b64:
                        try:
                            pdf_bytes = base64.b64decode(pdf_b64)
                            pdf_name = boleta.get("pdf_filename", "boleta.pdf")
                            tmp = tempfile.NamedTemporaryFile(
                                delete=False,
                                suffix=".pdf",
                                prefix=pdf_name.replace(".pdf", "_"),
                            )
                            tmp.write(pdf_bytes)
                            tmp.close()
                            media_list = [tmp.name]
                        except Exception as e:
                            logger.error("Failed to decode PDF base64: {}", e)
```

`_send_media()` in `evolution.py` already handles local file paths: reads file, base64-encodes, detects MIME, sends to Evolution API. No changes needed in `evolution.py`.

- [ ] **Step 3: Run tests**

Run: `cd C:\Users\fanny\OneDrive\Documentos\GitHub\nanobot && pytest tests/ -v`

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
cd C:\Users\fanny\OneDrive\Documentos\GitHub\nanobot
git add nanobot/agent/loop.py
git commit -m "feat: decode base64 PDF from CRM payload to temp file for WhatsApp delivery"
```

---

### Task 5: Update integration contract

**Files:**
- Modify: `nanobot/docs/contracts/nanobot-gar-integration.md`

- [ ] **Step 1: Update boleta payload section**

Find the `boleta_emitida` payload section. Replace `enlace_pdf`, `enlace_xml`, `enlace_cdr` with:

```jsonc
"boleta": {
  "serie": "B001",
  "correlativo": "00001234",
  "codigo_completo": "B001-00001234",
  "pdf_base64": "<base64 encoded PDF or null if generation failed>",
  "pdf_filename": "Boleta-B001-00001234.pdf"
}
```

- [ ] **Step 2: Add note about template source change**

Add a section noting:
- Both `prenda_terminada` and `boleta_emitida` now read templates from `whatsapp_templates` table
- `template_sugerido.contenido_renderizado` is included when templates exist in DB
- Fallback: if no templates in DB, payload omits `template_sugerido` and Nanobot uses LLM
- Template rotation via `cliente.ultimo_indice_plantilla` (shared counter across event types)

- [ ] **Step 3: Commit**

```bash
cd C:\Users\fanny\OneDrive\Documentos\GitHub\nanobot
git add -f docs/contracts/nanobot-gar-integration.md
git commit -m "docs: update integration contract — pdf_base64 replaces enlace_pdf, both events use DB templates"
```

---

### Task 6: E2E verification

**No files to modify — manual testing.**

- [ ] **Step 1: Start local services**

1. Evolution API: `docker compose -f docker-compose.evolution.yml up -d`
2. Nanobot: `uv run nanobot gateway`
3. Cloudflare tunnel (for webhook URL)
4. GAR dev: `cd gar && npm run dev`
5. Supabase local or remote with seeds applied

- [ ] **Step 2: Update Edge Function secrets**

Update `NANOBOT_WEBHOOK_URL` in Supabase Edge Function secrets to the current Cloudflare tunnel URL.

- [ ] **Step 3: Test prenda_terminada flow**

In GAR, mark all prendas of a test pedido as "terminado". Verify:
- [ ] `notify-nanobot` logs: template fetched from DB, variables rendered
- [ ] Nanobot logs: template bypass (no LLM call)
- [ ] WhatsApp: receives templated message with nombre and saldo/pagado
- [ ] DB: `crm_mensajes.mensaje_renderizado` has the rendered text, `estado_envio = 'enviado_api'`

- [ ] **Step 4: Test boleta_emitida flow**

In GAR, create a test pedido and emit boleta. Verify:
- [ ] `notify-nanobot` logs: PDF generated via `generar-ticket-pdf`, template from DB
- [ ] Nanobot logs: template bypass + PDF temp file created
- [ ] WhatsApp: receives text message(s) + PDF document attachment
- [ ] DB: `crm_mensajes.estado_envio = 'enviado_api'`

- [ ] **Step 5: Test fallback (no templates in DB)**

Temporarily delete all `prenda_lista` templates. Trigger `prenda_terminada`. Verify:
- [ ] Nanobot receives event without `template_sugerido`
- [ ] LLM generates message (fallback works)
- [ ] Re-insert templates after test
