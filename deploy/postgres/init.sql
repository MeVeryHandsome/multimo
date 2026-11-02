-- Runs once when the postgres volume is first created.
-- Tables are created by the backend's migrations (multimo-backend/manifest/sql).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
