"""Event JSONL ingestion into the append-only ledger.

Only steps 1 (malformed) and 2 (duplicate) of the spec happen here. Steps 3 (orphan) and 4
(unknown disposition) depend on which leads exist, which changes as later drops arrive, so they are
evaluated at report time (see linking.py). The ledger therefore keeps orphans too.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .crypto import Crypto
from .normalize import InvalidValue, normalise_phone, parse_timestamp, to_epoch_us


def _str_or_none(v) -> str | None:
    if v is None:
        return None
    return v if isinstance(v, str) else str(v)


def parse_event_line(line: str, crypto: Crypto) -> tuple[dict | None, str | None]:
    """Returns (row, None) for a well-formed event, or (None, why) for a malformed line."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, RecursionError):
        return None, "invalid_json"
    if not isinstance(obj, dict):
        return None, "not_an_object"
    event_id = obj.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        return None, "missing_event_id"
    # Decision: an event whose time we cannot read cannot be placed on any date or ordered
    # against overrides, so it is treated as malformed (documented in SUBMISSION.md).
    try:
        occurred = parse_timestamp(obj.get("occurred_at"))
    except InvalidValue:
        return None, "invalid_occurred_at"

    raw_phone = obj.get("phone")
    if raw_phone is None or (isinstance(raw_phone, str) and not raw_phone.strip()):
        token, status = None, "absent"
    else:
        try:
            token, status = crypto.phone_token(normalise_phone(str(raw_phone))), "valid"
        except InvalidValue:
            token, status = None, "invalid"

    payload = obj.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    code = payload.get("disposition_code")
    return {
        "event_id": event_id.strip(),
        "event_type": _str_or_none(obj.get("event_type")),
        "workflow_id": _str_or_none(obj.get("workflow_id")),
        "call_id": _str_or_none(obj.get("call_id")),
        "occurred_at_us": to_epoch_us(occurred),
        "occurred_at_raw": obj["occurred_at"],
        "phone_token": token,
        "phone_status": status,
        "disposition_code": code.strip() if isinstance(code, str) else _str_or_none(code),
        "payload_json": json.dumps(payload, sort_keys=True, ensure_ascii=False),
    }, None


def ingest_event_file(conn: sqlite3.Connection, crypto: Crypto, path: Path) -> bool:
    """Ingest one JSONL file atomically. Returns False if this exact file was already ingested."""
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM source_files WHERE kind='events' AND sha256=?",
                        (sha,)).fetchone():
            conn.execute("ROLLBACK")
            return False
        # (physical line number, text) for every non-empty line
        lines = [(n, ln) for n, ln in
                 enumerate(data.decode("utf-8-sig", errors="replace").splitlines(), start=1)
                 if ln.strip()]
        file_id = conn.execute(
            "INSERT INTO source_files (kind, path, sha256, lines_total) "
            "VALUES ('events', ?, ?, ?)", (str(path), sha, len(lines))).lastrowid

        insert = ("INSERT OR IGNORE INTO events (event_id, file_id, line_number, event_type, "
                  "workflow_id, call_id, occurred_at_us, occurred_at_raw, phone_token, "
                  "phone_status, disposition_code, payload_json) VALUES "
                  "(:event_id, :file_id, :line_number, :event_type, :workflow_id, :call_id, "
                  ":occurred_at_us, :occurred_at_raw, :phone_token, :phone_status, "
                  ":disposition_code, :payload_json)")
        rejects = []
        for n, line in lines:
            row, why = parse_event_line(line, crypto)
            if row is None:
                rejects.append((file_id, n, "malformed", None, why))
                continue
            row["file_id"], row["line_number"] = file_id, n
            if conn.execute(insert, row).rowcount == 0:  # event_id seen before (any drop)
                rejects.append((file_id, n, "duplicate", row["event_id"], None))
        conn.executemany("INSERT INTO event_rejects VALUES (?, ?, ?, ?, ?)", rejects)
        conn.execute("COMMIT")
        return True
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
