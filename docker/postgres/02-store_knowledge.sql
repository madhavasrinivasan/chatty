-- Drop and recreate store_knowledge with pgvector + full-text.
-- Tortoise cannot create vector(768) / generated tsvector — run this manually (or via initdb).
--
-- Fresh volume (first compose up): mounted as docker-entrypoint-initdb.d/02-*.sql
-- Existing DB:
--   docker compose exec -T db psql -U chatty -d chatty < docker/postgres/02-store_knowledge.sql

CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS store_knowledge CASCADE;

CREATE TABLE store_knowledge (
    id                  serial PRIMARY KEY,
    data_type           varchar(7) NOT NULL,
    store_id            bigint NOT NULL,
    shopify_product_id  varchar(50) UNIQUE,
    handle              varchar(255) NOT NULL,
    title               text NOT NULL,
    content             text NOT NULL,
    price               numeric(10, 2),
    stock               integer NOT NULL DEFAULT 0,
    image_url           text,
    variant_data        jsonb,
    collection_handles  jsonb,
    content_hash        varchar(32),
    url                 text,
    product_type        varchar(7),
    created_at          timestamptz NOT NULL DEFAULT NOW(),
    -- vector + FTS (not managed by Tortoise)
    embedding           vector(768),
    content_tsv         tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED
);

CREATE INDEX IF NOT EXISTS idx_store_knowledge_data_type
    ON store_knowledge (data_type);
CREATE INDEX IF NOT EXISTS idx_store_knowledge_shopify_product_id
    ON store_knowledge (shopify_product_id);
CREATE INDEX IF NOT EXISTS idx_store_knowledge_handle
    ON store_knowledge (handle);
CREATE INDEX IF NOT EXISTS idx_store_knowledge_store_id
    ON store_knowledge (store_id);

CREATE INDEX IF NOT EXISTS idx_store_knowledge_vector_search
    ON store_knowledge USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_store_knowledge_keyword_search
    ON store_knowledge USING GIN (content_tsv);

CREATE INDEX IF NOT EXISTS idx_store_knowledge_collection_handles
    ON store_knowledge USING GIN (collection_handles);

CREATE INDEX IF NOT EXISTS idx_store_knowledge_store_price
    ON store_knowledge (store_id, price)
    WHERE data_type = 'product' AND price IS NOT NULL;
