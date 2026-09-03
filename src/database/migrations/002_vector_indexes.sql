-- Phase 2: HNSW Cosine Vector Indexes for pgvector

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx 
ON chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS chunks_contextual_embedding_hnsw_idx 
ON chunks 
USING hnsw (contextual_embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS assets_embedding_hnsw_idx 
ON assets 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
