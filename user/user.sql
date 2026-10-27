PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,      -- scrypt$n$r$p$salt$hash
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT,

    CHECK (length(username) BETWEEN 3 AND 32),
    CHECK (email LIKE '%_@_%._%')
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,      -- secrets.token_urlsafe(32)
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

-- "revision" is the contract with the recommendation agent: every profile
-- change bumps it (see _touch() in users.py), so the agent can cheaply ask
-- "has this user's taste changed since revision N?" instead of diffing text.
CREATE TABLE IF NOT EXISTS profiles (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    display_name TEXT,
    bio          TEXT,               -- free text, fed to the LLM
    is_public    INTEGER NOT NULL DEFAULT 1 CHECK (is_public IN (0, 1)),
    era_from     INTEGER,            -- only recommend movies from this year on
    era_to       INTEGER,            -- ... up to this year
    max_runtime  INTEGER,            -- minutes
    revision     INTEGER NOT NULL DEFAULT 1,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),

    CHECK (era_from IS NULL OR era_to IS NULL OR era_from <= era_to)
);

CREATE INDEX IF NOT EXISTS idx_profiles_browse ON profiles(is_public, updated_at DESC);

-- weight > 0 means "I like this", weight < 0 means "don't recommend this".
CREATE TABLE IF NOT EXISTS profile_genres (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    genre   TEXT    NOT NULL,
    weight  REAL    NOT NULL DEFAULT 1.0 CHECK (weight BETWEEN -1.0 AND 1.0),
    PRIMARY KEY (user_id, genre)
);

-- Free-form interests ("slow burn", "practical effects"), so a profile is not
-- limited to a fixed genre list. Also the main way users discover each other.
CREATE TABLE IF NOT EXISTS profile_tags (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tag     TEXT    NOT NULL COLLATE NOCASE,
    PRIMARY KEY (user_id, tag),
    CHECK (length(tag) BETWEEN 2 AND 40)
);

CREATE INDEX IF NOT EXISTS idx_tags_tag ON profile_tags(tag);

-- Every user gets a profile row the moment they register. A trigger keeps the
-- two inserts atomic without any transaction handling in Python.
CREATE TRIGGER IF NOT EXISTS trg_user_profile AFTER INSERT ON users BEGIN
    INSERT INTO profiles (user_id, display_name) VALUES (NEW.id, NEW.username);
END;

-- The payload behind GET /api/user/profiles/{username}: one row per user with
-- the genre and tag lists flattened into strings the frontend can print.
CREATE VIEW IF NOT EXISTS v_profile AS
SELECT
    u.id AS user_id,
    u.username,
    u.created_at,
    p.display_name,
    p.bio,
    p.is_public,
    p.era_from,
    p.era_to,
    p.max_runtime,
    p.revision,
    p.updated_at,
    (SELECT group_concat(genre, ', ') FROM profile_genres
      WHERE user_id = u.id AND weight > 0) AS likes_genres,
    (SELECT group_concat(genre, ', ') FROM profile_genres
      WHERE user_id = u.id AND weight < 0) AS avoids_genres,
    (SELECT group_concat(tag, ', ') FROM profile_tags
      WHERE user_id = u.id)                AS tags
FROM users u
JOIN profiles p ON p.user_id = u.id;
