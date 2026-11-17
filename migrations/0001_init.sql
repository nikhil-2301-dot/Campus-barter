-- 0001_init: core tables for Campus Barter (see docs/system-design.md section 5).
-- Never edit this file after it has been applied anywhere; add 0002_*.sql instead.
-- Timestamps are UTC ISO-8601 text with milliseconds, e.g. 2026-10-15T09:30:00.123Z.

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    email         TEXT    NOT NULL UNIQUE CHECK (email = lower(email)),
    password_hash TEXT    NOT NULL,
    full_name     TEXT    NOT NULL,
    branch        TEXT    NOT NULL,
    year          TEXT    NOT NULL CHECK (year IN ('FE', 'SE', 'TE', 'BE')),
    phone         TEXT,                       -- private; shown only after a request is accepted
    is_admin      INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
    is_suspended  INTEGER NOT NULL DEFAULT 0 CHECK (is_suspended IN (0, 1)),
    deleted_at    TEXT,                       -- account deletion anonymises the row
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE listings (
    id               INTEGER PRIMARY KEY,
    owner_id         INTEGER NOT NULL REFERENCES users (id),
    title            TEXT    NOT NULL,
    category         TEXT    NOT NULL,        -- validated against the config list, not a table
    condition        TEXT    NOT NULL CHECK (condition IN ('new', 'good', 'fair', 'worn')),
    description      TEXT    NOT NULL DEFAULT '',
    mode             TEXT    NOT NULL CHECK (mode IN ('swap', 'lend', 'give')),
    wanted_in_return TEXT,
    photo_key        TEXT,
    status           TEXT    NOT NULL DEFAULT 'available'
                     CHECK (status IN ('available', 'reserved', 'closed', 'removed')),
    deleted_at       TEXT,                    -- soft delete
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- a listing is soft-deleted exactly when its status is 'removed'
    CHECK ((status = 'removed') = (deleted_at IS NOT NULL))
);

CREATE TABLE exchange_requests (
    id                 INTEGER PRIMARY KEY,
    listing_id         INTEGER NOT NULL REFERENCES listings (id),
    requester_id       INTEGER NOT NULL REFERENCES users (id),
    offered_listing_id INTEGER REFERENCES listings (id),
    message            TEXT    NOT NULL DEFAULT '',
    status             TEXT    NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'accepted', 'completed', 'declined', 'cancelled')),
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (offered_listing_id IS NULL OR offered_listing_id <> listing_id)
);

CREATE TABLE reports (
    id          INTEGER PRIMARY KEY,
    listing_id  INTEGER NOT NULL REFERENCES listings (id),
    reporter_id INTEGER NOT NULL REFERENCES users (id),
    reason      TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'dismissed')),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Rate limiting (used from chunk C8). No foreign keys on purpose: "key" is an email or IP.
CREATE TABLE login_attempts (
    id           INTEGER PRIMARY KEY,
    "key"        TEXT NOT NULL,
    attempted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- One open (pending/accepted) request per requester per listing.
CREATE UNIQUE INDEX uq_requests_one_open_per_requester
    ON exchange_requests (listing_id, requester_id) WHERE status IN ('pending', 'accepted');
-- Rule from the status model: one accepted request per listing at a time.
CREATE UNIQUE INDEX uq_requests_one_accepted_per_listing
    ON exchange_requests (listing_id) WHERE status = 'accepted';

-- Foreign-key indexes and the browse/search indexes from the design.
CREATE INDEX ix_listings_owner        ON listings (owner_id);
CREATE INDEX ix_listings_status_cat   ON listings (status, category);
CREATE INDEX ix_listings_created      ON listings (created_at);
CREATE INDEX ix_requests_requester    ON exchange_requests (requester_id);
CREATE INDEX ix_requests_offered      ON exchange_requests (offered_listing_id);
CREATE INDEX ix_reports_listing       ON reports (listing_id);
CREATE INDEX ix_reports_reporter      ON reports (reporter_id);
CREATE INDEX ix_login_attempts_lookup ON login_attempts ("key", attempted_at);

-- Keep updated_at honest. The WHEN clause also stops the trigger re-firing itself.
CREATE TRIGGER trg_users_updated AFTER UPDATE ON users
WHEN NEW.updated_at = OLD.updated_at
BEGIN UPDATE users SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id; END;

CREATE TRIGGER trg_listings_updated AFTER UPDATE ON listings
WHEN NEW.updated_at = OLD.updated_at
BEGIN UPDATE listings SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id; END;

CREATE TRIGGER trg_requests_updated AFTER UPDATE ON exchange_requests
WHEN NEW.updated_at = OLD.updated_at
BEGIN UPDATE exchange_requests SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id; END;

CREATE TRIGGER trg_reports_updated AFTER UPDATE ON reports
WHEN NEW.updated_at = OLD.updated_at
BEGIN UPDATE reports SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id; END;
