# Contrato de Integración: Nanobot ↔ GAR CRM ↔ Evolution API

> Versión: 1.0 | Fecha: 2026-03-17 | Estado: Propuesta

---

## Visión General

Tres sistemas se comunican para automatizar notificaciones WhatsApp del negocio de lavandería:

```
┌──────────────┐     ┌───────────────────┐     ┌──────────────────┐
│  WhatsApp    │◄───►│  Evolution API     │     │  GAR CRM         │
│  Clientes    │     │  (Docker :8080)    │     │  (Lovable/React) │
└──────────────┘     └──┬──────────┬──────┘     └───────┬──────────┘
                        │          │                     │
              webhook   │          │ REST                │ Supabase
              (recv)    │          │ (send)              │
                        ▼          │                     ▼
                ┌───────────────────────┐     ┌──────────────────┐
                │  Nanobot (Docker)     │     │  Supabase        │
                │  :18790              │◄────►│  (PostgreSQL)    │
                │                       │     │  Edge Functions  │
                └───────────────────────┘     └──────────────────┘
```

**Responsabilidades:**

| Sistema | Responsabilidad |
|---------|----------------|
| **GAR CRM** | Decide QUÉ notificar (trigger en DB, crea `crm_mensajes` pendiente). Controla templates, opt-in, UI del operador. |
| **Nanobot** | Decide CÓMO notificar (LLM genera mensaje o usa template). Envía via Evolution API. Registra resultado. Maneja conversación bidireccional. |
| **Evolution API** | Infraestructura de WhatsApp. Envía/recibe mensajes. Maneja sesión, QR, reconexión. |
| **Supabase** | Base de datos compartida. Tabla `crm_mensajes` es el contrato de datos entre GAR y Nanobot. |

---

## Contrato 1: GAR → Nanobot (Webhook de Eventos CRM)

### Cuándo se dispara

Un **database trigger** en Supabase detecta cambios y una **Edge Function** envía el webhook.

| Evento | Trigger | Condición |
|--------|---------|-----------|
| `prenda_terminada` | `prendas.estado` cambia a `'terminado'` | Cliente tiene `whatsapp_opt_in != false` y teléfono |
| `pago_asignado` | `pagos_yape.estado` cambia a `'asignado'` | (Fase 2) |
| `primer_pedido` | INSERT en `pedidos` | Cliente tiene 0 pedidos previos (Fase 2) |
| `boleta_emitida` | UPDATE `pedidos.boleta_serie` de NULL a valor | (Fase 3) |

### Endpoint

```
POST http://nanobot:18790/webhook/crm
Content-Type: application/json
Authorization: Bearer ${NANOBOT_WEBHOOK_SECRET}
```

> **Nota Docker:** Dentro de la red Docker, nanobot es accesible como `http://nanobot:18790`.
> Desde Supabase Edge Functions (externo), usar la IP/dominio público del servidor.

### Payload: `prenda_terminada`

```jsonc
{
  "event": "prenda_terminada",
  "timestamp": "2026-03-17T14:30:00.000Z",
  "sucursal_id": "uuid-sucursal",
  "data": {
    "cliente": {
      "cliente_id": "C-000123",           // PK en clientes
      "nombre": "María López García",
      "nombre_preferido": "Marita",       // null si no tiene
      "telefono_whatsapp": "+51987654321",// Prioridad: telefono_whatsapp > telefono
      "whatsapp_opt_in": true,            // true | null = enviar, false = no enviar
      "ultimo_indice_plantilla": 5        // Para rotación de templates
    },
    "pedido": {
      "codigo": "B001-4",                 // PK en pedidos
      "importe": 45.00,
      "pago_efectivo": 20.00,
      "pago_yape": 0.00,
      "pago_credito": 0.00,
      "saldo": 25.00,                     // Calculado: importe - pagos
      "fecha_entrega": "2026-03-18"       // null si no se asignó
    },
    "prendas": [                           // Prendas terminadas en este batch
      {
        "prenda_id": "PRE-0001",
        "servicio": "lavado",
        "cantidad": 2
      },
      {
        "prenda_id": "PRE-0002",
        "servicio": "planchado",
        "cantidad": 1
      }
    ],
    "template_sugerido": {                 // Opcional: GAR sugiere template
      "plantilla_id": "uuid-plantilla",
      "nombre": "V6",
      "contenido_renderizado": "Hola Marita! 👋\nTus prendas están listas:\n- 2x lavado\n- 1x planchado\nSaldo pendiente: S/25.00\nTe esperamos!"
    },
    "crm_mensaje_id": "uuid-mensaje"       // ID del registro en crm_mensajes (ya creado como 'pendiente')
  }
}
```

### Payload: `pago_asignado` (Fase 2)

```jsonc
{
  "event": "pago_asignado",
  "timestamp": "2026-03-17T14:30:00.000Z",
  "sucursal_id": "uuid-sucursal",
  "data": {
    "cliente": { /* mismo formato */ },
    "pedido": { /* mismo formato, con saldo actualizado */ },
    "pago": {
      "monto": 25.00,
      "metodo": "yape",                   // yape | efectivo
      "codigo_yape": "YP-2026-001"        // Solo para yape
    },
    "crm_mensaje_id": "uuid-mensaje"
  }
}
```

### Payload: `boleta_emitida`

```jsonc
{
  "event": "boleta_emitida",
  "timestamp": "2026-03-17T14:30:00.000Z",
  "sucursal_id": "uuid-sucursal",
  "data": {
    "cliente": { /* mismo formato */ },
    "pedido": { /* mismo formato */ },
    "boleta": {
      "serie": "B001",
      "correlativo": "00001234",
      "codigo_completo": "B001-00001234",
      "pdf_base64": "<base64 encoded PDF or null>",  // Generado por generar-ticket-pdf
      "pdf_filename": "Boleta-B001-00001234.pdf"
    },
    "es_primer_pedido": true,
    "crm_mensaje_id": "uuid-mensaje",
    "template_sugerido": {                            // Presente cuando hay templates en DB
      "contenido_renderizado": "¡Hola María! ..."
    }
  }
}
```

> **Nota (2026-03-19):** Ambos eventos (`prenda_terminada` y `boleta_emitida`) ahora leen templates desde la tabla `whatsapp_templates` en GAR. Cuando hay templates disponibles, el payload incluye `template_sugerido.contenido_renderizado` y Nanobot bypassa el LLM. Si no hay templates en DB, el campo se omite y Nanobot usa LLM como fallback. El PDF de boleta se genera internamente via `generar-ticket-pdf` (base64), reemplazando el anterior `enlace_pdf` de SUNAT.

### Respuestas esperadas de Nanobot

```jsonc
// Éxito: evento aceptado para procesamiento
{
  "status": "accepted",
  "crm_mensaje_id": "uuid-mensaje"
}
// HTTP 202 Accepted

// Error de validación
{
  "status": "error",
  "error": "Missing required field: data.cliente.telefono_whatsapp"
}
// HTTP 400 Bad Request

// Error de autenticación
{
  "status": "error",
  "error": "Invalid webhook secret"
}
// HTTP 401 Unauthorized

// Evento duplicado (mismo crm_mensaje_id ya procesado)
{
  "status": "duplicate",
  "crm_mensaje_id": "uuid-mensaje"
}
// HTTP 200 OK
```

> **Importante:** Nanobot responde `202 Accepted` inmediatamente. El procesamiento
> (LLM + envío WhatsApp) es asíncrono. El resultado se refleja en `crm_mensajes`.
> Si la Edge Function reintenta, Nanobot detecta el duplicado y responde `200` sin reprocesar.

---

## Contrato 2: Nanobot → Evolution API (Enviar WhatsApp)

### Endpoint: Enviar texto

```
POST http://evolution-api:8080/message/sendText/{instanceName}
Content-Type: application/json
apikey: ${EVOLUTION_API_KEY}
```

```jsonc
{
  "number": "51987654321",               // Sin +, sin @s.whatsapp.net
  "text": "Hola Marita! 👋\nTus prendas están listas...",
  "delay": 1200                           // ms de delay (simula typing, opcional)
}
```

### Endpoint: Enviar media (Fase 3 — boletas PDF)

```
POST http://evolution-api:8080/message/sendMedia/{instanceName}
Content-Type: application/json
apikey: ${EVOLUTION_API_KEY}
```

```jsonc
{
  "number": "51987654321",
  "mediatype": "document",
  "mimetype": "application/pdf",
  "caption": "Aquí tienes tu boleta electrónica B001-00001234",
  "media": "https://rapifac.com/boletas/xxx.pdf",  // URL pública
  "fileName": "Boleta-B001-00001234.pdf"
}
```

### Respuesta de Evolution API

```jsonc
{
  "key": {
    "remoteJid": "51987654321@s.whatsapp.net",
    "fromMe": true,
    "id": "BAE5941A012345AB"              // Message ID para tracking
  },
  "message": {
    "extendedTextMessage": {
      "text": "Hola Marita! 👋..."
    }
  },
  "messageTimestamp": "1710680000",
  "status": "SERVER_ACK"
}
```

### Manejo de errores

| HTTP Status | Significado | Acción de Nanobot |
|-------------|-------------|-------------------|
| 200 | Enviado OK | Actualizar `crm_mensajes.estado_envio = 'enviado_api'` |
| 400 | Número inválido | Marcar `crm_mensajes.estado_envio = 'fallido'`, log error |
| 404 | Instancia no encontrada | Retry con backoff, alertar |
| 500 | Error interno Evolution | Retry (max 3 intentos, backoff exponencial) |

---

## Contrato 3: Evolution API → Nanobot (Mensajes Entrantes)

### Configuración del webhook

```
# En Evolution API (.env o via API)
WEBHOOK_GLOBAL_ENABLED=true
WEBHOOK_GLOBAL_URL=http://nanobot:18790/webhook/evolution
WEBHOOK_EVENTS_MESSAGES_UPSERT=true
WEBHOOK_EVENTS_CONNECTION_UPDATE=true
WEBHOOK_EVENTS_QRCODE_UPDATED=true
```

### Endpoint en Nanobot

```
POST http://nanobot:18790/webhook/evolution
Content-Type: application/json
```

### Payload: Mensaje de texto entrante

```jsonc
{
  "event": "MESSAGES_UPSERT",
  "instance": "lavanderia-principal",
  "data": {
    "key": {
      "remoteJid": "51987654321@s.whatsapp.net",
      "fromMe": false,
      "id": "BAE594145F4C59B4"
    },
    "message": {
      "conversation": "Hola, quiero saber si mis prendas están listas"
    },
    "messageType": "conversation",
    "messageTimestamp": 1717689097,
    "pushName": "María López",
    "status": "SERVER_ACK"
  },
  "sender": "51987654321@s.whatsapp.net",
  "apikey": "instance-api-key"
}
```

### Mapeo a InboundMessage de Nanobot

```python
# Nanobot convierte webhook Evolution → InboundMessage
InboundMessage(
    channel="whatsapp",
    sender_id="51987654321",                          # remoteJid sin @s.whatsapp.net
    chat_id="51987654321@s.whatsapp.net",             # remoteJid completo
    content="Hola, quiero saber si mis prendas están listas",
    media=[],                                          # URLs si hay media
    metadata={
        "message_id": "BAE594145F4C59B4",
        "push_name": "María López",
        "instance": "lavanderia-principal",
        "message_type": "conversation",
        "timestamp": 1717689097,
    }
)
```

### Payload: Actualización de conexión

```jsonc
{
  "event": "CONNECTION_UPDATE",
  "instance": "lavanderia-principal",
  "data": {
    "instance": "lavanderia-principal",
    "state": "open"                       // open | close | connecting
  }
}
```

### Payload: QR Code

```jsonc
{
  "event": "QRCODE_UPDATED",
  "instance": "lavanderia-principal",
  "data": {
    "pairingCode": "WZYEH1YY",
    "code": "2@y8eK+bjtEjUWy9/FOM...",
    "count": 1
  }
}
```

---

## Contrato 4: Nanobot → Supabase (Actualizar crm_mensajes)

Nanobot actualiza directamente la tabla `crm_mensajes` en Supabase después de enviar (o fallar).

### Credenciales

```bash
SUPABASE_URL=https://sxnfccqpjxoipptgsowu.supabase.co
SUPABASE_SERVICE_KEY=eyJ...  # Service role key (bypasa RLS)
```

### Operación: Marcar como enviado

```sql
UPDATE crm_mensajes
SET
    estado_envio = 'enviado_api',
    metadata = metadata || '{"source": "nanobot", "evolution_msg_id": "BAE5941A012345AB", "sent_at": "2026-03-17T14:30:05Z"}'::jsonb,
    updated_at = NOW()
WHERE id = '{crm_mensaje_id}'
```

Equivalente en Python (supabase-py):

```python
await db.table("crm_mensajes").update({
    "estado_envio": "enviado_api",
    "metadata": {
        **existing_metadata,
        "source": "nanobot",
        "agent": "lavanderia",
        "evolution_msg_id": "BAE5941A012345AB",
        "sent_at": "2026-03-17T14:30:05.000Z",
    },
}).eq("id", crm_mensaje_id).execute()
```

### Operación: Marcar como fallido

```python
await db.table("crm_mensajes").update({
    "estado_envio": "fallido",
    "detalle_error": "Evolution API error: número inválido (400)",
    "metadata": {
        **existing_metadata,
        "source": "nanobot",
        "error_at": "2026-03-17T14:30:05.000Z",
        "retry_count": 2,
    },
}).eq("id", crm_mensaje_id).execute()
```

### Operación: Registrar mensaje generado por LLM (sin template de GAR)

Cuando nanobot genera el mensaje con LLM en lugar de usar el template sugerido:

```python
await db.table("crm_mensajes").update({
    "estado_envio": "enviado_api",
    "mensaje_renderizado": "Hola Marita! Tus prendas ya están listas para recoger...",
    "metadata": {
        **existing_metadata,
        "source": "nanobot",
        "agent": "lavanderia",
        "generation_mode": "llm",   # "llm" | "template"
        "evolution_msg_id": "BAE5941A012345AB",
        "sent_at": "2026-03-17T14:30:05.000Z",
    },
}).eq("id", crm_mensaje_id).execute()
```

---

## Cambios en Schema de GAR (Supabase)

### 1. Nuevos valores para enums existentes

```sql
-- Estado de envío: agregar estados para envío automático
ALTER TYPE send_status ADD VALUE IF NOT EXISTS 'enviado_api';
ALTER TYPE send_status ADD VALUE IF NOT EXISTS 'fallido';

-- Tipo de mensaje: agregar tipo para nanobot
ALTER TYPE message_type ADD VALUE IF NOT EXISTS 'automatico_nanobot';
```

### 2. Nuevos campos opcionales en crm_mensajes

No se requieren columnas nuevas. El campo `metadata` (JSONB) ya existe y es extensible.

**Convención para metadata de nanobot:**

```jsonc
{
  // Campos existentes de GAR
  "auto_generated": true,
  "generated_at": "2026-03-17T14:30:00.000Z",
  "cliente_nombre": "María López",
  "servicio": "lavado",

  // Campos agregados por nanobot
  "source": "nanobot",               // Identifica origen
  "agent": "lavanderia",             // Nombre del agente
  "event_type": "prenda_terminada",  // Tipo de evento que disparó
  "generation_mode": "llm",          // "llm" | "template"
  "evolution_msg_id": "BAE...",      // ID del mensaje en Evolution
  "sent_at": "ISO-timestamp",        // Cuándo se envió realmente
  "error_at": "ISO-timestamp",       // Si falló
  "retry_count": 0                   // Intentos
}
```

### 3. Edge Function: `notify-nanobot`

**Archivo:** `supabase/functions/notify-nanobot/index.ts`

```typescript
// Recibe evento de DB trigger, enriquece datos, envía a nanobot
// Trigger: prendas.estado changed to 'terminado'
//
// Pasos:
// 1. Recibir prenda_id del trigger
// 2. Consultar pedido, cliente, prendas terminadas del mismo pedido
// 3. Verificar whatsapp_opt_in != false
// 4. Verificar teléfono existe
// 5. Crear registro en crm_mensajes con estado 'pendiente'
// 6. POST al webhook de nanobot
// 7. Si nanobot no responde, el registro queda 'pendiente' para retry
```

### 4. Database trigger

```sql
CREATE OR REPLACE FUNCTION notify_prenda_terminada()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.estado = 'terminado' AND OLD.estado != 'terminado' THEN
        -- Invocar Edge Function via pg_net o http extension
        PERFORM net.http_post(
            url := current_setting('app.notify_nanobot_url'),
            body := json_build_object(
                'prenda_id', NEW.prenda_id,
                'pedido_id', NEW.pedido_id
            )::text,
            headers := json_build_object(
                'Content-Type', 'application/json'
            )::jsonb
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_prenda_terminada
AFTER UPDATE ON prendas
FOR EACH ROW
EXECUTE FUNCTION notify_prenda_terminada();
```

---

## Cambios en Nanobot

### 1. Nuevo canal: EvolutionChannel

Reemplaza `WhatsAppChannel` + bridge de Node.js.

```python
class EvolutionChannel(BaseChannel):
    name = "whatsapp"  # Mantiene el nombre "whatsapp" para compatibilidad

    async def start(self) -> None:
        """Inicia HTTP server para recibir webhooks de Evolution API."""
        # Endpoint: POST /webhook/evolution
        # Parsea payload → InboundMessage → bus.publish_inbound()

    async def send(self, msg: OutboundMessage) -> None:
        """Envía mensaje via REST a Evolution API."""
        # POST http://evolution-api:8080/message/sendText/{instance}
        # Headers: apikey: {EVOLUTION_API_KEY}
        # Body: {number: clean_number, text: msg.content}

    async def stop(self) -> None:
        """Cierra HTTP server."""
```

### 2. Nuevo endpoint: Webhook CRM

```python
# En el HTTP server de nanobot (mismo server que recibe webhooks de Evolution)
# Endpoint: POST /webhook/crm
# Auth: Bearer token

async def handle_crm_webhook(request):
    """Recibe eventos de GAR CRM y los publica al bus."""
    payload = await request.json()
    event_type = payload["event"]
    data = payload["data"]

    msg = InboundMessage(
        channel="crm_event",
        sender_id="crm_system",
        chat_id=data["cliente"]["telefono_whatsapp"],
        content=format_crm_event(payload),  # Texto estructurado para el agente
        metadata={
            "event_type": event_type,
            "crm_mensaje_id": data.get("crm_mensaje_id"),
            "cliente_id": data["cliente"]["cliente_id"],
            "pedido_codigo": data["pedido"]["codigo"],
        },
    )
    await bus.publish_inbound(msg)
    return {"status": "accepted"}
```

### 3. Configuración nueva

```bash
# Evolution API
NANOBOT_CHANNELS__WHATSAPP__PROVIDER=evolution        # "evolution" | "bridge" (legacy)
NANOBOT_CHANNELS__WHATSAPP__EVOLUTION_API_URL=http://evolution-api:8080
NANOBOT_CHANNELS__WHATSAPP__EVOLUTION_API_KEY=your-key
NANOBOT_CHANNELS__WHATSAPP__EVOLUTION_INSTANCE=lavanderia-principal

# Webhook server
NANOBOT_WEBHOOK__ENABLED=true
NANOBOT_WEBHOOK__PORT=18790                           # Mismo puerto que gateway
NANOBOT_WEBHOOK__SECRET=shared-secret-with-gar

# Supabase (para crm_notify tool)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
```

### 4. Docker Compose actualizado

```yaml
services:
  evolution-api:
    image: atendai/evolution-api:v2.2.3
    container_name: evolution_api
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      - AUTHENTICATION_API_KEY=${EVOLUTION_API_KEY}
      - SERVER_URL=${EVOLUTION_SERVER_URL:-http://localhost:8080}
      - WEBHOOK_GLOBAL_ENABLED=true
      - WEBHOOK_GLOBAL_URL=http://nanobot:18790/webhook/evolution
      - WEBHOOK_EVENTS_MESSAGES_UPSERT=true
      - WEBHOOK_EVENTS_CONNECTION_UPDATE=true
      - WEBHOOK_EVENTS_QRCODE_UPDATED=true
    volumes:
      - evolution_data:/evolution/instances

  nanobot:
    build: .
    container_name: nanobot
    restart: unless-stopped
    ports:
      - "18790:18790"
    env_file:
      - .env
    volumes:
      - nanobot-data:/root/.nanobot
    depends_on:
      - evolution-api
    command: ["nanobot", "gateway"]        # Ya no necesita bridge

volumes:
  evolution_data:
  nanobot-data:
```

---

## Flujos Completos

### Flujo 1: Prenda terminada → Notificación WhatsApp

```
Operador (GAR CRM)                GAR Supabase              Nanobot                    Evolution API        Cliente
     │                                │                        │                            │                  │
     │ UPDATE prendas.estado          │                        │                            │                  │
     │ = 'terminado'                  │                        │                            │                  │
     │───────────────────────────────►│                        │                            │                  │
     │                                │                        │                            │                  │
     │                    DB Trigger   │                        │                            │                  │
     │                    fires        │                        │                            │                  │
     │                                │                        │                            │                  │
     │                    Edge Fn:     │                        │                            │                  │
     │                    1. Query     │                        │                            │                  │
     │                       cliente,  │                        │                            │                  │
     │                       pedido,   │                        │                            │                  │
     │                       prendas   │                        │                            │                  │
     │                    2. Check     │                        │                            │                  │
     │                       opt-in    │                        │                            │                  │
     │                    3. Create    │                        │                            │                  │
     │                       crm_msg   │                        │                            │                  │
     │                       pendiente │                        │                            │                  │
     │                                │                        │                            │                  │
     │                                │ POST /webhook/crm      │                            │                  │
     │                                │ {event, data}          │                            │                  │
     │                                │───────────────────────►│                            │                  │
     │                                │                        │                            │                  │
     │                                │       202 Accepted     │                            │                  │
     │                                │◄───────────────────────│                            │                  │
     │                                │                        │                            │                  │
     │                                │                        │ AgentLoop procesa:         │                  │
     │                                │                        │ LLM genera mensaje         │                  │
     │                                │                        │ natural y variado          │                  │
     │                                │                        │                            │                  │
     │                                │                        │ POST /message/sendText     │                  │
     │                                │                        │ {number, text}             │                  │
     │                                │                        │───────────────────────────►│                  │
     │                                │                        │                            │  WhatsApp msg    │
     │                                │                        │                            │─────────────────►│
     │                                │                        │                            │                  │
     │                                │                        │   200 {key.id}             │                  │
     │                                │                        │◄───────────────────────────│                  │
     │                                │                        │                            │                  │
     │                                │ UPDATE crm_mensajes    │                            │                  │
     │                                │ estado = 'enviado_api' │                            │                  │
     │                                │◄───────────────────────│                            │                  │
     │                                │                        │                            │                  │
     │ UI se actualiza                │                        │                            │                  │
     │ (Supabase Realtime)            │                        │                            │                  │
     │◄───────────────────────────────│                        │                            │                  │
```

### Flujo 2: Cliente responde por WhatsApp

```
Cliente                  Evolution API        Nanobot                     GAR Supabase
  │                            │                  │                            │
  │ "Hola, ya puedo ir        │                  │                            │
  │  a recoger?"              │                  │                            │
  │───────────────────────────►│                  │                            │
  │                            │                  │                            │
  │                            │ POST /webhook/   │                            │
  │                            │ evolution        │                            │
  │                            │ MESSAGES_UPSERT  │                            │
  │                            │─────────────────►│                            │
  │                            │                  │                            │
  │                            │                  │ AgentLoop:                 │
  │                            │                  │ 1. Identifica cliente      │
  │                            │                  │    por número              │
  │                            │                  │ 2. Consulta pedidos        │
  │                            │                  │    del cliente             │
  │                            │                  │ 3. LLM responde           │
  │                            │                  │    con contexto            │
  │                            │                  │                            │
  │                            │ POST /message/   │                            │
  │                            │ sendText         │                            │
  │◄───────────────────────────│◄─────────────────│                            │
  │ "Hola Marita! Sí, tus     │                  │                            │
  │  3 prendas están listas.  │                  │                            │
  │  Te esperamos hasta       │                  │                            │
  │  las 8pm"                 │                  │                            │
```

### Flujo 3: Operador envía manualmente (modo actual GAR, sigue funcionando)

```
Operador (GAR CRM)                GAR Supabase
     │                                │
     │ Clic "Abrir WhatsApp"          │
     │ (crm_mensajes pendiente)       │
     │                                │
     │ 1. Copia mensaje al clipboard  │
     │ 2. Abre wa.me/51987654321      │
     │ 3. Pega y envía manualmente    │
     │                                │
     │ Confirma envío en UI           │
     │───────────────────────────────►│
     │                                │ UPDATE crm_mensajes
     │                                │ estado = 'enviado_manual'
```

> **Ambos modos coexisten.** `whatsapp_config.modo_envio` controla si GAR usa `'abrir_app'`
> (manual) o `'api'` (nanobot). En modo `'api'`, GAR crea el registro pendiente
> y dispara el webhook; nanobot se encarga del envío.

---

## Formato de Teléfono

| Contexto | Formato | Ejemplo |
|----------|---------|---------|
| GAR DB (`clientes.telefono_whatsapp`) | E.164 con + | `+51987654321` |
| Evolution API (`number`) | Sin + ni sufijo | `51987654321` |
| WhatsApp JID (interno Evolution) | Con @s.whatsapp.net | `51987654321@s.whatsapp.net` |
| Nanobot `InboundMessage.chat_id` | JID completo | `51987654321@s.whatsapp.net` |
| Nanobot `InboundMessage.sender_id` | Solo número | `51987654321` |

**Funciones de conversión en Nanobot:**

```python
def phone_to_evolution(phone: str) -> str:
    """E.164 → Evolution format. '+51987654321' → '51987654321'"""
    return phone.replace("+", "").replace(" ", "").replace("-", "")

def phone_to_jid(phone: str) -> str:
    """E.164 → WhatsApp JID. '+51987654321' → '51987654321@s.whatsapp.net'"""
    return f"{phone_to_evolution(phone)}@s.whatsapp.net"

def jid_to_phone(jid: str) -> str:
    """WhatsApp JID → number. '51987654321@s.whatsapp.net' → '51987654321'"""
    return jid.split("@")[0]
```

---

## Seguridad

### Autenticación entre servicios

| Ruta | Mecanismo | Variable de entorno |
|------|-----------|-------------------|
| GAR Edge Fn → Nanobot | Bearer token en header `Authorization` | `NANOBOT_WEBHOOK_SECRET` (ambos lados) |
| Nanobot → Evolution API | API key en header `apikey` | `EVOLUTION_API_KEY` |
| Evolution API → Nanobot | Ninguno (red Docker interna, no expuesto) | — |
| Nanobot → Supabase | Service role key | `SUPABASE_SERVICE_KEY` |

### Red Docker

```
evolution-api:8080  ─── red interna ─── nanobot:18790
                                              │
                                         puerto expuesto
                                         (solo si necesario
                                          para GAR Edge Fn)
```

- Evolution API **no se expone** al internet público (solo red Docker interna).
- Nanobot expone `:18790` solo si GAR Edge Functions necesitan alcanzarlo desde fuera de Docker.
- Si el servidor tiene IP pública, usar reverse proxy (nginx/caddy) con HTTPS para el webhook externo.

---

## Fases de Implementación

### Fase 1: Infraestructura base (este plan)
- [ ] EvolutionChannel en nanobot (reemplaza bridge)
- [ ] HTTP webhook server en nanobot
- [ ] Docker compose con Evolution API
- [ ] Evento `prenda_terminada` end-to-end
- [ ] Edge Function `notify-nanobot` en GAR
- [ ] DB trigger en GAR
- [ ] Nuevos valores en enums de GAR

### Fase 2: Pagos y bienvenida
- [ ] Evento `pago_asignado`
- [ ] Evento `primer_pedido` (bienvenida)
- [ ] Templates para cada tipo

### Fase 3: Boletas con media
- [ ] Evento `boleta_emitida`
- [ ] Envío de PDF via Evolution API sendMedia
- [ ] Templates de boleta

### Fase 4: Conversación bidireccional
- [ ] Agente responde mensajes entrantes con contexto CRM
- [ ] Consulta pedidos/prendas del cliente por teléfono
- [ ] Manejo de preguntas frecuentes

### Fase 5: Delivery status
- [ ] Suscripción a `MESSAGES_UPDATE` de Evolution API
- [ ] Tracking de leído/entregado en crm_mensajes

---

## Testing

### Probar webhook CRM (curl)

```bash
curl -X POST http://localhost:18790/webhook/crm \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${NANOBOT_WEBHOOK_SECRET}" \
  -d '{
    "event": "prenda_terminada",
    "timestamp": "2026-03-17T14:30:00.000Z",
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

### Probar Evolution API (curl)

```bash
# Enviar mensaje de texto
curl -X POST http://localhost:8080/message/sendText/lavanderia-principal \
  -H "Content-Type: application/json" \
  -H "apikey: ${EVOLUTION_API_KEY}" \
  -d '{
    "number": "51987654321",
    "text": "Test desde nanobot"
  }'

# Ver estado de conexión
curl http://localhost:8080/instance/connectionState/lavanderia-principal \
  -H "apikey: ${EVOLUTION_API_KEY}"
```
