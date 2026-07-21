-- Migration: ChatMessage.payload (JSONB) + nullable content
-- PostgreSQL. Run after table `chat_messages` exists.
--
-- Purpose:
-- 1) Store full assistant `final_response` JSON in `chat_messages.payload` (not only general_answer).
-- 2) Allow `content` NULL when only `payload` is used (we still set content = general_answer when present).
--
-- Chat sessions already have `customer_email`; the app updates that column when headers include customer email.

BEGIN;

ALTER TABLE chat_messages
  ADD COLUMN IF NOT EXISTS payload JSONB NULL;

ALTER TABLE chat_messages
  ALTER COLUMN content DROP NOT NULL;

COMMIT;

-- Notes:
-- - Tortoise `JSONField` maps to JSONB on PostgreSQL.
-- - User messages => content=text, payload=NULL; assistant => payload=full final_response dict, content=general_answer or NULL.
