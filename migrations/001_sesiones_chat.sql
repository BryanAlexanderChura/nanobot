-- Migration 001: Tabla de sesiones de chat para nanobot
-- Backend: Supabase (PostgreSQL)
-- Ejecutar en: Supabase Dashboard > SQL Editor

-- Tabla principal: almacena conversaciones por session_key (channel:chat_id)
CREATE TABLE IF NOT EXISTS sesiones_chat (
    key         TEXT PRIMARY KEY,                     -- "whatsapp:+51987654321"
    messages    JSONB NOT NULL DEFAULT '[]'::jsonb,   -- Array de {role, content, timestamp}
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,   -- Datos extra por sesión
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índice para listar sesiones recientes
CREATE INDEX IF NOT EXISTS idx_sesiones_chat_updated
    ON sesiones_chat (updated_at DESC);

-- Índice para buscar sesiones por canal (ej: todas las de whatsapp)
CREATE INDEX IF NOT EXISTS idx_sesiones_chat_channel
    ON sesiones_chat ((split_part(key, ':', 1)));

-- Auto-update de updated_at en cada upsert
CREATE OR REPLACE FUNCTION fn_sesiones_chat_updated()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sesiones_chat_updated ON sesiones_chat;
CREATE TRIGGER trg_sesiones_chat_updated
    BEFORE UPDATE ON sesiones_chat
    FOR EACH ROW
    EXECUTE FUNCTION fn_sesiones_chat_updated();

-- RLS: habilitar Row Level Security (requerido por Supabase)
ALTER TABLE sesiones_chat ENABLE ROW LEVEL SECURITY;

-- Policy: service_role tiene acceso total (nanobot usa service_key)
CREATE POLICY "service_role_full_access" ON sesiones_chat
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
