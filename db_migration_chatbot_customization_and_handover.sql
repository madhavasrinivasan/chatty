-- Migration: add needs_human handover flag to chat_sessions + create chatbot_customization table
-- PostgreSQL.
--

BEGIN;

-- 1) Add needs_human to chat_sessions
ALTER TABLE chat_sessions
  ADD COLUMN IF NOT EXISTS needs_human BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_chat_sessions_needs_human
  ON chat_sessions (needs_human);

-- 2) Create chatbot_customization table
CREATE TABLE IF NOT EXISTS chatbot_customization (
  id SERIAL PRIMARY KEY,
  store_id INT NOT NULL UNIQUE REFERENCES ecom_store(id) ON DELETE CASCADE,
  bot_name VARCHAR(255) NOT NULL DEFAULT 'Assistant',
  greeting_message TEXT NOT NULL DEFAULT 'Hi! How can I help you today?',
  logo_url TEXT NULL,
  avatar_url TEXT NULL,
  primary_color VARCHAR(50) NOT NULL DEFAULT '#4F46E5',
  secondary_color VARCHAR(50) NOT NULL DEFAULT '#E0E7FF',
  background_color VARCHAR(50) NOT NULL DEFAULT '#FFFFFF',
  text_color VARCHAR(50) NOT NULL DEFAULT '#1F2937',
  user_bubble_color VARCHAR(50) NOT NULL DEFAULT '#4F46E5',
  bot_bubble_color VARCHAR(50) NOT NULL DEFAULT '#F3F4F6',
  font_family VARCHAR(100) NOT NULL DEFAULT 'Inter',
  font_size_base INT NOT NULL DEFAULT 14,
  widget_position VARCHAR(50) NOT NULL DEFAULT 'bottom-right',
  border_radius INT NOT NULL DEFAULT 8,
  button_icon_style VARCHAR(50) NOT NULL DEFAULT 'default',
  sample_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
  system_prompt_override TEXT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chatbot_customization_store_id
  ON chatbot_customization (store_id);

COMMIT;
