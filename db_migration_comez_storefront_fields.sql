-- Comez / multi-platform storefront fields on ecom_store.
-- Run against your Postgres DB.

BEGIN;

ALTER TABLE ecom_store
  ADD COLUMN IF NOT EXISTS storefront_url VARCHAR(512) NULL,
  ADD COLUMN IF NOT EXISTS custom_domain BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS x_store VARCHAR(255) NULL;

COMMIT;
