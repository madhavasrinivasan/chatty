-- Product image embeddings for visual search.
-- Run after the pgvector extension exists.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE store_knowledge
  ADD COLUMN IF NOT EXISTS image_embedding vector(768);

CREATE INDEX IF NOT EXISTS idx_store_knowledge_image_vector_search
  ON store_knowledge USING hnsw (image_embedding vector_cosine_ops);

COMMIT;
