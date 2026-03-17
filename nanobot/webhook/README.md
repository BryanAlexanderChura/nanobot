# Webhook Server Module

HTTP server for receiving webhooks from external services.

## Architecture

```
External Service (Evolution API, CRM, etc.)
    │
    │  POST /webhook/{service}
    ▼
server.py  →  routes.py  →  channel._handle_message()  →  MessageBus
```

- **server.py** — Starts aiohttp web application, registers routes, manages lifecycle
- **routes.py** — Route handlers that parse payloads and delegate to channels

## Current Routes

| Route | Source | Purpose |
|-------|--------|---------|
| `POST /webhook/evolution` | Evolution API | Receive WhatsApp messages |

## Adding a New Route

1. Add handler function in `routes.py`
2. Register it in `setup_routes()`
3. Access bus/channels via `request.app["bus"]` and `request.app["channels"]`

## Configuration

```bash
NANOBOT_GATEWAY__WEBHOOK_ENABLED=true   # Enable the HTTP server
NANOBOT_GATEWAY__HOST=0.0.0.0           # Bind address
NANOBOT_GATEWAY__PORT=18790             # Bind port
```

## Testing with curl

```bash
# Simulate an Evolution API message
curl -X POST http://localhost:18790/webhook/evolution \
  -H "Content-Type: application/json" \
  -d '{
    "event": "MESSAGES_UPSERT",
    "instance": "test",
    "data": {
      "key": {
        "remoteJid": "51987654321@s.whatsapp.net",
        "fromMe": false,
        "id": "TEST123"
      },
      "message": {"conversation": "Hola"},
      "messageTimestamp": 1710680000,
      "pushName": "Test User"
    }
  }'
```

## Request Limits

aiohttp defaults to `client_max_size=1MB`, which is sufficient for JSON webhook
payloads. No custom limit is set.

## Migrating to FastAPI

If this server needs to scale beyond simple webhooks, replace with FastAPI:

1. Install: `pip install fastapi uvicorn`
2. Replace `server.py` with a FastAPI app that exposes the same
   `start_webhook_server(config, bus, channels)` function
3. Adapt route handlers in `routes.py` from `aiohttp.web.Request` to FastAPI
   `Request` objects (the logic stays the same)
4. The rest of nanobot only imports `start_webhook_server` — nothing else changes

The key contract is the function signature:

```python
async def start_webhook_server(
    config: GatewayConfig,
    bus: MessageBus,
    channels: dict[str, BaseChannel],
) -> None:
```

Any framework that implements this interface is a drop-in replacement.
