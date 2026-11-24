-- PrepBrief schema (PostgreSQL / Neon). Safe to re-run.

CREATE TABLE IF NOT EXISTS app_accounts (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT UNIQUE,
    email         TEXT UNIQUE,
    email_verified_at TIMESTAMPTZ,
    password_salt BYTEA NOT NULL,
    password_hash BYTEA NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='app_accounts' AND column_name='recovery_email')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='app_accounts' AND column_name='email') THEN
        ALTER TABLE app_accounts RENAME COLUMN recovery_email TO email;
    END IF;
END $$;
ALTER TABLE app_accounts ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE app_accounts ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;
ALTER TABLE app_accounts ALTER COLUMN username DROP NOT NULL;
UPDATE app_accounts SET email=lower(username) WHERE email IS NULL AND username LIKE '%@%.%';
UPDATE app_accounts a SET email=NULL WHERE a.email IS NOT NULL AND EXISTS
    (SELECT 1 FROM app_accounts older WHERE lower(older.email)=lower(a.email) AND older.id<a.id);
UPDATE app_accounts SET email=lower(email) WHERE email IS NOT NULL;
UPDATE app_accounts a SET username=NULL WHERE a.username IS NOT NULL AND EXISTS
    (SELECT 1 FROM app_accounts older WHERE lower(older.username)=lower(a.username) AND older.id<a.id);
UPDATE app_accounts SET username=lower(username) WHERE username IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_accounts_email ON app_accounts (lower(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_accounts_username_lower ON app_accounts (lower(username)) WHERE username IS NOT NULL;
ALTER TABLE app_accounts ALTER COLUMN id TYPE BIGINT;
ALTER TABLE app_accounts DROP CONSTRAINT IF EXISTS app_accounts_id_check;
CREATE SEQUENCE IF NOT EXISTS app_accounts_id_seq AS BIGINT OWNED BY app_accounts.id;
ALTER SEQUENCE IF EXISTS app_accounts_id_seq AS BIGINT;
ALTER TABLE app_accounts ALTER COLUMN id SET DEFAULT nextval('app_accounts_id_seq');
SELECT setval('app_accounts_id_seq', GREATEST(COALESCE((SELECT max(id) FROM app_accounts), 1), 1),
              EXISTS (SELECT 1 FROM app_accounts));

CREATE TABLE IF NOT EXISTS app_sessions (
    token_hash BYTEA PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES app_accounts(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE app_sessions ADD COLUMN IF NOT EXISTS user_id BIGINT;
UPDATE app_sessions SET user_id = (SELECT min(id) FROM app_accounts) WHERE user_id IS NULL;
DELETE FROM app_sessions WHERE user_id IS NULL;
ALTER TABLE app_sessions ALTER COLUMN user_id SET NOT NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'app_sessions_user_id_fkey') THEN
        ALTER TABLE app_sessions ADD CONSTRAINT app_sessions_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES app_accounts(id) ON DELETE CASCADE;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_app_sessions_expiry ON app_sessions (expires_at);
CREATE INDEX IF NOT EXISTS idx_app_sessions_user ON app_sessions (user_id);

CREATE TABLE IF NOT EXISTS auth_email_codes (
    id BIGSERIAL PRIMARY KEY,
    purpose TEXT NOT NULL CHECK (purpose IN ('signup', 'reset', 'recovery_email')),
    username TEXT NOT NULL,
    email TEXT NOT NULL,
    payload JSONB,
    code_hash BYTEA NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE (purpose, username)
);
CREATE INDEX IF NOT EXISTS idx_auth_email_codes_expiry ON auth_email_codes (expires_at);

CREATE TABLE IF NOT EXISTS articles (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL DEFAULT (NULLIF(current_setting('app.user_id', true), '')::BIGINT),
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    source          TEXT NOT NULL,
    section         TEXT,
    published_at    TIMESTAMPTZ,
    summary         TEXT,
    full_text       TEXT,
    reading_minutes INTEGER,
    must_read       BOOLEAN NOT NULL DEFAULT FALSE,
    is_relevant     BOOLEAN,
    status          TEXT NOT NULL DEFAULT 'new',  -- new | text_ready | processed | needs_review
    confidence      REAL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ
);
ALTER TABLE articles ADD COLUMN IF NOT EXISTS user_id BIGINT;
UPDATE articles SET user_id = (SELECT min(id) FROM app_accounts) WHERE user_id IS NULL;
ALTER TABLE articles ALTER COLUMN user_id SET DEFAULT (NULLIF(current_setting('app.user_id', true), '')::BIGINT);
ALTER TABLE articles ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE articles DROP CONSTRAINT IF EXISTS articles_url_key;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'articles_user_id_fkey') THEN
        ALTER TABLE articles ADD CONSTRAINT articles_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES app_accounts(id) ON DELETE CASCADE;
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_user_url ON articles (user_id, url);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles (status);

CREATE TABLE IF NOT EXISTS subjects (
    id   SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id          SERIAL PRIMARY KEY,
    subject_id  INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    UNIQUE (subject_id, name)
);

CREATE TABLE IF NOT EXISTS article_topics (
    article_id BIGINT  NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    topic_id   INTEGER NOT NULL REFERENCES topics(id)   ON DELETE CASCADE,
    PRIMARY KEY (article_id, topic_id)
);

CREATE TABLE IF NOT EXISTS highlights (
    id           BIGSERIAL PRIMARY KEY,
    article_id   BIGINT  NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    start_offset INTEGER NOT NULL,
    end_offset   INTEGER NOT NULL,
    origin       TEXT NOT NULL DEFAULT 'ai',  -- ai | user
    CHECK (end_offset > start_offset)
);
CREATE INDEX IF NOT EXISTS idx_highlights_article ON highlights (article_id);

CREATE TABLE IF NOT EXISTS takeaways (
    id         BIGSERIAL PRIMARY KEY,
    article_id BIGINT  NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    text       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id         BIGSERIAL PRIMARY KEY,
    article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    text       TEXT   NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS corrections (
    id                 BIGSERIAL PRIMARY KEY,
    article_id         BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    original_analysis  JSONB NOT NULL,
    corrected_analysis JSONB NOT NULL,
    note               TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_corrections_article ON corrections (article_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reference_items (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL DEFAULT (NULLIF(current_setting('app.user_id', true), '')::BIGINT),
    title      TEXT NOT NULL,
    category   TEXT NOT NULL,
    summary    TEXT NOT NULL,
    source_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE reference_items ADD COLUMN IF NOT EXISTS user_id BIGINT;
UPDATE reference_items SET user_id = (SELECT min(id) FROM app_accounts) WHERE user_id IS NULL;
ALTER TABLE reference_items ALTER COLUMN user_id SET DEFAULT (NULLIF(current_setting('app.user_id', true), '')::BIGINT);
ALTER TABLE reference_items ALTER COLUMN user_id SET NOT NULL;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reference_items_user_id_fkey') THEN
        ALTER TABLE reference_items ADD CONSTRAINT reference_items_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES app_accounts(id) ON DELETE CASCADE;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_reference_items_user ON reference_items (user_id);
CREATE INDEX IF NOT EXISTS idx_reference_items_category ON reference_items (category);

CREATE TABLE IF NOT EXISTS reference_item_topics (
    reference_id BIGINT NOT NULL REFERENCES reference_items(id) ON DELETE CASCADE,
    topic_id     INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    PRIMARY KEY (reference_id, topic_id)
);

CREATE TABLE IF NOT EXISTS note_embeddings (
    note_id         BIGINT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    embedding       JSONB NOT NULL,
    embedding_model TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS article_embeddings (
    article_id      BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    start_offset    INTEGER NOT NULL,
    end_offset      INTEGER NOT NULL,
    embedding       JSONB NOT NULL,
    embedding_model TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (article_id, chunk_index),
    CHECK (start_offset >= 0 AND end_offset > start_offset)
);
CREATE INDEX IF NOT EXISTS idx_article_embeddings_article ON article_embeddings (article_id);

CREATE TABLE IF NOT EXISTS reference_embeddings (
    reference_id    BIGINT PRIMARY KEY REFERENCES reference_items(id) ON DELETE CASCADE,
    embedding       JSONB NOT NULL,
    embedding_model TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS read_status (
    article_id BIGINT PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    is_read    BOOLEAN NOT NULL DEFAULT FALSE,
    is_saved   BOOLEAN NOT NULL DEFAULT FALSE,
    read_at    TIMESTAMPTZ
);

-- Row-level policies also protect child records by checking their owning parent.
ALTER TABLE articles ENABLE ROW LEVEL SECURITY;
ALTER TABLE articles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON articles;
CREATE POLICY prepbrief_owner ON articles
    USING (user_id = NULLIF(current_setting('app.user_id', true), '')::BIGINT)
    WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), '')::BIGINT);

ALTER TABLE reference_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference_items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON reference_items;
CREATE POLICY prepbrief_owner ON reference_items
    USING (user_id = NULLIF(current_setting('app.user_id', true), '')::BIGINT)
    WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), '')::BIGINT);

ALTER TABLE article_topics ENABLE ROW LEVEL SECURITY;
ALTER TABLE article_topics FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON article_topics;
CREATE POLICY prepbrief_owner ON article_topics
    USING (EXISTS (SELECT 1 FROM articles a WHERE a.id = article_topics.article_id))
    WITH CHECK (EXISTS (SELECT 1 FROM articles a WHERE a.id = article_topics.article_id));

ALTER TABLE highlights ENABLE ROW LEVEL SECURITY;
ALTER TABLE highlights FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON highlights;
CREATE POLICY prepbrief_owner ON highlights
    USING (EXISTS (SELECT 1 FROM articles a WHERE a.id = highlights.article_id))
    WITH CHECK (EXISTS (SELECT 1 FROM articles a WHERE a.id = highlights.article_id));

ALTER TABLE takeaways ENABLE ROW LEVEL SECURITY;
ALTER TABLE takeaways FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON takeaways;
CREATE POLICY prepbrief_owner ON takeaways
    USING (EXISTS (SELECT 1 FROM articles a WHERE a.id = takeaways.article_id))
    WITH CHECK (EXISTS (SELECT 1 FROM articles a WHERE a.id = takeaways.article_id));

ALTER TABLE notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE notes FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON notes;
CREATE POLICY prepbrief_owner ON notes
    USING (EXISTS (SELECT 1 FROM articles a WHERE a.id = notes.article_id))
    WITH CHECK (EXISTS (SELECT 1 FROM articles a WHERE a.id = notes.article_id));

ALTER TABLE corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE corrections FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON corrections;
CREATE POLICY prepbrief_owner ON corrections
    USING (EXISTS (SELECT 1 FROM articles a WHERE a.id = corrections.article_id))
    WITH CHECK (EXISTS (SELECT 1 FROM articles a WHERE a.id = corrections.article_id));

ALTER TABLE read_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE read_status FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON read_status;
CREATE POLICY prepbrief_owner ON read_status
    USING (EXISTS (SELECT 1 FROM articles a WHERE a.id = read_status.article_id))
    WITH CHECK (EXISTS (SELECT 1 FROM articles a WHERE a.id = read_status.article_id));

ALTER TABLE article_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE article_embeddings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON article_embeddings;
CREATE POLICY prepbrief_owner ON article_embeddings
    USING (EXISTS (SELECT 1 FROM articles a WHERE a.id = article_embeddings.article_id))
    WITH CHECK (EXISTS (SELECT 1 FROM articles a WHERE a.id = article_embeddings.article_id));

ALTER TABLE note_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE note_embeddings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON note_embeddings;
CREATE POLICY prepbrief_owner ON note_embeddings
    USING (EXISTS (SELECT 1 FROM notes n JOIN articles a ON a.id = n.article_id WHERE n.id = note_embeddings.note_id))
    WITH CHECK (EXISTS (SELECT 1 FROM notes n JOIN articles a ON a.id = n.article_id WHERE n.id = note_embeddings.note_id));

ALTER TABLE reference_item_topics ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference_item_topics FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON reference_item_topics;
CREATE POLICY prepbrief_owner ON reference_item_topics
    USING (EXISTS (SELECT 1 FROM reference_items r WHERE r.id = reference_item_topics.reference_id))
    WITH CHECK (EXISTS (SELECT 1 FROM reference_items r WHERE r.id = reference_item_topics.reference_id));

ALTER TABLE reference_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference_embeddings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS prepbrief_owner ON reference_embeddings;
CREATE POLICY prepbrief_owner ON reference_embeddings
    USING (EXISTS (SELECT 1 FROM reference_items r WHERE r.id = reference_embeddings.reference_id))
    WITH CHECK (EXISTS (SELECT 1 FROM reference_items r WHERE r.id = reference_embeddings.reference_id));
