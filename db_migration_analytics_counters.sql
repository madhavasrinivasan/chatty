-- Analytics counters: prompt-cache savings, multi-intent routing, knowledge
-- retrieval, and human escalations. Idempotent (also applied at startup by
-- app/core/config/db.py::_upgrade_analytics_columns).

BEGIN;

ALTER TABLE ecom_store
  ADD COLUMN IF NOT EXISTS total_cached_input_tokens BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS total_multi_intent_turns BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS total_product_searches BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS total_knowledge_searches BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS total_escalations BIGINT NOT NULL DEFAULT 0;

ALTER TABLE chat_sessions
  ADD COLUMN IF NOT EXISTS cached_input_tokens_total BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS multi_intent_turns INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS product_searches_total INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS knowledge_searches_total INTEGER NOT NULL DEFAULT 0;

COMMIT;
