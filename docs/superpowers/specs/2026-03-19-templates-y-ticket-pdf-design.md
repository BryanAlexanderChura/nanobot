# Templates desde DB + Ticket PDF siempre disponible — Design Spec

> Versión: 1.0 | Fecha: 2026-03-19 | Estado: Propuesta

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

---

## Cambio 1: Templates desde la tabla `whatsapp_templates`

### Para `prenda_terminada` (tipo: `prenda_lista`)

**Variables disponibles:**
- `{{cliente_nombre}}` — nombre del cliente
- `{{saldo_pendiente}}` — monto pendiente (ej: "S/20.00")
- `{{mensaje_pago}}` — frase condicional de pago

**Lógica de renderizado:**
- Si `saldo > 0`: usar `{{mensaje_pago}}` = "Recuerda que tienes un saldo pendiente de S/{saldo}."
- Si `saldo == 0`: usar `{{mensaje_pago}}` = "¡Ya está todo pagado!"

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
1. `notify-nanobot` consulta `whatsapp_templates` WHERE `tipo = 'prenda_lista'` AND `sucursal_id` matches (o null)
2. Rota usando `ultimo_indice_plantilla` del cliente
3. Renderiza variables (`{{cliente_nombre}}`, `{{mensaje_pago}}`)
4. Envía como `template_sugerido.contenido_renderizado` → Nanobot bypassa LLM

### Para `boleta_emitida` (tipo: `boleta`)

Mismo cambio: migrar templates hardcoded actuales a la tabla `whatsapp_templates` con `tipo = 'boleta'`.

Los templates de bienvenida vs recurrente se distinguen con la variable `{{mensaje_bienvenida}}` que se renderiza condicionalmente según `es_primer_pedido`.

**Flujo:** Idéntico al de `prenda_lista` — consulta DB, rota, renderiza, envía.

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
const pdfBuffer = await pdfResponse.arrayBuffer();
const pdfBase64 = btoa(String.fromCharCode(...new Uint8Array(pdfBuffer)));
```

### Payload a Nanobot

Reemplazar `boleta.enlace_pdf` por:

```jsonc
{
  "boleta": {
    // enlace_pdf ya no se usa
    "pdf_base64": "<base64 string>",
    "pdf_filename": "Boleta-B001-00123.pdf"
  }
}
```

### Cambios en Nanobot

**`webhook/routes.py`:**
- Leer `boleta.pdf_base64` y `boleta.pdf_filename` del payload.
- Pasar en metadata del `InboundMessage`.

**`agent/loop.py`:**
- En el template bypass, leer `pdf_base64` de metadata en vez de `enlace_pdf`.
- Escribir base64 a archivo temporal, pasarlo como media al `OutboundMessage`.

**`channels/evolution.py`:**
- `_send_media()` ya soporta base64 para archivos locales — se reutiliza sin cambios, o se acepta base64 directo en el campo `media` del payload de Evolution API.

---

## Resumen de cambios por archivo

### GAR (repo gar)

| Archivo | Cambio |
|---------|--------|
| `supabase/functions/notify-nanobot/index.ts` | Leer templates de DB, invocar generar-ticket-pdf, enviar base64 en payload |
| `whatsapp_templates` (tabla) | Insertar templates iniciales de `prenda_lista` (3) + migrar existentes de `boleta` |

### Nanobot (repo nanobot)

| Archivo | Cambio |
|---------|--------|
| `nanobot/webhook/routes.py` | Leer `pdf_base64`/`pdf_filename` del payload |
| `nanobot/agent/loop.py` | Usar base64 de metadata en vez de `enlace_pdf` |

### Sin cambios necesarios
- `evolution.py` — ya soporta base64
- UI de templates en GAR — ya existe, se usa tal cual
- Template bypass en loop.py — ya funciona

---

## Datos de prueba

Para verificar E2E local:
- Crear templates en tabla `whatsapp_templates` vía UI de GAR o SQL directo
- Crear pedido de prueba con prendas terminadas
- Verificar que `notify-nanobot` lee template de DB, renderiza, y envía con PDF adjunto

---

## Fuera de alcance

- Cambios en la UI de templates de GAR (ya funciona)
- Nuevos tipos de templates (futuras fases)
- Deploy a producción (todo local por ahora)
