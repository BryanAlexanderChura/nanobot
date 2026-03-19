# Templates desde DB + Ticket PDF siempre disponible — Design Spec

> Versión: 1.1 | Fecha: 2026-03-19 | Estado: Propuesta

---

## Objetivo

Dos mejoras en la Edge Function `notify-nanobot`:

1. **Ticket PDF siempre disponible** — `boleta_emitida` adjunta un PDF generado internamente via `generar-ticket-pdf` (base64), eliminando la dependencia de `enlace_pdf` de SUNAT/Rapifac.
2. **prenda_terminada con templates** — Migrar de LLM a templates, eliminando consumo de tokens. Mensajes simples y amigables con rotación.

Ambos eventos pasan a leer templates desde la tabla `whatsapp_templates` existente en GAR, reemplazando los templates hardcoded actuales de `boleta_emitida`.

## Contexto

### Sistema de templates existente en GAR

GAR ya cuenta con:

- **Tabla `whatsapp_templates`** — campos: `id`, `name`, `body`, `is_default`, `tipo` (enum: `prenda_lista` | `boleta`), `sucursal_id`, timestamps.
- **Variables con sintaxis `{{variable}}`** — renderizadas por `useWhatsAppTemplates.renderTemplate()`.
- **UI de gestión** — página WhatsAppTemplates con CRUD, preview en vivo, badges de variables, emojis.
- **Rotación** — `cliente.ultimo_indice_plantilla` ya existe para rotar entre templates.

### Estado actual de notify-nanobot

- `boleta_emitida`: templates hardcoded en TypeScript (3 bienvenida + 3 recurrente), envía `template_sugerido` → Nanobot bypassa LLM.
- `prenda_terminada`: envía contexto completo a Nanobot → LLM genera mensaje (cuesta tokens).
- PDF de boleta: usa `pedidos.boleta_enlace_pdf` (URL de Rapifac/SUNAT), frecuentemente `null`.
- `generar-ticket-pdf`: Edge Function ya existente que genera PDFs de ticket/boleta. Recibe `pedidoId`, retorna PDF binario.

---

## Cambio 1: Templates desde la tabla `whatsapp_templates`

### Rotación de templates

`notify-nanobot` maneja la rotación para ambos tipos de evento:

1. Consulta templates activos: `SELECT * FROM whatsapp_templates WHERE tipo = :tipo ORDER BY name`
2. Lee `cliente.ultimo_indice_plantilla` (integer)
3. Selecciona: `templates[ultimo_indice % templates.length]`
4. Incrementa: `UPDATE clientes SET ultimo_indice_plantilla = ultimo_indice + 1 WHERE id = :clienteId`

Ambos eventos (`prenda_lista` y `boleta`) comparten el mismo contador de rotación. Esto es aceptable — el objetivo es variar mensajes, no sincronizar por tipo.

### Para `prenda_terminada` (tipo: `prenda_lista`)

**Variables disponibles:**
- `{{cliente_nombre}}` — nombre del cliente
- `{{mensaje_pago}}` — frase condicional de pago (renderizada por la Edge Function)

**Lógica de renderizado en `notify-nanobot`:**
- Si `saldo > 0`: `{{mensaje_pago}}` = `"Recuerda que tienes un saldo pendiente de S/{saldo}."`
- Si `saldo == 0`: `{{mensaje_pago}}` = `"¡Ya está todo pagado!"`

**Templates iniciales sugeridos (tipo `prenda_lista`):**

Template 1:
```
¡Hola {{cliente_nombre}}! 😊 Te informamos que tus prendas están listas para recoger. {{mensaje_pago}} ¡Te esperamos!
```

Template 2:
```
¡Hola {{cliente_nombre}}! 👋 Tus prendas ya están listas. {{mensaje_pago}} Puedes pasar a recogerlas cuando gustes. 😊
```

Template 3:
```
¡{{cliente_nombre}}! 🎉 ¡Buenas noticias! Tus prendas están listas para recoger. {{mensaje_pago}} ¡Te esperamos con gusto!
```

**Flujo:**
1. `notify-nanobot` consulta `whatsapp_templates` WHERE `tipo = 'prenda_lista'`
2. Rota usando `ultimo_indice_plantilla` del cliente (ver sección Rotación arriba)
3. Calcula `mensaje_pago` según saldo
4. Renderiza variables (`{{cliente_nombre}}`, `{{mensaje_pago}}`)
5. Crea `crm_mensajes` con mensaje renderizado
6. Envía como `template_sugerido.contenido_renderizado` → Nanobot bypassa LLM

### Para `boleta_emitida` (tipo: `boleta`)

Mismo cambio: migrar templates hardcoded actuales a la tabla `whatsapp_templates` con `tipo = 'boleta'`.

Los templates de bienvenida vs recurrente se distinguen con la variable `{{mensaje_bienvenida}}` que la Edge Function renderiza condicionalmente:
- Si `es_primer_pedido`: `{{mensaje_bienvenida}}` = `"¡Bienvenido/a a El Chinito Veloz!"`
- Si no: `{{mensaje_bienvenida}}` = `"¡Gracias por confiar en nosotros nuevamente!"`

**Flujo:** Idéntico al de `prenda_lista` — consulta DB, rota, renderiza, envía.

### Fallback si no hay templates en DB

Si la consulta a `whatsapp_templates` retorna 0 templates para el tipo, `notify-nanobot`:
1. Envía el payload **sin** `template_sugerido` (igual que antes)
2. Nanobot recibe el evento y lo procesa con LLM como fallback
3. Esto mantiene compatibilidad — si alguien borra todos los templates, el sistema no se rompe

---

## Cambio 2: Ticket PDF via `generar-ticket-pdf`

### Flujo actual (a reemplazar)
```
boleta_emitida → enlace_pdf (URL SUNAT, frecuentemente null) → Nanobot → WhatsApp
```

### Flujo nuevo
```
boleta_emitida → notify-nanobot invoca generar-ticket-pdf → recibe base64 → payload a Nanobot → WhatsApp
```

### Implementación en `notify-nanobot`

Después de obtener datos del pedido, invocar `generar-ticket-pdf` internamente:

```typescript
// Llamar a generar-ticket-pdf via Supabase Functions invoke
const pdfResponse = await fetch(
  `${SUPABASE_URL}/functions/v1/generar-ticket-pdf`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${SUPABASE_SERVICE_KEY}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ pedidoId: pedido.id })
  }
);

let pdfBase64: string | null = null;
let pdfFilename = `Boleta-${pedido.codigo}.pdf`;

if (pdfResponse.ok) {
  const pdfBuffer = await pdfResponse.arrayBuffer();
  // Usar encode chunkeado — btoa(...spread) crashea con buffers >100KB
  const bytes = new Uint8Array(pdfBuffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  pdfBase64 = btoa(binary);
} else {
  // PDF no disponible — enviar mensaje sin adjunto
  console.error('generar-ticket-pdf failed:', pdfResponse.status);
}
```

### Error handling para PDF

Si `generar-ticket-pdf` falla o no responde:
- `pdfBase64` queda `null`
- El mensaje de texto se envía normalmente (sin PDF adjunto)
- No se marca como fallido — el mensaje de texto sí llegó

### Payload a Nanobot

Reemplazar `boleta.enlace_pdf` por:

```jsonc
{
  "boleta": {
    "pdf_base64": "<base64 string o null>",
    "pdf_filename": "Boleta-PED-001.pdf"
    // enlace_pdf, enlace_xml, enlace_cdr ya no se envían
  }
}
```

### Cambios en Nanobot

**`webhook/routes.py`:**
- Leer `boleta.pdf_base64` y `boleta.pdf_filename` del payload
- Pasar en metadata del `InboundMessage`

**`agent/loop.py` — flujo base64 a archivo temporal:**

```python
import tempfile, base64, os

boleta = msg.metadata.get("boleta") or {}
pdf_b64 = boleta.get("pdf_base64")
pdf_filename = boleta.get("pdf_filename", "boleta.pdf")

if pdf_b64:
    # Decodificar base64 a archivo temporal
    pdf_bytes = base64.b64decode(pdf_b64)
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".pdf", prefix=pdf_filename.replace(".pdf", "_")
    )
    tmp.write(pdf_bytes)
    tmp.close()
    # tmp.name va a OutboundMessage.media (list[str])
    # _send_media() ya lee archivos locales y los envía como base64 a Evolution
    media = [tmp.name]
    # Cleanup: eliminar después de enviar (en _dispatch_outbound o con atexit)
```

- `_send_media()` ya soporta archivos locales → lee el `.pdf`, detecta MIME `application/pdf`, envía como base64 a Evolution API
- El archivo temporal se limpia después del envío

**`channels/evolution.py`:**
- Sin cambios — `_send_media()` ya maneja archivos locales con base64

---

## Resumen de cambios por archivo

### GAR (repo gar)

| Archivo | Cambio |
|---------|--------|
| `supabase/functions/notify-nanobot/index.ts` | Leer templates de DB, invocar generar-ticket-pdf, enviar base64 en payload, eliminar templates hardcoded |
| `whatsapp_templates` (tabla) | Insertar templates iniciales de `prenda_lista` (3) + migrar templates de `boleta` (6: 3 bienvenida + 3 recurrente) |

### Nanobot (repo nanobot)

| Archivo | Cambio |
|---------|--------|
| `nanobot/webhook/routes.py` | Leer `pdf_base64`/`pdf_filename` del payload, pasar en metadata |
| `nanobot/agent/loop.py` | Decodificar base64 a temp file, pasar como media en OutboundMessage |
| `docs/contracts/nanobot-gar-integration.md` | Actualizar payload de `boleta_emitida` (pdf_base64 en vez de enlace_pdf) |

### Sin cambios necesarios
- `evolution.py` — ya soporta archivos locales con base64
- UI de templates en GAR — ya existe, se usa tal cual
- Template bypass en loop.py — ya funciona para `template_sugerido`

---

## SQL seed: templates iniciales

```sql
-- prenda_lista templates
INSERT INTO whatsapp_templates (name, body, is_default, tipo) VALUES
('PL1', '¡Hola {{cliente_nombre}}! 😊 Te informamos que tus prendas están listas para recoger. {{mensaje_pago}} ¡Te esperamos!', true, 'prenda_lista'),
('PL2', '¡Hola {{cliente_nombre}}! 👋 Tus prendas ya están listas. {{mensaje_pago}} Puedes pasar a recogerlas cuando gustes. 😊', false, 'prenda_lista'),
('PL3', '¡{{cliente_nombre}}! 🎉 ¡Buenas noticias! Tus prendas están listas para recoger. {{mensaje_pago}} ¡Te esperamos con gusto!', false, 'prenda_lista');

-- boleta templates: migrar desde los hardcoded actuales en notify-nanobot
-- (los 6 templates existentes se copian tal cual a la tabla)
```

---

## Datos de prueba

Para verificar E2E local:

1. Insertar templates en `whatsapp_templates` via SQL seed o UI de GAR
2. Verificar con curl simulando `prenda_terminada`:
```bash
curl -X POST http://localhost:8787/webhook/crm \
  -H "Authorization: Bearer nanobot-gar-webhook-secret-2026" \
  -H "Content-Type: application/json" \
  -d '{"event":"prenda_terminada","crm_mensaje_id":"test-001","cliente":{"nombre":"Fanny","telefono":"+51928456493"},"pedido":{"codigo":"TEST-001","total":50,"saldo_pendiente":20}}'
```
3. Verificar que llega un mensaje con template (no LLM) a WhatsApp
4. Verificar `boleta_emitida` con PDF adjunto:
```bash
curl -X POST http://localhost:8787/webhook/crm \
  -H "Authorization: Bearer nanobot-gar-webhook-secret-2026" \
  -H "Content-Type: application/json" \
  -d '{"event":"boleta_emitida","crm_mensaje_id":"test-002","cliente":{"nombre":"Fanny","telefono":"+51928456493"},"pedido":{"codigo":"TEST-001"},"boleta":{"pdf_base64":"JVBER...","pdf_filename":"Boleta-TEST-001.pdf"}}'
```
5. Verificar que llega mensaje + PDF a WhatsApp

---

## Fuera de alcance

- Cambios en la UI de templates de GAR (ya funciona)
- Nuevos tipos de templates (futuras fases)
- Deploy a producción (todo local por ahora)
- Variables de prendas individuales en templates de `prenda_terminada` (se mantiene simple por ahora)
