-- Extended thinking flag on merchant chatbot customization
-- Run against your app DB (e.g. symma_db / chatty):
--   psql "$DB_URL" -f scripts/migrate_extended_thinking.sql

ALTER TABLE chatbot_customization
ADD COLUMN IF NOT EXISTS extended_thinking BOOLEAN DEFAULT FALSE;

-- Optional: enable for one store after you confirm TypeSafe is configured
-- UPDATE chatbot_customization SET extended_thinking = TRUE WHERE store_id = ?;
