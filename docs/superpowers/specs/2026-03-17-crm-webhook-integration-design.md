# CRM Webhook Integration — Fase 1: prenda_terminada

> Fecha: 2026-03-17 | Estado: Aprobado

## Objetivo

Implementar el flujo end-to-end para que cuando un operador marque una prenda como "terminado" en GAR CRM, el cliente reciba automáticamente un mensaje de WhatsApp natural avisándole que su ropa está lista, y el operador vea el estado del envío actualizado en GAR.

## Flujo end-to-end

```
Operador marca prenda "terminado" en GAR
  → DB trigger llama Edge Function notify-nanobot
    → Edge Function junta datos (cliente, pedido, prendas, saldo)
    → Crea registro crm_mensajes con estado "pendiente"
    → POST /webhook/crm a Nanobot
      → Nanobot publica InboundMessage(channel="crm_event") al bus
        → Agente lavandería procesa el evento
        → Si hay template_sugerido → lo usa
        → Si no → LLM genera mensaje natural y personalizado
        → OutboundMessage(channel="whatsapp") al teléfono del cliente
          → EvolutionChannel envía via Evolution API
          → Nanobot actualiza crm_mensajes en Supabase (enviado_api / fallido)
            → Operador ve estado actualizado en GAR (Supabase Realtime)
```

## Enfoque elegido

**"CRM como canal"**: El webhook CRM se trata como un canal más que pasa por el AgentLoop. El agente recibe el contexto completo del cliente/pedido y decide qué mensaje enviar.

**Generación de mensajes — modo híbrido:**
- Si GAR envía `template_sugerido.contenido_renderizado`, se usa tal cual (rápido, sin costo LLM)
- Si no hay template, el LLM genera un mensaje natural y variado con el contexto recibido

**Justificación:** Aprovecha toda la infraestructura existente (bus, AgentLoop, skills), permite mensajes naturales y personalizados, y escala para las fases futuras (bidireccional).

## Diseño — Lado Nanobot

### 1. Endpoint `/webhook/crm` (en `webhook/routes.py`)

Nueva ruta registrada en `setup_routes()`, al lado de `/webhook/evolution`.

**Autenticación:** Header `Authorization: Bearer <token>` comparado contra `config.gateway.webhook_secret` (ya existe en `GatewayConfig`).

**Validación:** Verifica campos requeridos: `event`, `data.cliente.telefono_whatsapp`, `data.crm_mensaje_id`.

**Respuesta:** `202 Accepted` inmediato. El procesamiento es asíncrono.

**Publicación directa al bus:** A diferencia del webhook de Evolution (que delega a `channel._handle_message()`), el webhook CRM publica directamente al bus con `request.app["bus"].publish_inbound(msg)`. No hay un channel object para `crm_event` — la autenticación se hace en el handler con el Bearer token.

**Utilidad `phone_to_jid`:** Nueva función helper para convertir teléfono E.164 a JID de WhatsApp. Se crea en `webhook/routes.py` o en un módulo de utils.
```python
def phone_to_jid(phone: str) -> str:
    """'+51987654321' → '51987654321@s.whatsapp.net'"""
    return f"{phone.replace('+', '').replace(' ', '').replace('-', '')}@s.whatsapp.net"
```

**InboundMessage generado:**
```python
InboundMessage(
    channel="crm_event",
    sender_id="crm_system",
    chat_id=phone_to_jid(data["cliente"]["telefono_whatsapp"]),
    content="EVENTO CRM: prenda_terminada. Cliente: Marita (...). Pedido B001-4: 2x lavado...",
    metadata={
        "event_type": "prenda_terminada",
        "crm_mensaje_id": "uuid",
        "reply_channel": "whatsapp",
        "cliente": { ... },
        "pedido": { ... },
        "prendas": [ ... ],
        "template_sugerido": "..." or None,
    }
)
```

### 2. Routing crm_event → WhatsApp

El agente lavandería declara `crm_event` en sus canales:
```yaml
# workspace/agents/lavanderia/agent.yaml
channels: [whatsapp, crm_event]
```

El `InboundMessage` llega con `channel="crm_event"`. El agente genera la respuesta. El `OutboundMessage` debe salir con `channel="whatsapp"`, no `"crm_event"`.

**Modificación requerida en `agent/loop.py`:** Actualmente `_process_message()` (línea 331) hardcodea `channel=msg.channel` en el OutboundMessage. Se debe modificar para que lea `msg.metadata.get("reply_channel")` y lo use como canal de salida si existe:

```python
# Antes:
return OutboundMessage(channel=msg.channel, ...)

# Después:
out_channel = msg.metadata.get("reply_channel", msg.channel)
return OutboundMessage(channel=out_channel, ...)
```

Esto permite que el ChannelManager entregue el mensaje al EvolutionChannel registrado como "whatsapp".

**Sesiones:** El session_key será `"crm_event:51987654321@s.whatsapp.net"`, separado de la sesión WhatsApp directa `"whatsapp:51987654321@s.whatsapp.net"`. Para Fase 1 esto es aceptable — el evento CRM es autocontenido. En Fase 4 (bidireccional) se unificarán las sesiones.

### 3. Actualización de crm_mensajes (`integrations/supabase.py`)

Módulo ligero que actualiza `crm_mensajes` vía REST API de Supabase (usando `httpx` para no agregar dependencia pesada).

**Operaciones:**
- `update_crm_mensaje_enviado(crm_mensaje_id, evolution_msg_id, mensaje_generado)` — estado → `enviado_api`
- `update_crm_mensaje_fallido(crm_mensaje_id, error, retry_count)` — estado → `fallido`

**Se ejecuta como callback** después de que EvolutionChannel envía (o falla) el WhatsApp.

### 4. Config (ya existe en `config/schema.py`)

Los campos de config necesarios ya existen:
- `GatewayConfig.webhook_secret` (línea 100) — env var: `NANOBOT_GATEWAY__WEBHOOK_SECRET`
- `ToolsConfig.supabase.url` y `supabase.service_key` (líneas 120-123) — env vars: `NANOBOT_TOOLS__SUPABASE__URL`, `NANOBOT_TOOLS__SUPABASE__SERVICE_KEY`

No se necesitan cambios en el schema. Solo configurar las env vars en `.env`.

## Diseño — Lado GAR

### 1. Edge Function `notify-nanobot`

**Archivo:** `supabase/functions/notify-nanobot/index.ts`

**Patrón:** Igual que `webhook-yape-notification` (token auth, Supabase service role client, JSON response).

**Pasos:**
1. Recibe `{ prenda_id, pedido_id }` del trigger
2. Consulta cliente, pedido, prendas terminadas del mismo pedido
3. Verifica `whatsapp_opt_in != false` y teléfono existe
4. Busca template sugerido (si aplica, rotación por `ultimo_indice_plantilla`)
5. Crea registro en `crm_mensajes` con estado `pendiente` y tipo `automatico_nanobot`
6. POST a Nanobot `/webhook/crm` con payload completo
7. Si Nanobot no responde, el registro queda `pendiente` para envío manual

**Autenticación:** Header `Authorization: Bearer <NANOBOT_WEBHOOK_SECRET>` (secreto compartido).

### 2. Migración SQL

**Archivo:** `supabase/migrations/YYYYMMDD_nanobot_integration.sql`

**Contenido:**
- `ALTER TYPE send_status ADD VALUE IF NOT EXISTS 'enviado_api'`
- `ALTER TYPE send_status ADD VALUE IF NOT EXISTS 'fallido'`
- `ALTER TYPE message_type ADD VALUE IF NOT EXISTS 'automatico_nanobot'`
- Función `notify_prenda_terminada()` que usa `pg_net` para llamar a la Edge Function
- Trigger `trg_prenda_terminada` en tabla `prendas` AFTER UPDATE

### 3. Agent workspace

Crear o actualizar `workspace/agents/lavanderia/agent.yaml` con `channels: [whatsapp, crm_event]`.

## Archivos a crear/modificar

| Repo | Archivo | Acción |
|------|---------|--------|
| Nanobot | `nanobot/webhook/routes.py` | Modificar — agregar ruta `/webhook/crm` + helper `phone_to_jid` |
| Nanobot | `nanobot/agent/loop.py` | Modificar — leer `metadata["reply_channel"]` para cross-channel routing |
| Nanobot | `nanobot/integrations/__init__.py` | Crear |
| Nanobot | `nanobot/integrations/supabase.py` | Crear — cliente para actualizar crm_mensajes |
| Nanobot | `workspace/agents/lavanderia/agent.yaml` | Crear/modificar — agregar crm_event |
| GAR | `supabase/functions/notify-nanobot/index.ts` | Crear — Edge Function |
| GAR | `supabase/migrations/YYYYMMDD_nanobot_integration.sql` | Crear — enums + trigger |

## Fases futuras (documentadas, no implementar ahora)

### Fases 2-3: Bienvenida inteligente con boleta

Cuando un cliente nuevo hace su primer pedido y se emite la boleta:
1. Mensaje de bienvenida y agradecimiento por usar el servicio
2. Envío del PDF de la boleta por WhatsApp
3. Aclaración del estado de pago (pendiente o pagado)

Esto establece el primer contacto con valor real (boleta útil), resolviendo el problema del opt-in de forma natural.

**Eventos involucrados:** `primer_pedido` + `boleta_emitida` + `pago_asignado`, combinados en un flujo unificado para clientes nuevos.

### Fase 2b: Confirmación de pago recibido

Cuando el operario marca un pedido como cobrado, enviar confirmación por WhatsApp al cliente. Escenarios:
- Pago con efectivo → "Recibimos tu pago de S/X"
- Pago con Yape → incluir info adicional del Yape (código, nombre pagador)
- Pago mixto (efectivo + Yape) → detallar ambos métodos
- Pago parcial → informar monto recibido y saldo restante

**Evento:** `pago_recibido` — trigger cuando operario registra cobro en GAR.

### Fase 4: Conversación bidireccional
### Fase 5: Delivery status (leído/entregado)
