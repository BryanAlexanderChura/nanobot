# Levantar Nanobot en local

Skill para construir y levantar el proyecto nanobot en el entorno local (Docker o directo con uv).

## Instrucciones

Cuando el usuario invoque esta skill, seguir estos pasos en orden:

### 1. Verificar Docker

```bash
docker info 2>&1 | head -3
```

Si Docker no responde, intentar iniciarlo:
```bash
"/c/Program Files/Docker/Docker/Docker Desktop.exe" &>/dev/null &
```
Esperar hasta 90s con:
```bash
for i in $(seq 1 30); do docker info &>/dev/null && echo "Docker ready!" && break || sleep 3; done
```

### 2. Build

```bash
cd /c/Users/Bryan/OneDrive/Documentos/GitHub/nanobot && docker compose build
```

### 3. Verificar que levanta

```bash
docker compose run --rm nanobot status
```

Nota: el entrypoint ya incluye `nanobot`, NO duplicar (usar `nanobot status`, no `nanobot nanobot status`).

### 4. Test rápido

```bash
docker compose run --rm nanobot agent -m "Responde OK si funcionas"
```

### 5. Gateway (si el usuario lo pide)

Para levantar en modo gateway (Telegram/WhatsApp/Feishu):
```bash
docker compose up -d
docker compose logs -f
```

## Alternativa sin Docker (uv)

Si el usuario prefiere sin Docker:

```bash
cd /c/Users/Bryan/OneDrive/Documentos/GitHub/nanobot
uv run nanobot status
uv run nanobot agent -m "Responde OK si funcionas"
```

Para gateway:
```bash
uv run nanobot gateway
```

## Notas

- Config en `~/.nanobot/config.json` (si no existe, se crea con `nanobot onboard`)
- Env vars con prefijo `NANOBOT_` y separador `__` (ej: `NANOBOT_PROVIDERS__ANTHROPIC__API_KEY`)
- El volumen Docker `nanobot-data` persiste en `/root/.nanobot`
- Para conectar a APIs locales desde Docker: usar `host.docker.internal`
