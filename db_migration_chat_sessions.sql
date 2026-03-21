-- Migration: add chat sessions/messages + store sync tracking
-- Intended for PostgreSQL.
--
-- What this adds:
-- 1) ecom_store.last_synced_at, ecom_store.sync_status (default: idle)
-- 2) chat_sessions (UUID PK)
-- 3) chat_messages (UUID PK, FK -> chat_sessions)
--
-- Notes:
-- - Tortoise ORM will also create these automatically in development via
--   Tortoise.generate_schemas(safe=True). This file is for explicit migration in
--   environments where you manage schema manually.

BEGIN;

-- 1) ecom_store sync tracking
ALTER TABLE ecom_store
  ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS sync_status VARCHAR(20) NOT NULL DEFAULT 'idle';

-- 2) chat_sessions
CREATE TABLE IF NOT EXISTS chat_sessions (
  id UUID PRIMARY KEY,
  shop_domain VARCHAR(255) NOT NULL,
  customer_email VARCHAR(255) NULL,
  cart_token VARCHAR(255) NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_shop_domain
  ON chat_sessions (shop_domain);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_customer_email
  ON chat_sessions (customer_email);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_cart_token
  ON chat_sessions (cart_token);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_status
  ON chat_sessions (status);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated_at
  ON chat_sessions (updated_at);

-- 3) chat_messages
CREATE TABLE IF NOT EXISTS chat_messages (
  id UUID PRIMARY KEY,
  session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role VARCHAR(20) NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id
  ON chat_messages (session_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at
  ON chat_messages (created_at);

COMMIT;

