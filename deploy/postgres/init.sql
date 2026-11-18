-- Runs once, when the database volume is first created.
-- pgvector stores the embeddings behind permanent memory and the knowledge base.
CREATE EXTENSION IF NOT EXISTS vector;
