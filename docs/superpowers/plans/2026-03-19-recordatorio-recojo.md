# Recordatorio de recojo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically send escalating WhatsApp reminders (day 2, 5, 10) to customers who haven't picked up their finished orders, using templates without LLM.

**Architecture:** Supabase pg_cron runs daily at 9 AM Lima → calls Edge Function `recordatorio-recojo` → queries pedidos terminados not picked up → counts existing reminders in crm_mensajes → renders template per level → POSTs to Nanobot `/webhook/crm` with `template_sugerido` → Nanobot forwards to WhatsApp (no LLM).

**Tech Stack:** TypeScript/Deno (Supabase Edge Functions), PostgreSQL (pg_cron), Supabase REST API

**Spec:** `docs/superpowers/specs/2026-03-19-recordatorio-recojo-design.md`

**Note:** No Nanobot code changes needed. All tasks are in the GAR repo (`C:\Users\fanny\OneDrive\Documentos\GitHub\gar`).

---

## File Structure

| Repo | File | Responsibility |
|------|------|----------------|
| GAR | `supabase/functions/recordatorio-recojo/index.ts` | **Create:** Edge Function that queries pedidos, counts reminders, renders templates, POSTs to Nanobot |
| GAR | Supabase Dashboard → pg_cron | **Configure:** Daily cron job at 9:00 AM Lima time |

---

## Task 1: Verify prerequisite — pedidos.estado lifecycle

Before building reminders, we need to confirm that GAR updates `pedidos.estado` to `'entregado'` when the customer picks up. Otherwise reminders will fire even after pickup.

**Files:**
- Check: `C:\Users\fanny\OneDrive\Documentos\GitHub\gar\src\hooks\pedidos\useSupabasePedidos.ts`
- Check: `C:\Users\fanny\OneDrive\Documentos\GitHub\gar\src\pages\operaciones\Orders.tsx`

- [ ] **Step 1: Search for 'entregado' in GAR codebase**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
grep -r "entregado" src/ --include="*.ts" --include="*.tsx" -l
```

- [ ] **Step 2: Verify the flow exists**

Confirm there is a button/action in the Orders page that sets `pedidos.estado = 'entregado'` when the customer picks up their order. If it doesn't exist, document this as a blocker.

- [ ] **Step 3: Document finding**

Report: Does the flow exist? If yes, where? If no, what needs to be created?

---

## Task 2: Create Edge Function `recordatorio-recojo`

**Files:**
- Create: `C:\Users\fanny\OneDrive\Documentos\GitHub\gar\supabase\functions\recordatorio-recojo\index.ts`

- [ ] **Step 1: Create the Edge Function**

```typescript
/**
 * Edge Function: recordatorio-recojo
 *
 * Called daily by pg_cron. Queries pedidos with estado='terminado' that
 * haven't been picked up, sends escalating WhatsApp reminders via Nanobot.
 * Uses templates (no LLM). Maximum 3 reminders per pedido (day 2, 5, 10).
 */

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2.56.1';

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
};

// --- Reminder levels: days since fecha_entrega ---
const REMINDER_LEVELS = [
  { nivel: 1, minDays: 2 },
  { nivel: 2, minDays: 5 },
  { nivel: 3, minDays: 10 },
];

// --- Templates per level (rotated via ultimo_indice_plantilla) ---
const TEMPLATES: Record<number, string[]> = {
  1: [
    '¡Hola {nombre}! Te recordamos que tus prendas del pedido {codigo} ya están listas para recoger 😊\n|||\nTe esperamos en horario de atención. ¡Será un gusto atenderte!',
    '¡Hola {nombre}! Tus prendas están listas y te esperan 👕✨\n|||\nPuedes pasar a recogerlas cuando gustes. ¡Te esperamos!',
  ],
  2: [
    '¡Hola {nombre}! Tus prendas del pedido {codigo} llevan unos días esperándote 😊\n|||\nRecuerda que puedes pasar a recogerlas en nuestro horario de atención.',
    '¡Hola {nombre}! Solo un recordatorio amable: tus prendas del pedido {codigo} siguen aquí listas para ti 🙌\n|||\n¡Te esperamos!',
  ],
  3: [
    '¡Hola {nombre}! Queremos recordarte que tus prendas del pedido {codigo} siguen en nuestro local.\n|||\nTe informamos que según nuestra política, las prendas no retiradas dentro de los 30 días posteriores a la fecha de recojo (hasta el {fecha_limite}) podrán ser dispuestas por la empresa. ¡Te esperamos pronto!',
    '¡Hola {nombre}! Tus prendas del pedido {codigo} te están esperando.\n|||\nRecuerda que tienes plazo hasta el {fecha_limite} para recogerlas, según la política indicada en tu boleta. ¡Pasa cuando puedas!',
  ],
};

function renderTemplate(
  templates: string[],
  indice: number,
  vars: Record<string, string>
): { rendered: string; nextIndex: number } {
  const idx = indice % templates.length;
  let rendered = templates[idx];
  for (const [key, value] of Object.entries(vars)) {
    rendered = rendered.replaceAll(`{${key}}`, value);
  }
  return { rendered, nextIndex: idx + 1 };
}

function formatFechaLimite(fechaEntrega: string): string {
  const date = new Date(fechaEntrega);
  date.setDate(date.getDate() + 30);
  const dias = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];
  const meses = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];
  return `${dias[date.getDay()]} ${date.getDate()} de ${meses[date.getMonth()]} de ${date.getFullYear()}`;
}

function daysSince(dateStr: string): number {
  const date = new Date(dateStr);
  const now = new Date();
  return Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60 * 24));
}

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    console.log('🔔 recordatorio-recojo: iniciando escaneo diario');

    const supabase = createClient(
      Deno.env.get('SUPABASE_URL') ?? '',
      Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? ''
    );

    // 1. Query pedidos terminados no entregados con fecha_entrega >= 2 días atrás
    const twoDaysAgo = new Date();
    twoDaysAgo.setDate(twoDaysAgo.getDate() - 2);

    const { data: pedidos, error: pedidosError } = await supabase
      .from('pedidos')
      .select('codigo, cliente_id, sucursal_id, importe, fecha_entrega')
      .eq('estado', 'terminado')
      .not('fecha_entrega', 'is', null)
      .lte('fecha_entrega', twoDaysAgo.toISOString().split('T')[0]);

    if (pedidosError) {
      console.error('❌ Error consultando pedidos:', pedidosError);
      throw new Error(`Error consultando pedidos: ${pedidosError.message}`);
    }

    if (!pedidos || pedidos.length === 0) {
      console.log('✅ No hay pedidos pendientes de recojo');
      return new Response(
        JSON.stringify({ success: true, processed: 0 }),
        { headers: { ...corsHeaders, 'Content-Type': 'application/json; charset=utf-8' } }
      );
    }

    console.log(`📋 ${pedidos.length} pedidos terminados sin recoger`);

    let sent = 0;
    let skipped = 0;

    for (const pedido of pedidos) {
      try {
        // 2. Get cliente
        const { data: cliente, error: clienteError } = await supabase
          .from('clientes')
          .select('cliente_id, nombre, nombre_preferido, telefono_whatsapp, telefono, whatsapp_opt_in, ultimo_indice_plantilla')
          .eq('cliente_id', pedido.cliente_id)
          .single();

        if (clienteError || !cliente) {
          console.warn(`⚠️ Cliente no encontrado para pedido ${pedido.codigo}`);
          skipped++;
          continue;
        }

        // 3. Check phone & opt-in
        const phone = cliente.telefono_whatsapp || cliente.telefono;
        if (!phone || cliente.whatsapp_opt_in === false) {
          skipped++;
          continue;
        }

        // 4. Count existing reminders for this pedido
        const { count: reminderCount } = await supabase
          .from('crm_mensajes')
          .select('*', { count: 'exact', head: true })
          .eq('pedido_id', pedido.codigo)
          .eq('metadata->>event_type', 'recordatorio_recojo');

        const existingReminders = reminderCount || 0;

        // 5. Determine which level to send
        const days = daysSince(pedido.fecha_entrega);
        let targetLevel: number | null = null;

        for (const level of REMINDER_LEVELS) {
          if (days >= level.minDays && existingReminders < level.nivel) {
            targetLevel = level.nivel;
            break;
          }
        }

        if (!targetLevel) {
          skipped++;
          continue;
        }

        // 6. Render template
        const templates = TEMPLATES[targetLevel];
        const nombre = cliente.nombre_preferido || cliente.nombre;
        const fechaLimite = formatFechaLimite(pedido.fecha_entrega);
        const { rendered, nextIndex } = renderTemplate(
          templates,
          cliente.ultimo_indice_plantilla || 0,
          { nombre, codigo: pedido.codigo, fecha_limite: fechaLimite }
        );

        // 7. Update template rotation index
        await supabase
          .from('clientes')
          .update({ ultimo_indice_plantilla: nextIndex })
          .eq('cliente_id', cliente.cliente_id);

        // 8. Create crm_mensajes record
        const { data: crmMsg, error: crmError } = await supabase
          .from('crm_mensajes')
          .insert({
            cliente_id: cliente.cliente_id,
            pedido_id: pedido.codigo,
            sucursal_id: pedido.sucursal_id,
            canal: 'whatsapp' as const,
            tipo: 'automatico_nanobot' as const,
            mensaje_renderizado: rendered,
            estado_envio: 'pendiente' as const,
            telefono_destino: phone,
            metadata: {
              auto_generated: true,
              generated_at: new Date().toISOString(),
              event_type: 'recordatorio_recojo',
              nivel: targetLevel,
              cliente_nombre: cliente.nombre,
              dias_sin_recoger: days,
            },
          })
          .select('id')
          .single();

        if (crmError) {
          console.error(`❌ Error creando crm_mensajes para ${pedido.codigo}:`, crmError);
          skipped++;
          continue;
        }

        // 9. POST to Nanobot
        const nanobotUrl = Deno.env.get('NANOBOT_WEBHOOK_URL');
        const nanobotSecret = Deno.env.get('NANOBOT_WEBHOOK_SECRET');

        if (!nanobotUrl) {
          console.warn('⚠️ NANOBOT_WEBHOOK_URL no configurado');
          skipped++;
          continue;
        }

        const webhookPayload = {
          event: 'recordatorio_recojo',
          timestamp: new Date().toISOString(),
          sucursal_id: pedido.sucursal_id,
          data: {
            cliente: {
              cliente_id: cliente.cliente_id,
              nombre: cliente.nombre,
              nombre_preferido: cliente.nombre_preferido || null,
              telefono_whatsapp: phone,
              whatsapp_opt_in: cliente.whatsapp_opt_in ?? true,
            },
            pedido: {
              codigo: pedido.codigo,
              importe: pedido.importe || 0,
              fecha_entrega: pedido.fecha_entrega,
            },
            crm_mensaje_id: crmMsg.id,
            template_sugerido: {
              contenido_renderizado: rendered,
            },
          },
        };

        try {
          const nanobotResp = await fetch(nanobotUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${nanobotSecret}`,
            },
            body: JSON.stringify(webhookPayload),
          });

          if (!nanobotResp.ok) {
            const errorText = await nanobotResp.text();
            console.error(`❌ Nanobot respondió ${nanobotResp.status}: ${errorText}`);
          } else {
            console.log(`✅ Recordatorio nivel ${targetLevel} enviado: pedido=${pedido.codigo} crm_id=${crmMsg.id}`);
            sent++;
          }
        } catch (fetchError) {
          console.error(`❌ Error conectando a Nanobot para ${pedido.codigo}:`, fetchError);
        }

        // Small delay between sends to avoid flooding
        await new Promise(resolve => setTimeout(resolve, 1000));

      } catch (err) {
        console.error(`❌ Error procesando pedido ${pedido.codigo}:`, err);
        skipped++;
      }
    }

    console.log(`🔔 recordatorio-recojo: ${sent} enviados, ${skipped} omitidos de ${pedidos.length} total`);

    return new Response(
      JSON.stringify({ success: true, processed: pedidos.length, sent, skipped }),
      { headers: { ...corsHeaders, 'Content-Type': 'application/json; charset=utf-8' } }
    );

  } catch (error: unknown) {
    const errorMessage = error instanceof Error ? error.message : 'Error desconocido';
    console.error('❌ Error en recordatorio-recojo:', errorMessage);

    return new Response(
      JSON.stringify({ success: false, error: errorMessage }),
      {
        status: 400,
        headers: { ...corsHeaders, 'Content-Type': 'application/json; charset=utf-8' },
      }
    );
  }
});
```

- [ ] **Step 2: Commit**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
git add supabase/functions/recordatorio-recojo/index.ts
git commit -m "feat: add recordatorio-recojo Edge Function with escalating templates"
```

---

## Task 3: Deploy Edge Function and configure cron

- [ ] **Step 1: Deploy Edge Function**

```bash
cd "C:/Users/fanny/OneDrive/Documentos/GitHub/gar"
npx supabase functions deploy recordatorio-recojo --project-ref sxnfccqpjxoipptgsowu
```

- [ ] **Step 2: Configure pg_cron in Supabase SQL Editor**

Run in Supabase SQL Editor:

```sql
-- Enable pg_cron if not already enabled
CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA extensions;

-- Schedule daily at 9:00 AM Lima time (14:00 UTC)
-- Using pg_net to call the Edge Function
SELECT cron.schedule(
  'recordatorio-recojo',
  '0 14 * * *',
  $$SELECT net.http_post(
    url := (SELECT decrypted_secret FROM vault.decrypted_secrets WHERE name = 'supabase_url' LIMIT 1) || '/functions/v1/recordatorio-recojo',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || (SELECT decrypted_secret FROM vault.decrypted_secrets WHERE name = 'supabase_anon_key' LIMIT 1)
    ),
    body := '{}'::jsonb
  )$$
);
```

If the vault approach doesn't work, use the simpler direct URL:

```sql
SELECT cron.schedule(
  'recordatorio-recojo',
  '0 14 * * *',
  $$SELECT net.http_post(
    url := 'https://sxnfccqpjxoipptgsowu.supabase.co/functions/v1/recordatorio-recojo',
    headers := '{"Content-Type": "application/json", "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InN4bmZjY3FwanhvaXBwdGdzb3d1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTY2ODgwMzYsImV4cCI6MjA3MjI2NDAzNn0.P_vkP1PUV1Tyk9qCF-8WYLaOT8lP9faKcl4aaqpxHDA"}'::jsonb,
    body := '{}'::jsonb
  )$$
);
```

- [ ] **Step 3: Verify cron is scheduled**

Run in SQL Editor:
```sql
SELECT * FROM cron.job WHERE jobname = 'recordatorio-recojo';
```
Expected: One row with schedule `0 14 * * *`

---

## Task 4: E2E verification

- [ ] **Step 1: Test Edge Function manually (without cron)**

Call the Edge Function directly to verify it works:

```bash
curl -X POST https://sxnfccqpjxoipptgsowu.supabase.co/functions/v1/recordatorio-recojo \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <ANON_KEY>" \
  -d '{}'
```

Expected: JSON response with `{ success: true, processed: N, sent: N, skipped: N }`

- [ ] **Step 2: Verify via Nanobot webhook test (simulated)**

If no real pedidos qualify, test by sending the payload directly to Nanobot:

```python
import httpx
payload = {
    "event": "recordatorio_recojo",
    "timestamp": "2026-03-19T14:00:00.000Z",
    "sucursal_id": "test-sucursal",
    "data": {
        "cliente": {
            "cliente_id": "TEST-REC-001",
            "nombre": "Johan Escobar",
            "nombre_preferido": "Johan",
            "telefono_whatsapp": "+51928456493",
            "whatsapp_opt_in": True,
        },
        "pedido": {
            "codigo": "TEST-REC-PEDIDO",
            "importe": 40.00,
            "fecha_entrega": "2026-03-15",
        },
        "crm_mensaje_id": "TEST-RECORDATORIO-001",
        "template_sugerido": {
            "contenido_renderizado": "\u00a1Hola Johan! Te recordamos que tus prendas del pedido TEST-REC-PEDIDO ya est\u00e1n listas para recoger \ud83d\ude0a\n|||\nTe esperamos en horario de atenci\u00f3n. \u00a1Ser\u00e1 un gusto atenderte!"
        },
    },
}
resp = httpx.post("http://localhost:18790/webhook/crm", json=payload,
    headers={"Authorization": "Bearer nanobot-gar-webhook-secret-2026"})
print(resp.status_code, resp.json())
```

Expected: 202 + WhatsApp arrives with 2 messages (split by `|||`)

- [ ] **Step 3: Verify dedup — call Edge Function again**

Call the Edge Function a second time immediately. Pedidos that already received level 1 should NOT get another level 1.

- [ ] **Step 4: Check cron execution (next day)**

After 9:00 AM the next day, check:
```sql
SELECT * FROM cron.job_run_details WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname = 'recordatorio-recojo') ORDER BY start_time DESC LIMIT 5;
```
