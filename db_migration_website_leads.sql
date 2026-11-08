-- Website chatbot leads (PDF Q&A + lead capture).
-- Idempotent. Tortoise `generate_schemas(safe=True)` also creates this table
-- at startup, but run this if you manage migrations manually.

BEGIN;

CREATE TABLE IF NOT EXISTS leads (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    chatbot_id INTEGER NULL,
    store_id INTEGER NULL,
    session_id VARCHAR(255) NULL,
    name VARCHAR(255) NULL,
    email VARCHAR(255) NULL,
    phone VARCHAR(64) NULL,
    extra_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    page_url TEXT NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'inline_form',
    consent_text_version VARCHAR(64) NULL,
    consent_at TIMESTAMPTZ NULL,
    conversation_summary TEXT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'new',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_leads_user_id ON leads (user_id);
CREATE INDEX IF NOT EXISTS idx_leads_chatbot_id ON leads (chatbot_id);
CREATE INDEX IF NOT EXISTS idx_leads_store_id ON leads (store_id);
CREATE INDEX IF NOT EXISTS idx_leads_session_id ON leads (session_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads (created_at);

COMMIT;
