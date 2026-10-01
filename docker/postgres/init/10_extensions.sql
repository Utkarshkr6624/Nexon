-- =============================================================================
-- NEXUS — PostgreSQL extensions enabled on first initialisation.
-- =============================================================================
-- Mounted read-only at /docker-entrypoint-initdb.d and executed by the official
-- postgres entrypoint, in filename order, *only* when the data directory is
-- empty (i.e. on first `docker compose up` with a fresh volume).
--
-- This file creates extensions and nothing else: no tables, no roles, no seed
-- or demo data. The schema is owned by Alembic (backend/migrations) and must
-- stay the single source of truth for it.
--
-- To re-apply after the volume already exists, exec into the container and
-- point psql at the in-container mount point — the host path does not exist
-- inside it:
--   docker compose exec postgres psql -U nexus -d nexus \
--     -f /docker-entrypoint-initdb.d/10_extensions.sql
-- =============================================================================

-- trigram similarity index (gin_trgm_ops) and LIKE/ILIKE acceleration.
-- Prerequisite for the upcoming knowledge-base features: fuzzy title/tag
-- matching, "did you mean" suggestions and typo-tolerant search over notes and
-- documents. Creating the extension now avoids a migration that cannot be
-- reverted cleanly later.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Dictionary that strips diacritics before comparison, so a search for
-- "uber" matches "über" and "resume" matches "résumé". Exposes the immutable
-- unaccent(text) function used in generated search vectors and functional
-- indexes; loaded as a text-search dictionary as well.
CREATE EXTENSION IF NOT EXISTS unaccent;
