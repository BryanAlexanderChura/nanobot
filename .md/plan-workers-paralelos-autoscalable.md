# Plan De Migracion Minima A Workers Paralelos Autoescalables

## Objetivo
Migrar `nanobot` desde el modelo actual de proceso unico hacia un modelo con cola + workers paralelos autoescalables, manteniendo compatibilidad funcional y tocando el minimo codigo posible.

## Requisitos Que Debe Cumplir El Cambio
- Misma UX para usuario final en Telegram/WhatsApp/CLI.
- Soporte para multiples workers en paralelo.
- Coherencia por conversacion: `1 conversacion = 1 lock`.
- Mantener modo actual como default para no romper instalaciones existentes.
- Cambios acotados y reversibles por feature flag.

## Estado Actual (Resumen Tecnico)
- `MessageBus` es local en memoria (`nanobot/bus/queue.py`).
- `gateway` ejecuta canales + `agent.run()` en el mismo proceso (`nanobot/cli/commands.py`).
- El procesamiento es basicamente secuencial global por un consumidor principal.
- Sesiones se guardan en archivos JSONL locales (`nanobot/session/manager.py`).

## Estrategia De Minimos Cambios
1. Mantener interfaces actuales (`publish_inbound`, `consume_inbound`, `publish_outbound`, `consume_outbound`) para no reescribir canales ni loop.
2. Introducir modo distribuido por config, dejando modo local intacto como default.
3. Agregar nuevos componentes como archivos nuevos, con pocos cambios en archivos existentes.
4. Activar lock por conversacion solo en procesamiento, no en canales.
5. Hacer rollout gradual: local -> distribuido con 1 worker -> N workers.

## Arquitectura Objetivo (Compatibilidad Primero)

### Componentes
- `Gateway`:
  - Sigue recibiendo mensajes de canales.
  - Publica inbound en cola compartida (Redis en modo distribuido).
  - Consume outbound y envia a canales.
  - Puede correr 1 worker embebido para mantener compatibilidad (igual que hoy).
- `Workers`:
  - Solo consumen inbound, ejecutan `AgentLoop`, publican outbound.
  - Escalan horizontalmente.
- `Lock Manager`:
  - Garantiza exclusividad por `session_key` (`channel:chat_id`).
  - Implementacion Redis (`SET NX PX`) en modo distribuido.

### Flujo
1. Canal recibe mensaje y publica `InboundMessage`.
2. Worker toma mensaje.
3. Worker adquiere lock por `session_key`.
4. Worker procesa con `AgentLoop`.
5. Worker publica `OutboundMessage`.
6. Gateway despacha respuesta al canal correspondiente.
7. Worker libera lock.

## Fases De Implementacion

## Fase 0 - Guardrails Y Paridad (sin cambiar comportamiento)
Objetivo: preparar base para migracion sin impacto funcional.

Cambios:
- Agregar pruebas de regresion de flujo actual (local).
- Corregir bug de sesion directa:
  - `AgentLoop.process_direct()` hoy recibe `session_key` pero no lo usa.
  - Hacer que use `session_key` real para CLI/cron/heartbeat.

Archivos:
- `nanobot/agent/loop.py`
- `tests/` (nuevos tests de session routing)

Riesgo: bajo.

## Fase 1 - Abstraccion De Bus Con Cero Ruptura
Objetivo: permitir backend local o Redis sin tocar canales.

Cambios:
- Mantener `MessageBus` actual.
- Crear `RedisMessageBus` con misma API publica.
- Crear factory de bus por config (`local` por defecto, `redis` opcional).

Archivos nuevos:
- `nanobot/bus/redis_queue.py`
- `nanobot/bus/factory.py`

Archivos editados:
- `nanobot/config/schema.py` (nueva seccion `bus`)
- `nanobot/cli/commands.py` (usar factory en vez de instanciar `MessageBus()` directo)

Riesgo: bajo, porque default sigue local.

## Fase 2 - Lock Por Conversacion (1 conversacion = 1 lock)
Objetivo: coherencia de respuestas con workers paralelos.

Cambios:
- Introducir `ConversationLockManager` (interfaz simple).
- Implementaciones:
  - `LocalConversationLockManager` con `asyncio.Lock` por `session_key`.
  - `RedisConversationLockManager` con lock distribuido.
- En `AgentLoop.run()`, envolver `_process_message(msg)` en acquire/release del lock.

Clave de lock:
- `nanobot:lock:{session_key}`
- TTL recomendado inicial: 120s, con renovacion opcional si el turno dura mas.

Archivos nuevos:
- `nanobot/session/locks.py`

Archivos editados:
- `nanobot/agent/loop.py` (inyeccion y uso de lock manager)
- `nanobot/cli/commands.py` (inyectar lock manager segun config)
- `nanobot/config/schema.py` (config de lock: enabled, ttl_ms)

Riesgo: medio-bajo.
Mitigacion: fallback a lock local y timeout defensivo.

## Fase 3 - Worker Dedicado Y Autoescalado
Objetivo: separar consumo de mensajes en procesos replicables.

Cambios:
- Nuevo comando `nanobot worker`:
  - Inicializa provider + bus + lock manager.
  - Corre `AgentLoop.run()` sin canales.
- `nanobot gateway`:
  - Mantiene canales + outbound dispatcher.
  - En modo distribuido, habilitar flag `run_embedded_worker` (default `true` para no romper).
  - Permitir apagar worker embebido cuando haya workers externos.

Archivos:
- `nanobot/cli/commands.py`
- Opcional nuevo: `nanobot/worker/service.py` (si se quiere encapsular startup).

Riesgo: medio.
Mitigacion: default sigue equivalente a hoy (`gateway` con worker embebido).

## Fase 4 - Sesion Compartida Para Escalado Real
Objetivo: coherencia historica entre multiples instancias.

Cambios minimos recomendados:
- Introducir backend de sesiones compartido manteniendo API de `SessionManager`.
- Opcion A (minimo): Redis para historia corta de chat.
- Opcion B (mejor largo plazo): Postgres/Supabase para historial persistente.

Decision pragmatica:
- MVP de escalado: Redis SessionManager.
- Fase posterior: migrar a Supabase sin romper interfaz.

Archivos:
- Nuevo `nanobot/session/redis_manager.py` (o `session/backends/redis.py`)
- Editar `nanobot/agent/loop.py` para recibir manager por inyeccion.
- Editar `nanobot/cli/commands.py` para factory de session manager.

Riesgo: medio.
Mitigacion: flag de backend (`file` default, `redis` opcional).

## Fase 5 - Despliegue Y Operacion
Objetivo: autoescalado controlado y reversible.

### Deploy base recomendado
- 1 replica `gateway` (canales + dispatcher outbound).
- N replicas `worker` (CPU/mem autoscaling).
- Redis administrado para cola + locks.
- Storage persistente para memoria larga (cuando se active).

### Politicas operativas
- Liveness/readiness probes por proceso.
- Observabilidad:
  - profundidad de cola
  - latencia por turno
  - lock wait time
  - errores por tool/provider
- Retry con backoff en worker.

### Rollback
- Cambiar `bus.backend=local`.
- Desactivar workers externos.
- Dejar solo modo actual monolitico.

## Cambios Concretos Por Archivo (Estimacion)
- `nanobot/config/schema.py`: +35 a +60 lineas.
- `nanobot/cli/commands.py`: +70 a +120 lineas.
- `nanobot/agent/loop.py`: +40 a +80 lineas.
- `nanobot/bus/factory.py` (nuevo): +20 a +35 lineas.
- `nanobot/bus/redis_queue.py` (nuevo): +120 a +220 lineas.
- `nanobot/session/locks.py` (nuevo): +80 a +140 lineas.
- `nanobot/session/redis_manager.py` (nuevo, fase 4): +120 a +220 lineas.

Total MVP (fases 0-3, sin session distribuida): ~300-500 lineas.
Total con fase 4: ~450-750 lineas.

## Compatibilidad Funcional (No Romper Bot)
- Modo default sigue siendo local.
- Misma estructura de mensajes `InboundMessage/OutboundMessage`.
- Canales existentes no cambian contrato.
- Cron/Heartbeat siguen funcionando:
  - Inicialmente con worker embebido activo en gateway.
  - Luego se puede mover a worker dedicado con lock de lider.

## Riesgos Y Mitigaciones
| Riesgo | Impacto | Mitigacion |
|---|---|---|
| Deadlock o lock huérfano | Alto | TTL + release en `finally` + metricas de lock |
| Reorden por alta concurrencia | Medio | lock por `session_key` + consumo con ack controlado |
| Duplicados por retry | Medio | idempotency key por `message_id/session_key/timestamp` |
| Ruptura de flujo actual | Alto | feature flags + modo local default + rollout canary |
| Historial inconsistente multi-instancia | Alto | fase 4 con session backend compartido |

## Criterios De Aceptacion
- `gateway` en modo local funciona igual que hoy.
- En modo distribuido con 3 workers:
  - 50 usuarios concurrentes sin mezclar conversaciones.
  - Ninguna conversacion procesada en paralelo por mas de un worker.
  - Latencia p95 dentro del objetivo definido.
- Escalado horizontal agregando workers sin cambios de codigo.
- Rollback a modo local en menos de 5 minutos.

## Plan De Ejecucion Recomendado (Orden)
1. Fase 0: paridad + bugfix session_key.
2. Fase 1: bus factory + redis bus (flag apagado).
3. Fase 2: lock manager en `AgentLoop`.
4. Fase 3: comando `nanobot worker` + deploy 1 gateway + 1 worker.
5. Subir a 3-5 workers y validar carga.
6. Fase 4: session backend compartido para escalado completo.

## Notas Finales
- Este plan minimiza cambios en contratos existentes.
- Se prioriza compatibilidad y rollback rapido.
- Redis es suficiente para arrancar concurrencia real.
- Supabase/Postgres se vuelve importante al consolidar memoria de largo plazo y analitica.
