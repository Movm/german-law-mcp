CREATE TABLE IF NOT EXISTS laws (
  id BIGSERIAL PRIMARY KEY,
  doknr TEXT NOT NULL UNIQUE,
  slug TEXT NOT NULL,
  gii_slug TEXT NOT NULL UNIQUE,
  abbreviation TEXT NOT NULL,
  extra_abbreviations TEXT[] NOT NULL DEFAULT '{}',
  first_published DATE,
  source_timestamp TEXT,
  title_long TEXT NOT NULL,
  title_short TEXT,
  publication_info JSONB NOT NULL DEFAULT '[]',
  status_info JSONB NOT NULL DEFAULT '[]',
  source_url TEXT NOT NULL,
  source_snapshot DATE NOT NULL,
  source_repository TEXT NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS provisions (
  id BIGSERIAL PRIMARY KEY,
  doknr TEXT NOT NULL UNIQUE,
  law_id BIGINT NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
  item_type TEXT NOT NULL CHECK (item_type IN ('article', 'heading', 'heading_article')),
  name TEXT NOT NULL,
  title TEXT,
  body TEXT,
  footnotes TEXT,
  documentary_footnotes TEXT,
  parent_doknr TEXT,
  position INTEGER NOT NULL,
  search_vector TSVECTOR GENERATED ALWAYS AS (
    setweight(to_tsvector('german', coalesce(name, '')), 'A') ||
    setweight(to_tsvector('german', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('german', coalesce(body, '')), 'B')
  ) STORED
);

CREATE INDEX IF NOT EXISTS provisions_law_id_idx ON provisions(law_id);
CREATE INDEX IF NOT EXISTS provisions_name_idx ON provisions(name);
CREATE INDEX IF NOT EXISTS provisions_search_idx ON provisions USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS laws_abbreviation_lower_idx ON laws(lower(abbreviation));
CREATE INDEX IF NOT EXISTS laws_slug_idx ON laws(slug);
CREATE INDEX IF NOT EXISTS laws_title_long_search_idx
  ON laws USING GIN(to_tsvector('german', title_long));

CREATE TABLE IF NOT EXISTS ingestion_runs (
  id BIGSERIAL PRIMARY KEY,
  source_repository TEXT NOT NULL,
  source_snapshot DATE NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  imported_laws INTEGER NOT NULL DEFAULT 0,
  imported_provisions INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  error_sample JSONB NOT NULL DEFAULT '[]'
);
