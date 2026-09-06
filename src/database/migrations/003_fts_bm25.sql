-- Phase 3: PostgreSQL Full-Text Search / BM25-Style Lexical Retrieval Setup

-- 1. Add tsvector column if it doesn't already exist
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_vector tsvector;

-- 2. Populate search_vector from content using english configuration
UPDATE chunks 
SET search_vector = to_tsvector('english', coalesce(content, ''))
WHERE search_vector IS NULL;

-- 3. Create GIN index for fast text search
CREATE INDEX IF NOT EXISTS chunks_search_idx 
ON chunks USING gin(search_vector);

-- 4. Create trigger function to keep search_vector automatically synced on inserts & updates
CREATE OR REPLACE FUNCTION chunks_search_vector_trigger() 
RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', coalesce(NEW.content, ''));
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS chunks_search_update ON chunks;

CREATE TRIGGER chunks_search_update
    BEFORE INSERT OR UPDATE OF content ON chunks
    FOR EACH ROW
    EXECUTE FUNCTION chunks_search_vector_trigger();
