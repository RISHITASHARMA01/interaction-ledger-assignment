"""SQLite schema and connection handling.

Tables (see SUBMISSION.md for the rationale):

  meta            key/value: schema version, PII key fingerprint
  source_files    one row per ingested file; the idempotency guard (+ per-file line counts)
  leads           one row per (workflow_id, phone_token); PII only encrypted / hashed
  lead_rows       lineage: what happened to every non-blank CSV row (created/merged/rejected)
  events          THE interaction ledger. Append-only (UPDATE/DELETE blocked by triggers)
  event_rejects   lines that never made it into the ledger: malformed / duplicate

Linking events to leads (orphans, overrides) is *derived* at report time, never written back into
the ledger, so an event that was an orphan yesterday counts as soon as its lead arrives.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .crypto import Crypto

SCHEMA_VERSION = "1"

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_files (
    file_id      INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('leads', 'events')),
    workflow_id  TEXT,                -- leads only
    batch_id     TEXT,                -- leads only: file stem
    path         TEXT NOT NULL,       -- as given on the command line (informational)
    sha256       TEXT NOT NULL,
    lines_total  INTEGER NOT NULL,    -- leads: non-blank data rows; events: non-empty lines
    ingested_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK ((kind = 'leads') = (workflow_id IS NOT NULL AND batch_id IS NOT NULL))
);
-- A lead batch is identified by (workflow, file stem); an event file by its content.
CREATE UNIQUE INDEX IF NOT EXISTS ux_source_lead_batch
    ON source_files (workflow_id, batch_id) WHERE kind = 'leads';
CREATE UNIQUE INDEX IF NOT EXISTS ux_source_event_file
    ON source_files (sha256) WHERE kind = 'events';

CREATE TABLE IF NOT EXISTS leads (
    lead_id       INTEGER PRIMARY KEY,
    workflow_id   TEXT NOT NULL,
    phone_token   TEXT NOT NULL,      -- HMAC-SHA256 blind index of '+91XXXXXXXXXX'
    phone_last4   TEXT NOT NULL CHECK (length(phone_last4) = 4),
    external_ref  TEXT NOT NULL,      -- schema field marked "reference": true (non-PII)
    batch_id      TEXT NOT NULL,      -- batch of the first valid row; never changes
    pii_enc       BLOB NOT NULL,      -- Fernet(JSON of every "pii": true field)
    attrs_json    TEXT NOT NULL,      -- JSON of non-PII fields (nulls kept; defaults applied on read)
    created_file_id INTEGER NOT NULL REFERENCES source_files(file_id),
    updated_file_id INTEGER NOT NULL REFERENCES source_files(file_id),
    UNIQUE (workflow_id, phone_token)
);

CREATE TABLE IF NOT EXISTS lead_rows (
    file_id        INTEGER NOT NULL REFERENCES source_files(file_id),
    row_number     INTEGER NOT NULL CHECK (row_number >= 1),
    outcome        TEXT NOT NULL CHECK (outcome IN ('created', 'merged', 'rejected')),
    lead_id        INTEGER REFERENCES leads(lead_id),
    reason         TEXT,              -- rejection reason, no PII
    field_warnings INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_id, row_number),
    CHECK ((outcome = 'rejected') = (reason IS NOT NULL AND lead_id IS NULL))
);

CREATE TABLE IF NOT EXISTS events (
    event_id         TEXT PRIMARY KEY,
    file_id          INTEGER NOT NULL REFERENCES source_files(file_id),
    line_number      INTEGER NOT NULL,
    event_type       TEXT,
    workflow_id      TEXT,
    call_id          TEXT,
    occurred_at_us   INTEGER NOT NULL,  -- UTC epoch microseconds (naive input == IST)
    occurred_at_raw  TEXT NOT NULL,     -- as received, for audit
    phone_token      TEXT,              -- NULL when absent or not a valid mobile
    phone_status     TEXT NOT NULL CHECK (phone_status IN ('valid', 'invalid', 'absent')),
    disposition_code TEXT,
    payload_json     TEXT NOT NULL      -- payload as received (contains no phone)
);
CREATE INDEX IF NOT EXISTS ix_events_link ON events (workflow_id, phone_token);
CREATE INDEX IF NOT EXISTS ix_events_call ON events (call_id);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events ledger is append-only'); END;

CREATE TABLE IF NOT EXISTS event_rejects (
    file_id     INTEGER NOT NULL REFERENCES source_files(file_id),
    line_number INTEGER NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('malformed', 'duplicate')),
    event_id    TEXT,                 -- for duplicates; never the raw line (may hold PII)
    detail      TEXT,
    PRIMARY KEY (file_id, line_number)
);
"""


class KeyMismatch(RuntimeError):
    pass


def connect(db_path: Path | str, crypto: Crypto | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None)  # explicit BEGIN/COMMIT
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(DDL)
    conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    if crypto is not None:
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('pii_key_fingerprint', ?)",
                     (crypto.fingerprint,))
        stored = conn.execute(
            "SELECT value FROM meta WHERE key = 'pii_key_fingerprint'").fetchone()[0]
        if stored != crypto.fingerprint:
            raise KeyMismatch("PII key does not match the key this database was created with; "
                              "phone matching would silently break. Refusing to continue.")
    return conn
