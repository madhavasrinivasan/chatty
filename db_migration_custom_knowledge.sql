-- Custom PDF knowledge (data_type='custom') for hybrid chat retrieval.
-- "custom" is 6 chars and fits existing store_knowledge.data_type VARCHAR(7).
-- No structural change required if the column is already VARCHAR(7)+; this is documentation + safety.

BEGIN;

-- Ensure data_type can store 'custom' (idempotent widen if someone has a tighter check).
ALTER TABLE store_knowledge
  ALTER COLUMN data_type TYPE VARCHAR(16);

COMMIT;
