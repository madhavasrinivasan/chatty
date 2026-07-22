-- Dashboard analytics: add-to-cart click events (revenue attribution from chat widget).
-- Run against your Postgres DB.

BEGIN;

CREATE TABLE IF NOT EXISTS add_to_cart_events (
    id BIGSERIAL PRIMARY KEY,
    store_id INTEGER NOT NULL,
    chatbot_id INTEGER NULL,
    session_id VARCHAR(64) NULL,
    shop_domain VARCHAR(255) NULL,
    product_id VARCHAR(255) NULL,
    variant_id VARCHAR(255) NULL,
    title VARCHAR(512) NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price DOUBLE PRECISION NOT NULL DEFAULT 0,
    currency VARCHAR(8) NOT NULL DEFAULT 'INR',
    line_revenue DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_add_to_cart_events_store_id ON add_to_cart_events (store_id);
CREATE INDEX IF NOT EXISTS idx_add_to_cart_events_chatbot_id ON add_to_cart_events (chatbot_id);
CREATE INDEX IF NOT EXISTS idx_add_to_cart_events_session_id ON add_to_cart_events (session_id);
CREATE INDEX IF NOT EXISTS idx_add_to_cart_events_created_at ON add_to_cart_events (created_at);

-- Ensure LLM usage columns exist (idempotent; also in db_migration_ecom_store_and_session_usage.sql).
ALTER TABLE ecom_store
  ADD COLUMN IF NOT EXISTS total_input_tokens BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS total_output_tokens BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS total_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0;

COMMIT;
