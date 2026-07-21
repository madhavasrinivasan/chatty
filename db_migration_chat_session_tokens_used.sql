-- Add cumulative tiktoken-based estimate on chat_sessions (per-turn increments in approutes).
BEGIN;

ALTER TABLE chat_sessions
  ADD COLUMN IF NOT EXISTS tokens_used BIGINT NOT NULL DEFAULT 0;

COMMIT;
