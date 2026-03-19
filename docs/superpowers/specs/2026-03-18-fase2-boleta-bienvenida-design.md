# Fase 2: Boleta + Bienvenida — Design Spec

> Versión: 1.0 | Fecha: 2026-03-18 | Estado: Propuesta

---

## Objetivo

Cuando un operario crea un pedido en GAR y la boleta se emite automáticamente vía Rapifac, el cliente recibe por WhatsApp:

1. Un mensaje de bienvenida (cliente nuevo) o agradecimiento (cliente recurrente)
2. Un segundo mensaje breve: "Aquí tienes tu boleta"
3. El PDF de la boleta como documento adjunto

## Contexto de negocio

- La boleta se emite automáticamente al crear el pedido (configuración por defecto activa en GAR)
- Rapifac devuelve las URLs del PDF, XML y CDR que se guardan en `pedidos.boleta_enlace_pdf`
- El operario ya imprime el ticket físico; el WhatsApp es el complemento digital
- GAR sabe si es el primer pedido del cliente (puede contar pedidos previos)

## Evento: `boleta_emitida`

### Trigger

GAR llama a la Edge Function `notify-nanobot` después de que Rapifac retorna éxito y las URLs se guardan en `pedidos`.

### Payload

```jsonc
{
  "event": "boleta_emitida",
  "timestamp": "2026-03-18T14:30:00.000Z",
  "sucursal_id": "uuid-sucursal",
  "data": {
    "cliente": {
      "cliente_id": "C-000123",
      "nombre": "María López García",
      "nombre_preferido": "Marita",
      "telefono_whatsapp": "+51987654321",
      "whatsapp_opt_in": true
    },
    "pedido": {
      "codigo": "B001-4",
      "importe": 45.00,
      "fecha_entrega": "2026-03-20"
    },
    "boleta": {
      "serie": "B001",
      "correlativo": "00001234",
      "codigo_completo": "B001-00001234",
      "enlace_pdf": "https://rapifac.com/boletas/xxx.pdf"
    },
    "es_primer_pedido": true,
    "crm_mensaje_id": "uuid-mensaje"
  }
}
```

Campos clave vs `prenda_terminada`:
- `data.boleta` — datos de la boleta + URL del PDF
- `data.es_primer_pedido` — flag que GAR calcula (contando pedidos previos del cliente)
- `data.pedido` — simplificado (no necesita campos de pago aquí)

---

## Flujo técnico

```
Operario crea pedido en GAR
  → Rapifac emite boleta (automático)
  → URLs guardadas en pedidos.boleta_enlace_pdf
  → GAR llama Edge Function notify-nanobot (evento: boleta_emitida)
  → Edge Function cuenta pedidos previos del cliente → es_primer_pedido
  → Edge Function crea/reutiliza crm_mensajes (pendiente)
  → POST /webhook/crm con payload boleta_emitida
  → Nanobot recibe, dedup check, publica en MessageBus
  → AgentLoop genera respuesta con |||
  → Chunk 1 (texto bienvenida/agradecimiento) → sendText
  → Chunk 2 (texto "aquí tu boleta") → sendText
  → PDF adjunto → sendMedia
  → ChannelManager actualiza crm_mensajes (enviado_api/fallido)
```

---

## Comportamiento del agente

El prompt para `boleta_emitida` debe instruir al agente a:

1. **Cliente nuevo** (`es_primer_pedido: true`):
   - Primer bloque: bienvenida cálida, presentarse como El Chinito Veloz
   - Segundo bloque: "Aquí tienes tu boleta electrónica"
   - Usar `|||` para separar

2. **Cliente recurrente** (`es_primer_pedido: false`):
   - Primer bloque: agradecimiento por seguir confiando, mencionar nombre
   - Segundo bloque: "Aquí tienes tu boleta electrónica"
   - Usar `|||` para separar

Ejemplo de respuesta del agente (cliente nuevo):
```
¡Hola Marita! Bienvenida a El Chinito Veloz 😊 Nos alegra que confíes en nosotros para el cuidado de tus prendas.
|||
Aquí tienes tu boleta electrónica B001-00001234 del pedido B001-4 por S/45.00
```

Ejemplo (cliente recurrente):
```
¡Hola Marita! Gracias por seguir confiando en El Chinito Veloz 🙌
|||
Te enviamos tu boleta electrónica B001-00001234 del pedido B001-4 por S/45.00
```

El PDF se adjunta automáticamente después del texto (no es generado por el agente).

---

## Separación de mensajes con `|||` (cambio global)

El agente lavanderia ya tiene la instrucción de usar `|||` en su SOUL.md, pero esto debe reforzarse como comportamiento global:

- La instrucción `|||` ya existe en `SOUL.md` línea 48
- El código de splitting ya existe en `loop.py:_split_chunks()` y funciona para todos los agentes
- **Cambio necesario:** Asegurar que la instrucción en SOUL.md cubra todos los escenarios, no solo "más de un tema". Reformular para que el agente siempre divida mensajes largos en bloques breves y naturales.

---

## Cambios por repo

### GAR

| Archivo | Cambio |
|---------|--------|
| `supabase/functions/notify-nanobot/index.ts` | Agregar caso `boleta_emitida`: consultar boleta URLs, contar pedidos previos, construir payload |
| Hook de creación de pedido (donde se llama `emitir-boleta`) | Después de emisión exitosa, llamar a Edge Function con evento `boleta_emitida` si `modo_envio='api'` |
| Migración: `idx_crm_mensajes_pedido_unico` | Modificar unique index para permitir múltiples eventos por pedido (agregar `tipo` o `metadata->>'event_type'` al index) |

### Nanobot

| Archivo | Cambio |
|---------|--------|
| `nanobot/webhook/routes.py` | 1) Agregar branch `boleta_emitida` en `format_crm_event()`. 2) Forwarding de `boleta` y `es_primer_pedido` en `handle_crm_webhook` metadata |
| `nanobot/agent/loop.py` | Adjuntar PDF al **último** chunk de OutboundMessage.media (texto primero, PDF al final) |
| `nanobot/channels/evolution.py` | 1) `_send_media()`: detectar URL vs archivo local. 2) `send()`: enviar texto primero, media al final (invertir orden actual). 3) Aceptar `caption` y `fileName` opcionales |
| `workspace/agents/lavanderia/SOUL.md` | Reforzar instrucción de `|||` como separador universal para mensajes largos |
| Contrato `docs/contracts/nanobot-gar-integration.md` | Actualizar payload `boleta_emitida` con `es_primer_pedido`, version bump |

### Detalle: `format_crm_event()` — branch `boleta_emitida`

```python
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
```

### Detalle: `handle_crm_webhook()` — forwarding de metadata

Agregar al dict de metadata en `InboundMessage`:
```python
"boleta": data.get("boleta", {}),
"es_primer_pedido": data.get("es_primer_pedido", False),
```

### Detalle: `_send_media()` — URL vs archivo local

```python
async def _send_media(self, number: str, media_path: str, caption: str = "", file_name: str = "") -> str:
    # Detectar si es URL o archivo local
    if media_path.startswith("http://") or media_path.startswith("https://"):
        media_value = media_path  # Pasar URL directamente
        mime = "application/pdf"  # Default para URLs
        if not file_name:
            file_name = media_path.split("/")[-1].split("?")[0]
    else:
        # Leer archivo local como base64 (código existente)
        ...
```

### Detalle: `send()` — orden texto primero, media al final

```python
async def send(self, msg: OutboundMessage) -> None:
    number = self._jid_to_number(msg.chat_id)

    # 1. Enviar texto primero
    chunks = self._split_message(msg.content)
    last_msg_id = ""
    for chunk in chunks:
        msg_id = await self._send_text(number, chunk)
        if msg_id:
            last_msg_id = msg_id
        if len(chunks) > 1:
            await asyncio.sleep(0.8)

    # 2. Enviar media al final (PDFs, imágenes)
    for media_path in (msg.media or []):
        if chunks:  # Delay entre texto y media
            await asyncio.sleep(0.8)
        msg_id = await self._send_media(number, media_path)
        if msg_id:
            last_msg_id = msg_id

    if last_msg_id:
        msg.metadata["evolution_msg_id"] = last_msg_id
```

### Detalle: `loop.py` — PDF en el último chunk

En `_handle_message()`, después de `_split_chunks()`, adjuntar media solo al último OutboundMessage:

```python
chunks = _split_chunks(response.content)
boleta_pdf = msg.metadata.get("boleta", {}).get("enlace_pdf", "")
for i, chunk in enumerate(chunks):
    is_last = (i == len(chunks) - 1)
    out = OutboundMessage(
        channel=out_channel,
        chat_id=msg.chat_id,
        content=chunk,
        media=[boleta_pdf] if (is_last and boleta_pdf) else [],
        metadata=msg.metadata,
    )
    await self.bus.publish_outbound(out)
    if not is_last:
        await asyncio.sleep(0.8)
```

---

## Constraint de crm_mensajes

El unique index actual es:
```sql
CREATE UNIQUE INDEX idx_crm_mensajes_pedido_unico
ON crm_mensajes(pedido_id)
WHERE estado_envio = 'pendiente' AND pedido_id IS NOT NULL;
```

Esto solo permite UN mensaje pendiente por pedido. Con Fase 2, un pedido puede tener:
- 1 mensaje `boleta_emitida` (al crear)
- 1 mensaje `prenda_terminada` (al terminar)

**Solución:** Modificar el index para incluir el tipo de evento:
```sql
DROP INDEX IF EXISTS idx_crm_mensajes_pedido_unico;
CREATE UNIQUE INDEX idx_crm_mensajes_pedido_evento_unico
ON crm_mensajes(pedido_id, tipo)
WHERE estado_envio = 'pendiente' AND pedido_id IS NOT NULL;
```

O alternativamente, usar `metadata->>'event_type'` si el campo `tipo` no es suficiente.

---

## Adjuntar PDF al mensaje

**Decisión: Pasar URL directamente a Evolution API** (sin descargar el archivo).

Evolution API `sendMedia` acepta URLs públicas en el campo `media`. Nanobot pasa la URL de Rapifac directamente. Esto evita I/O de descarga/upload.

`EvolutionChannel._send_media()` detecta si `media_path` empieza con `http` y lo pasa directo. Si es archivo local, usa base64 (código actual). Ver detalle en sección "Cambios por repo".

**Orden de envío:** Texto primero, PDF al final. Esto se logra invirtiendo el orden actual en `send()` (que hoy envía media antes que texto). Ver detalle en sección "Cambios por repo".

---

## Lo que NO cambia

- Endpoint webhook (`/webhook/crm`)
- Auth (Bearer token)
- Dedup (buffer de `crm_mensaje_id`)
- EvolutionChannel base (ya tiene `_send_media()`)
- Supabase client (`mark_sent` / `mark_failed`)
- Tabla `crm_mensajes` (solo cambia el index)

---

## Testing

1. **Unit test:** `format_crm_event()` con payload `boleta_emitida` + `es_primer_pedido`
2. **Unit test:** `_send_media()` con URL directa (no archivo local)
3. **Unit test:** Dedup con múltiples eventos del mismo pedido
4. **E2E local:** Crear pedido en GAR → verificar que llega WhatsApp con texto + PDF
5. **E2E:** Primer pedido de cliente nuevo → verificar tono de bienvenida

---

## Fases actualizadas

1. ~~prenda_terminada~~ — **COMPLETADA**
2. **boleta_emitida + bienvenida** — ESTA FASE
3. ~~pagos~~ — **DESCARTADA** (el pago es presencial, no aporta valor por WhatsApp)
4. **conversación bidireccional** — unificar sesiones, agente responde con contexto CRM
5. **delivery status** — tracking leído/entregado via Evolution API
6. **recordatorio de recojo** — pedido listo >48h sin recoger (nuevo, prioridad alta)
