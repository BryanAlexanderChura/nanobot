# Configuración completa — Nanobot + Evolution API + GAR

## Entorno local (actual)

### Variables de entorno — Nanobot (`.env`)

| Variable                                           | Valor                                        | Descripción                    |
| -------------------------------------------------- | -------------------------------------------- | ------------------------------- |
| `NANOBOT_AGENTS__DEFAULTS__PROVIDER`             | `openai`                                   | Proveedor LLM                   |
| `NANOBOT_AGENTS__DEFAULTS__MODEL`                | `gpt-4o-mini`                              | Modelo LLM                      |
| `NANOBOT_PROVIDERS__OPENAI__API_KEY`             | `sk-proj-...`                              | API key de OpenAI               |
| `NANOBOT_AGENTS__DEFAULTS__WORKSPACE`            | `C:/Users/fanny/.../Open Claw`             | Carpeta de workspace            |
| `NANOBOT_AGENTS__DEFAULTS__TEMPERATURE`          | `0.3`                                      | Creatividad del modelo          |
| `NANOBOT_AGENTS__DEFAULTS__MAX_TOKENS`           | `4096`                                     | Máximo de tokens por respuesta |
| `NANOBOT_AGENTS__DEFAULTS__THINKING`             | `false`                                    | Modo thinking (solo Anthropic)  |
| `NANOBOT_CHANNELS__WHATSAPP__ENABLED`            | `true`                                     | Activa canal WhatsApp           |
| `NANOBOT_CHANNELS__WHATSAPP__PROVIDER`           | `evolution`                                | Usa Evolution API (no Baileys)  |
| `NANOBOT_CHANNELS__WHATSAPP__EVOLUTION_API_URL`  | `http://localhost:8085`                    | URL de Evolution API            |
| `NANOBOT_CHANNELS__WHATSAPP__EVOLUTION_API_KEY`  | `nanobot-evo-local-key`                    | Clave de Evolution API          |
| `NANOBOT_CHANNELS__WHATSAPP__EVOLUTION_INSTANCE` | `nanobot-test`                             | Nombre de la instancia WhatsApp |
| `NANOBOT_CHANNELS__WHATSAPP__ALLOW_FROM`         | `[]`                                       | Vacío = acepta de todos        |
| `NANOBOT_GATEWAY__WEBHOOK_ENABLED`               | `true`                                     | Activa servidor webhook         |
| `NANOBOT_GATEWAY__WEBHOOK_SECRET`                | `nanobot-gar-webhook-secret-2026`          | Secret compartido con GAR       |
| `NANOBOT_TOOLS__SUPABASE__URL`                   | `https://sxnfccqpjxoipptgsowu.supabase.co` | Proyecto Supabase               |
| `NANOBOT_TOOLS__SUPABASE__SERVICE_KEY`           | `eyJ...`                                   | Service role key de Supabase    |

### Variables de entorno — Evolution API (Docker)

Configuradas en `docker-compose.evolution.yml`. Solo necesitas sobreescribirlas si cambias de defaults:

| Variable                   | Default                                                 | Descripción                    |
| -------------------------- | ------------------------------------------------------- | ------------------------------- |
| `EVOLUTION_HOST_PORT`    | `8085`                                                | Puerto en el host               |
| `EVOLUTION_API_KEY`      | `nanobot-evo-local-key`                               | Clave de autenticación         |
| `EVOLUTION_SERVER_URL`   | `http://localhost:8085`                               | URL pública de Evolution       |
| `EVOLUTION_DATABASE_URL` | `postgresql://evo:evo123@whatsapp-db:5432/evolution`  | DB interna                      |
| `EVOLUTION_REDIS_URL`    | `redis://whatsapp-cache:6379`                         | Cache interno                   |
| `EVOLUTION_WEBHOOK_URL`  | `http://host.docker.internal:18790/webhook/evolution` | Donde envía webhooks a Nanobot |

### Variables de entorno — GAR (`.env`)

| Variable                          | Valor                                        | Descripción      |
| --------------------------------- | -------------------------------------------- | ----------------- |
| `VITE_SUPABASE_PROJECT_ID`      | `sxnfccqpjxoipptgsowu`                     | Proyecto Supabase |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | `eyJ...anon...`                            | Anon key          |
| `VITE_SUPABASE_URL`             | `https://sxnfccqpjxoipptgsowu.supabase.co` | URL Supabase      |

### Secrets de Supabase Edge Function

Configurados en Supabase Dashboard → Edge Functions → Secrets:

| Secret                     | Valor                                                | Cambia?                                           |
| -------------------------- | ---------------------------------------------------- | ------------------------------------------------- |
| `NANOBOT_WEBHOOK_URL`    | `https://TUNNEL-URL.trycloudflare.com/webhook/crm` | **SÍ**, cada vez que se reinicia el tunnel |
| `NANOBOT_WEBHOOK_SECRET` | `nanobot-gar-webhook-secret-2026`                  | No                                                |

### Tabla `whatsapp_config` (Supabase)

| Campo          | Valor         | Efecto                                |
| -------------- | ------------- | ------------------------------------- |
| `modo_envio` | `api`       | Nanobot envía automáticamente       |
| `modo_envio` | `abrir_app` | Manual (crea pendiente para operario) |

### Puertos

| Servicio               | Puerto                                 | Protocolo   |
| ---------------------- | -------------------------------------- | ----------- |
| Nanobot Gateway        | `18790`                              | HTTP        |
| Evolution API          | `8085` (host) / `8080` (container) | HTTP        |
| GAR CRM                | `8080`                               | HTTP (Vite) |
| PostgreSQL (Evolution) | `5432` (solo Docker interno)         | TCP         |
| Redis (Evolution)      | `6379` (solo Docker interno)         | TCP         |

### Volúmenes Docker (persistentes entre reinicios)

| Volumen               | Contenido                    |
| --------------------- | ---------------------------- |
| `whatsapp_sessions` | Sesiones WhatsApp (QR, auth) |
| `whatsapp_pgdata`   | Base de datos de Evolution   |
| `whatsapp_cache`    | Cache Redis                  |

---

## Migración a VPS — Qué cambia

### Lo que DESAPARECE

| Componente                                          | Razón                                                  |
| --------------------------------------------------- | ------------------------------------------------------- |
| Cloudflare tunnel                                   | Se reemplaza con IP fija o dominio                      |
| `PYTHONIOENCODING=utf-8`                          | Solo necesario en Windows, Linux no tiene este problema |
| Puerto 8080 de GAR local                            | GAR se deploya en Lovable/Vercel, no corre local        |
| Actualizar `NANOBOT_WEBHOOK_URL` en cada reinicio | URL fija del VPS                                        |

### Lo que CAMBIA

| Config                                            | Local                                                   | VPS                                                                                                        |
| ------------------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `NANOBOT_CHANNELS__WHATSAPP__EVOLUTION_API_URL` | `http://localhost:8085`                               | `http://localhost:8085` (misma máquina) o `http://evolution-api:8080` (Docker network)                |
| `EVOLUTION_SERVER_URL`                          | `http://localhost:8085`                               | `https://tu-dominio.com:8085` o IP pública                                                              |
| `EVOLUTION_WEBHOOK_URL`                         | `http://host.docker.internal:18790/webhook/evolution` | `http://nanobot:18790/webhook/evolution` (Docker network) o `http://localhost:18790/webhook/evolution` |
| `NANOBOT_WEBHOOK_URL` (Supabase secret)         | `https://TUNNEL.trycloudflare.com/webhook/crm`        | `https://tu-dominio.com:18790/webhook/crm` (fijo, no cambia)                                             |
| Nanobot execution                                 | `uv run nanobot gateway`                              | Docker:`docker compose up -d` o systemd service                                                          |
| GAR                                               | `localhost:8080` (npm run dev)                        | Lovable deploy o Vercel                                                                                    |

### Lo que se AGREGA en VPS

| Componente                              | Descripción                                                                                                                                      |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Reverse proxy (Caddy o Nginx)** | HTTPS + routing. Caddy auto-genera certificados SSL. Ejemplo:`tu-dominio.com:18790 → nanobot:18790`, `tu-dominio.com:8085 → evolution:8080` |
| **docker-compose unificado**      | Un solo `docker-compose.yml` con Nanobot + Evolution API + PostgreSQL + Redis en la misma red Docker                                            |
| **SSL/HTTPS**                     | Obligatorio para webhooks de Supabase Edge Functions (no acepta HTTP plano)                                                                       |
| **Process management**            | Docker `restart: unless-stopped` o systemd para auto-restart                                                                                    |
| **Firewall**                      | Abrir solo puertos necesarios: 443 (HTTPS), 18790 (webhook), 8085 (Evolution manager)                                                             |
| **Monitoreo**                     | Logs:`docker compose logs -f`. Opcional: Grafana/Uptime Kuma                                                                                    |

### Ejemplo de docker-compose unificado para VPS

```yaml
services:
  nanobot:
    build: .
    container_name: nanobot
    restart: unless-stopped
    ports:
      - "18790:18790"
    volumes:
      - nanobot-data:/root/.nanobot
    env_file:
      - .env
    depends_on:
      - whatsapp-api

  whatsapp-api:
    image: evoapicloud/evolution-api:v2.3.7
    container_name: whatsapp_api
    restart: unless-stopped
    ports:
      - "8085:8080"
    environment:
      - AUTHENTICATION_API_KEY=${EVOLUTION_API_KEY}
      - SERVER_URL=https://tu-dominio.com:8085
      - DATABASE_CONNECTION_URI=postgresql://evo:evo123@whatsapp-db:5432/evolution
      - WEBHOOK_GLOBAL_URL=http://nanobot:18790/webhook/evolution
      # ... resto igual
    depends_on:
      whatsapp-db:
        condition: service_healthy

  whatsapp-db:
    image: postgres:15-alpine
    # ... igual que ahora

  whatsapp-cache:
    image: redis:7-alpine
    # ... igual que ahora
```

**Diferencia clave:** En VPS, `WEBHOOK_GLOBAL_URL` usa `http://nanobot:18790` (nombre del container en la red Docker) en vez de `host.docker.internal`. Y `NANOBOT_CHANNELS__WHATSAPP__EVOLUTION_API_URL` puede ser `http://whatsapp-api:8080` si Nanobot también corre en Docker.

### .env para VPS

```bash
# Cambia respecto a local:
NANOBOT_CHANNELS__WHATSAPP__EVOLUTION_API_URL=http://whatsapp-api:8080  # Red Docker interna
EVOLUTION_SERVER_URL=https://tu-dominio.com:8085                        # URL pública
EVOLUTION_WEBHOOK_URL=http://nanobot:18790/webhook/evolution            # Red Docker interna

# Lo demás igual (API keys, Supabase, etc.)
```

### Checklist de migración a VPS

1. [ ] VPS con Docker instalado (Ubuntu 22+ recomendado)
2. [ ] Dominio apuntando al VPS (o IP fija)
3. [ ] Clonar repo nanobot en VPS
4. [ ] Copiar `.env` y ajustar URLs (ver tabla arriba)
5. [ ] `docker compose up -d` (compose unificado)
6. [ ] Verificar Evolution API: `curl https://tu-dominio.com:8085/`
7. [ ] Configurar SSL (Caddy auto o certbot)
8. [ ] Actualizar secret `NANOBOT_WEBHOOK_URL` en Supabase → URL fija
9. [ ] Reconectar WhatsApp (QR desde Evolution Manager)
10. [ ] Verificar E2E: crear pedido en GAR → WhatsApp llega
11. [ ] Configurar firewall (solo puertos necesarios)
12. [ ] Opcional: monitoreo y alertas
