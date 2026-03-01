# Plan Minimo (1-5 Usuarios Concurrentes) - Bajo Costo Y Baja Entropia

## Objetivo
Soportar de 1 a 5 usuarios hablando al mismo tiempo, con respuestas coherentes por conversacion, sin Redis/Supabase por ahora y con cambios minimos en codigo.

## Decision Costo-Beneficio
- **No agregar infraestructura nueva** en esta etapa.
- Mantener arquitectura actual en un solo proceso.
- Agregar solo control de concurrencia local + lock por conversacion.

Esto te da mejora real de paralelismo para carga baja sin sobreingenieria.

## Alcance (MVP realista)
- Paralelismo limitado (ej. 3 turnos en paralelo).
- `1 conversacion = 1 lock` para evitar mezclar respuestas.
- Todo sigue funcionando igual en CLI/gateway/channels.

## No Alcance (por ahora)
- No autoescalado horizontal.
- No cola distribuida.
- No memoria compartida entre multiples instancias.

## Cambios Minimos Propuestos

## 1) Corregir bug de sesion directa (prioridad alta)
Problema actual: `process_direct()` recibe `session_key` pero no lo usa.

Archivo:
- `nanobot/agent/loop.py`

Cambio:
- Al crear `InboundMessage` en `process_direct()`, usar el `session_key` para resolver `channel/chat_id` o inyectar session key de forma explicita.

Beneficio:
- Cron/heartbeat/CLI no pisan historial entre conversaciones.

## 2) Concurrencia local controlada (sin romper flujo)
Archivo:
- `nanobot/agent/loop.py`

Cambio:
- Agregar `max_concurrency` (default `1` para compatibilidad).
- Cuando se consume un inbound, procesarlo en tarea async con `Semaphore(max_concurrency)`.

Beneficio:
- Atiende varias conversaciones en paralelo sin reescribir bus/channels.

## 3) Lock por conversacion en memoria
Archivo:
- `nanobot/agent/loop.py` (o nuevo helper pequeno `nanobot/session/locks_local.py`)

Cambio:
- Mapa local `dict[str, asyncio.Lock]` por `session_key`.
- Antes de `_process_message(msg)`, hacer `async with lock_for(msg.session_key)`.

Beneficio:
- Garantiza coherencia por usuario/chat.
- Evita respuestas cruzadas o desorden en misma conversacion.

## 4) Configuracion minima (opcional y simple)
Archivo:
- `nanobot/config/schema.py`

Agregar en `agents.defaults`:
- `max_concurrency: int = 1`
- `session_lock_enabled: bool = True`

Beneficio:
- Puedes ajustar concurrencia sin tocar codigo.

## Estimacion De Cambios
- `nanobot/agent/loop.py`: ~50-90 lineas
- `nanobot/config/schema.py`: ~4-10 lineas
- Tests minimos: ~30-60 lineas

Total estimado: **~90-160 lineas**.

## Pruebas Minimas (sin test suite gigante)
1. Dos mensajes simultaneos de **distintas** conversaciones se procesan en paralelo.
2. Dos mensajes simultaneos de la **misma** conversacion se serializan.
3. Flujo actual con `max_concurrency=1` se comporta igual que antes.
4. `process_direct(session_key=...)` usa sesion correcta.

## Riesgos Y Mitigaciones
| Riesgo | Impacto | Mitigacion |
|---|---|---|
| Condiciones de carrera en sesiones | Medio | Lock por `session_key` |
| Regresion en comportamiento actual | Medio | Default `max_concurrency=1` |
| Saturacion por muchas tareas | Bajo | Limite con `Semaphore` |

## Criterios De Aceptacion
- Soporta 1-5 usuarios concurrentes de forma estable.
- Misma conversacion nunca procesa 2 turnos en paralelo.
- No cambia UX ni comandos existentes.
- Sin dependencias nuevas de infraestructura.

## Cuando recien pasar a Redis/Supabase
Subir a Redis/Supabase solo si ocurre alguno:
1. >10-20 usuarios concurrentes reales sostenidos.
2. Necesitas 2+ replicas del bot.
3. Necesitas memoria compartida fuerte entre instancias.

---

Este plan es el mejor costo-beneficio para tu caso actual: mejoras claras con muy pocas lineas y sin meter complejidad operacional.
