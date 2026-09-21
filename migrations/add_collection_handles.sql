-- Product search: collection membership + optional GIN index
-- Run against your Postgres (chatty backend DB) before re-syncing products.

ALTER TABLE store_knowledge
  ADD COLUMN IF NOT EXISTS collection_handles JSONB;

-- Optional but recommended for collection filters
CREATE INDEX IF NOT EXISTS idx_store_knowledge_collection_handles
  ON store_knowledge USING GIN (collection_handles);

-- Optional: speed up budget / cheapest filters
CREATE INDEX IF NOT EXISTS idx_store_knowledge_store_price
  ON store_knowledge (store_id, price)
  WHERE data_type = 'product' AND price IS NOT NULL;
