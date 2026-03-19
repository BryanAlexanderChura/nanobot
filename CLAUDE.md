# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

NLM_NOTEBOOK_ID=nanobot

## Build & Development Commands

```bash
# Install for development
pip install -e ".[dev]"

# Run with uv (preferred)
uv run nanobot agent -m "Hello"

# Run tests
pytest tests/ -v

# Run single test
pytest tests/test_tool_validation.py -v

# Lint
ruff check .

# WhatsApp bridge (Node.js 18+)
cd bridge && npm install && npm run build

# Docker (build + run)
docker compose build
docker compose up -d

# Docker: solo agente CLI (sin gateway)
docker run --rm --env-file .env -v nanobot-data:/root/.nanobot nanobot-nanobot agent -m "Hello"
```

## Architecture Overview

Nanobot is an ultra-lightweight (~4k LOC) personal AI assistant framework. The core data flow is:

```
Channel (Telegram/WhatsApp/Feishu)
  → MessageBus (async queue)
    → AgentLoop (agentic tool-calling loop)
      → LLMProvider (LiteLLM or OpenAI SDK)
    → MessageBus
  → Channel.send()
```

### Multi-Agent Architecture

Agents are self-contained folders under `workspace/agents/`:

```
workspace/agents/
├── general/              # Default CLI agent (all tools)
│   ├── agent.yaml        # tools: [], channels: []
│   ├── IDENTITY.md
│   ├── SOUL.md
│   ├── memory/
│   └── skills/
└── lavanderia/           # Specialized agent
    ├── agent.yaml        # tools: [safe, comms], channels: [whatsapp]
    ├── IDENTITY.md
    ├── SOUL.md
    ├── memory/
    └── skills/
```

- **`agent.yaml`** — Declares `tools`, `channels`, `session_backend`. Name inferred from folder.
- **`agent/factory.py`** — `discover_agents()` scans `workspace/agents/*/agent.yaml` to build profiles. `create_agent_from_profile()` instantiates AgentLoop per profile.
- **Tool groups** — Defined in `AgentLoop.TOOL_GROUPS`: `safe`, `files`, `web`, `comms`, `system`. Profiles reference groups or individual tool names.
- **Handoff** — `HandoffTool` routes messages between agents via the bus (`channel=handoff:{target}`).
- **Skills** load in 3 levels: agent-specific (`agents/{name}/skills/`) → shared (`workspace/skills/`) → builtin (`nanobot/skills/`).

### Key Modules

- **`agent/loop.py`** — Core agentic loop. Receives messages, builds context, calls LLM, executes tools in a loop (max 20 iterations), returns response. Entry points: `run()` (bus consumer) and `process_direct()` (CLI).
- **`agent/context.py`** — Assembles system prompt from `workspace/agents/{entity}/` files (IDENTITY.md, SOUL.md, etc.), memory, and skills.
- **`bus/queue.py`** — `MessageBus` decouples channels from agent via `InboundMessage`/`OutboundMessage` async queues.
- **`providers/factory.py`** — Factory selects `LiteLLMProvider` (multi-provider via litellm) or `OpenAIProvider` (direct SDK) based on `config.agents.defaults.provider`.
- **`channels/manager.py`** — Starts enabled channels, routes outbound messages. Each channel implements `BaseChannel` (start/stop/send/is_allowed).
- **`config/schema.py`** — Pydantic models for all config. Stored at `~/.nanobot/config.json` or env vars with `NANOBOT_` prefix and `__` nesting. Agent profiles discovered from `agent.yaml` first, fallback to config.
- **`agent/skills.py`** — Discovers `SKILL.md` files in agent/shared/builtin skills dirs. Skills with `always: true` go in system prompt; others listed as XML summary for progressive loading.
- **`agent/tools/`** — Tools implement `Tool` ABC (`name`, `description`, `parameters` JSON schema, `async execute()`). Registered in `ToolRegistry`, filtered by `allowed_tools`.

### WhatsApp Bridge

Separate Node.js process (`bridge/`) using Baileys. Communicates with Python via WebSocket at `ws://localhost:3001`. Bridge handles QR login and WhatsApp Web protocol; Python side (`channels/whatsapp.py`) connects as WS client.

### Cron & Heartbeat

- **`cron/service.py`** — Schedules agent tasks (one-time, interval, cron expression). Jobs stored in `~/.nanobot/data/cron/jobs.json`. Executes via agent's `process_direct()`.
- **`heartbeat/service.py`** — Wakes agent every 30 min, reads `workspace/HEARTBEAT.md` for proactive tasks.

### Subagents

`agent/subagent.py` — Spawns background async tasks with reduced tool set. Announces completion via bus as "system" channel messages routed back to original channel/chat.

### GAR CRM Integration

Nanobot integra con GAR CRM (lavandería) para notificaciones automáticas por WhatsApp. Contrato completo en `docs/contracts/nanobot-gar-integration.md`. GAR tiene su tarjeta de interfaz en `docs/NANOBOT_INTERFACE.md`.

- **Webhook:** `POST /webhook/crm` recibe eventos de GAR (prenda_terminada, pago_asignado, boleta_emitida)
- **Dedup:** Buffer de `crm_mensaje_id` procesados (previene duplicados de Edge Function retries)
- **Routing:** Eventos CRM entran como `crm_event` channel, salen por `whatsapp` via `reply_channel` metadata
- **Supabase:** `integrations/supabase.py` actualiza `crm_mensajes` table (enviado_api/fallido)
- **Media:** `EvolutionChannel._send_media()` soporta PDFs vía Evolution API `sendMedia` endpoint
- **Tabla compartida:** `crm_mensajes` — GAR crea registro (`pendiente`), Nanobot actualiza estado final
- **Evolution API:** Puerto `8085` (host), Docker container interno en `8080`

## Docker

Archivos: `Dockerfile`, `docker-compose.yml`, `.env.example`

```bash
# 1. Copiar y configurar env vars
cp .env.example .env        # editar con tus API keys

# 2. Construir y levantar
docker compose build         # construye imagen (~Python 3.12 + Node.js 20)
docker compose up -d         # levanta gateway (Telegram/WhatsApp/Feishu)

# 3. Logs y status
docker compose logs -f       # ver logs en tiempo real
docker exec nanobot nanobot status

# 4. Mensaje directo (sin gateway)
docker compose run --rm nanobot agent -m "Hello!"
```

**Persistencia:** El volumen `nanobot-data` monta en `/root/.nanobot` y contiene config, workspace, sessions y cron jobs.

**Configuración:** Sin `config.json`, Pydantic Settings lee env vars con prefijo `NANOBOT_` y separador `__` (ej: `NANOBOT_PROVIDERS__ANTHROPIC__API_KEY`). Si existe `config.json` en el volumen, tiene prioridad sobre env vars.

**Servicios locales desde el contenedor:** Usar `host.docker.internal` para conectar a APIs corriendo en el host (ej: `NANOBOT_PROVIDERS__VLLM__API_BASE=http://host.docker.internal:8000/v1`).

## Conventions

- Python 3.11+, async-first everywhere (tools, channels, bus)
- Ruff for linting: line-length 100, rules E/F/I/N/W
- All tools return strings; errors caught in registry and returned as text
- Channel permission: `allow_from` list per channel (empty = allow all)
- Config uses camelCase in JSON, snake_case in Python (loader handles conversion)
- Provider model names are prefixed for LiteLLM mode (e.g., `anthropic/claude-opus-4-5`), arbitrary for OpenAI SDK mode
- Build system: hatchling; bridge included in wheel via `force-include`
