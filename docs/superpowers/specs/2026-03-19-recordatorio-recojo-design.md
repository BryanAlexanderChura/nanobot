# Recordatorio de recojo — Design Spec

> Versión: 1.0 | Fecha: 2026-03-19 | Estado: Propuesta

---

## Objetivo

Cuando un pedido lleva más de 2 días listo sin ser recogido, enviar recordatorios escalonados al cliente por WhatsApp. Máximo 3 recordatorios por pedido (día 2, 5 y 10). Usa templates sin LLM.

## Contexto de negocio

- Los clientes a veces olvidan recoger sus prendas
- La boleta indica que prendas no recogidas en 30 días pueden ser dispuestas por la empresa
- Los 30 días se cuentan desde `fecha_entrega` (fecha estimada de entrega del pedido)
- **Migración futura:** cambiar referencia a la fecha real en que se envió la notificación de `prenda_terminada` (más precisa que `fecha_entrega` que es una estimación)

## Trigger

Un **cron job de Supabase** (pg_cron) ejecuta diariamente (ej: 9:00 AM hora local) una llamada a la Edge Function `recordatorio-recojo`.

## Niveles de recordatorio

| Nivel | Días desde referencia | Tono | Contenido |
|-------|----------------------|------|-----------|
| 1 | ≥ 2 | Amable | "Tus prendas están listas, te esperamos" |
| 2 | ≥ 5 | Recordatorio | "Tus prendas llevan unos días esperándote" |
| 3 | ≥ 10 | Informativo | Menciona política de 30 días con fecha límite calculada |

## Fecha de referencia

Configurable en la query de la Edge Function:

```sql
-- Opción actual: fecha_entrega (estimación)
WHERE fecha_entrega + interval 'X days' <= now()

-- Opción futura: fecha de notificación prenda_terminada (más precisa)
-- WHERE (SELECT created_at FROM crm_mensajes WHERE pedido_id = p.codigo
--        AND metadata->>'event_type' = 'prenda_terminada' LIMIT 1) + interval 'X days' <= now()
```

El cambio de referencia es solo ajustar la query, no reescribir lógica.

## Fecha límite (para recordatorio #3)

Se calcula como `fecha_entrega + 30 días`, formateada en español: "Domingo 19 de Abril de 2026". Coherente con lo que dice la boleta impresa.

## Flujo técnico

```
pg_cron (diario, 9:00 AM)
  → Llama Edge Function recordatorio-recojo
  → Query: pedidos terminados no entregados, con fecha_entrega + X días <= hoy
  → Para cada pedido, cuenta recordatorios ya enviados (crm_mensajes)
  → Si nivel actual > recordatorios enviados:
    → Selecciona template según nivel
    → Renderiza con datos del cliente/pedido
    → Crea crm_mensajes (tipo: automatico_nanobot, event_type: recordatorio_recojo)
    → POST /webhook/crm con template_sugerido
  → Nanobot recibe, skip LLM, envía por WhatsApp
```

## Payload enviado a /webhook/crm

```jsonc
{
  "event": "recordatorio_recojo",
  "timestamp": "2026-03-19T14:00:00.000Z",
  "sucursal_id": "uuid-sucursal",
  "data": {
    "cliente": {
      "cliente_id": "C-000123",
      "nombre": "María López García",
      "nombre_preferido": "Marita",
      "telefono_whatsapp": "+51987654321",  // REQUERIDO por webhook
      "whatsapp_opt_in": true
    },
    "pedido": {
      "codigo": "B001-4",
      "importe": 45.00,
      "fecha_entrega": "2026-03-15"
    },
    "crm_mensaje_id": "uuid-nuevo-cada-vez",  // REQUERIDO: siempre un ID fresco
    "template_sugerido": {
      "contenido_renderizado": "¡Hola Marita! Tus prendas...\n|||\nTe esperamos..."
    }
  }
}
```

**Importante:** Cada recordatorio (cada nivel, cada ejecución del cron) inserta un **nuevo registro** en `crm_mensajes` con su propio `id`. Nunca se reutilizan registros entre ejecuciones del cron.

## Control de duplicados

La Edge Function cuenta registros en `crm_mensajes` donde:
- `pedido_id = X`
- `metadata->>'event_type' = 'recordatorio_recojo'`

| Recordatorios enviados | Acción |
|----------------------|--------|
| 0 y días ≥ 2 | Envía nivel 1 |
| 1 y días ≥ 5 | Envía nivel 2 |
| 2 y días ≥ 10 | Envía nivel 3 |
| 3 | No envía más |

## Templates

Definidos en la Edge Function. 2-3 variantes por nivel con rotación (`ultimo_indice_plantilla`).

### Nivel 1 — Amable (día 2+)

```
¡Hola {nombre}! Te recordamos que tus prendas del pedido {codigo} ya están listas para recoger 😊
|||
Te esperamos en horario de atención. ¡Será un gusto atenderte!
```

```
¡Hola {nombre}! Tus prendas están listas y te esperan 👕✨
|||
Puedes pasar a recogerlas cuando gustes. ¡Te esperamos!
```

### Nivel 2 — Recordatorio (día 5+)

```
¡Hola {nombre}! Tus prendas del pedido {codigo} llevan unos días esperándote 😊
|||
Recuerda que puedes pasar a recogerlas en nuestro horario de atención.
```

```
¡Hola {nombre}! Solo un recordatorio amable: tus prendas del pedido {codigo} siguen aquí listas para ti 🙌
|||
¡Te esperamos!
```

### Nivel 3 — Informativo (día 10+)

```
¡Hola {nombre}! Queremos recordarte que tus prendas del pedido {codigo} siguen en nuestro local.
|||
Te informamos que según nuestra política, las prendas no retiradas dentro de los 30 días posteriores a la fecha de recojo (hasta el {fecha_limite}) podrán ser dispuestas por la empresa. ¡Te esperamos pronto!
```

```
¡Hola {nombre}! Tus prendas del pedido {codigo} te están esperando.
|||
Recuerda que tienes plazo hasta el {fecha_limite} para recogerlas, según la política indicada en tu boleta. ¡Pasa cuando puedas!
```

Variables de template:
- `{nombre}` — nombre_preferido o nombre del cliente
- `{codigo}` — código del pedido
- `{fecha_limite}` — `fecha_entrega + 30 días`, formateada como "Domingo 19 de Abril de 2026"
- `{dias}` — días que llevan las prendas listas (opcional)

## Cambios por repo

### GAR

| Archivo | Cambio |
|---------|--------|
| `supabase/functions/recordatorio-recojo/index.ts` | **Crear:** Edge Function que consulta pedidos, cuenta recordatorios, renderiza templates, POST a Nanobot |
| Supabase Dashboard → Database → Cron Jobs | **Crear:** pg_cron que llama a la Edge Function diariamente |

### Nanobot

**Ningún cambio necesario.** El webhook `/webhook/crm` ya acepta cualquier evento con `template_sugerido`. El bypass de LLM, dedup, y envío por Evolution API ya están implementados.

## Insert en crm_mensajes

Cada recordatorio inserta un registro con esta estructura:

```typescript
{
  cliente_id: cliente.cliente_id,
  pedido_id: pedido.codigo,
  sucursal_id: pedido.sucursal_id,
  canal: 'whatsapp',
  tipo: 'automatico_nanobot',
  mensaje_renderizado: rendered,  // template renderizado
  estado_envio: 'pendiente',
  telefono_destino: phone,
  metadata: {
    auto_generated: true,
    generated_at: new Date().toISOString(),
    event_type: 'recordatorio_recojo',
    nivel: 1,  // 1, 2 o 3
    cliente_nombre: cliente.nombre,
  },
}
```

## Filtros de la query

La Edge Function consulta pedidos que cumplan TODOS estos criterios:
- `estado = 'terminado'` (listo pero no recogido)
- Cliente tiene `telefono_whatsapp` y `whatsapp_opt_in != false`
- `fecha_entrega` no es null
- `fecha_entrega + 2 días <= hoy` (mínimo 2 días desde fecha de entrega)

**Prerequisito:** GAR debe actualizar `pedidos.estado` a `'entregado'` cuando el cliente recoge. Si no se actualiza, los recordatorios se seguirían enviando (hasta el tope de 3) incluso después de recoger. Verificar que este flujo existe en GAR.

## Cron job — Timezone

Supabase pg_cron usa UTC por defecto. Perú es UTC-5. Para que ejecute a las 9:00 AM hora de Lima:

```sql
SELECT cron.schedule_in_timezone(
  'recordatorio-recojo',
  '0 9 * * *',           -- 9:00 AM
  'America/Lima',         -- Zona horaria de Perú
  $$SELECT net.http_post(
    url := current_setting('app.supabase_url') || '/functions/v1/recordatorio-recojo',
    headers := jsonb_build_object('Authorization', 'Bearer ' || current_setting('app.supabase_anon_key')),
    body := '{}'::jsonb
  )$$
);
```

Si `schedule_in_timezone` no está disponible, usar `'0 14 * * *'` (14:00 UTC = 9:00 AM Lima).

## Consideraciones

- **Horario:** El cron corre a las 9:00 AM hora de Lima para que los mensajes lleguen en horario razonable
- **Volumen:** Si hay muchos pedidos pendientes, la Edge Function los procesa en lote secuencial (no en paralelo, para no saturar Nanobot)
- **Opt-out:** Respeta `whatsapp_opt_in = false` del cliente
- **Pedidos sin fecha_entrega:** Se omiten (no se puede calcular días)
- **No importa precisión exacta:** Unos minutos/horas de diferencia es aceptable (cron diario)
- **Session history:** Los mensajes con template bypass no se guardan en el historial de sesión del agente. Si el cliente responde a un recordatorio, el agente lo trata como conversación nueva sin contexto del recordatorio enviado. Esto es aceptable para esta fase.

## Lo que NO cambia

- Webhook endpoint `/webhook/crm` (mismo)
- Auth Bearer token (mismo)
- Dedup `crm_mensaje_id` (mismo)
- Template bypass en AgentLoop (mismo)
- `|||` splitting (mismo)
- Supabase `mark_sent`/`mark_failed` (mismo)

## Testing

1. **Manual:** Llamar Edge Function directamente con un pedido de prueba que tenga `fecha_entrega` de hace 3 días
2. **E2E:** Verificar que llega WhatsApp con template correcto
3. **Dedup:** Llamar dos veces → segundo no debería enviar (ya hay 1 recordatorio en crm_mensajes)
4. **Escalonamiento:** Simular pedido con fecha_entrega de hace 6 días → debería enviar nivel 2 (asumiendo nivel 1 ya existe en crm_mensajes)
