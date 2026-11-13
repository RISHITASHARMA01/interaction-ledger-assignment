"""Lead CSV ingestion: header mapping, validation, normalisation, rejection, merging."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import Field, WorkflowSchema
from .crypto import Crypto
from .normalize import (InvalidValue, normalise_header, normalise_phone, parse_date,
                        parse_decimal, parse_enum)


class BatchConflict(RuntimeError):
    """The same (workflow, batch_id) was already ingested with different content."""


@dataclass
class RowResult:
    values: dict[str, str | None] | None  # canonical values; None when rejected
    reason: str | None = None
    warnings: int = 0


def _parse(field: Field, raw: str) -> str:
    if field.type == "string":
        return raw
    if field.type == "phone":
        return normalise_phone(raw)
    if field.type == "date":
        return parse_date(raw)
    if field.type == "decimal":
        return parse_decimal(raw)
    if field.type == "enum":
        return parse_enum(raw, list(field.values))
    raise AssertionError(field.type)


def validate_row(schema: WorkflowSchema, raw: dict[str, str]) -> RowResult:
    """Apply the spec's rejection rules to one row of already-trimmed raw values.

    Walk fields in schema order; the first failure is the row's single reason.
    """
    values: dict[str, str | None] = {}
    warnings = 0
    for f in schema.fields:
        v = raw.get(f.name, "")
        if v == "":
            if f.required:
                return RowResult(None, f"missing_required:{f.name}")
            values[f.name] = None
            continue
        try:
            values[f.name] = _parse(f, v)
        except InvalidValue:
            if f.type == "phone":
                return RowResult(None, "invalid_phone")
            if f.required:
                # Not covered by the spec's two reasons (no shipped schema has a required
                # date/decimal/enum). A required field we cannot use is not a usable lead.
                return RowResult(None, f"invalid_required:{f.name}")
            values[f.name] = None
            warnings += 1
    return RowResult(values, None, warnings)


def read_rows(text: str, schema: WorkflowSchema):
    """Yield (row_number, {field_name: trimmed value}) for each non-blank data row."""
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None:
        return
    col_to_field: dict[int, str] = {}
    taken: set[str] = set()
    for i, h in enumerate(header):
        name = schema.header_map.get(normalise_header(h))
        if name is not None and name not in taken:  # first matching column wins
            col_to_field[i] = name
            taken.add(name)
    row_number = 0
    for cells in reader:
        if all(not c.strip() for c in cells):
            continue  # blank row: skipped and not counted
        row_number += 1
        raw = {name: (cells[i].strip() if i < len(cells) else "")
               for i, name in col_to_field.items()}
        yield row_number, raw


def ingest_lead_file(conn: sqlite3.Connection, crypto: Crypto, schema: WorkflowSchema,
                     path: Path) -> bool:
    """Ingest one batch file atomically. Returns False if it was already ingested."""
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    batch_id = path.stem
    wf = schema.workflow_id

    conn.execute("BEGIN IMMEDIATE")
    try:
        prev = conn.execute(
            "SELECT sha256 FROM source_files WHERE kind='leads' AND workflow_id=? AND batch_id=?",
            (wf, batch_id)).fetchone()
        if prev is not None:
            conn.execute("ROLLBACK")
            if prev["sha256"] != sha:
                raise BatchConflict(
                    f"{wf}/{batch_id} was already ingested with different content; "
                    "refusing to re-ingest (batches are immutable once loaded)")
            return False

        rows = list(read_rows(data.decode("utf-8-sig"), schema))
        file_id = conn.execute(
            "INSERT INTO source_files (kind, workflow_id, batch_id, path, sha256, lines_total) "
            "VALUES ('leads', ?, ?, ?, ?, ?)",
            (wf, batch_id, str(path), sha, len(rows))).lastrowid

        pii_fields = [f.name for f in schema.fields if f.pii]
        attr_fields = [f.name for f in schema.fields if not f.pii]
        phone_name = schema.phone_field.name
        ref_name = schema.reference_field.name

        for row_number, raw in rows:
            res = validate_row(schema, raw)
            if res.values is None:
                conn.execute(
                    "INSERT INTO lead_rows (file_id, row_number, outcome, reason) "
                    "VALUES (?, ?, 'rejected', ?)", (file_id, row_number, res.reason))
                continue
            v = res.values
            phone = v[phone_name]
            token = crypto.phone_token(phone)
            existing = conn.execute(
                "SELECT lead_id, pii_enc, attrs_json FROM leads "
                "WHERE workflow_id=? AND phone_token=?", (wf, token)).fetchone()
            if existing is None:
                lead_id = conn.execute(
                    "INSERT INTO leads (workflow_id, phone_token, phone_last4, external_ref, "
                    " batch_id, pii_enc, attrs_json, created_file_id, updated_file_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (wf, token, phone[-4:], v[ref_name], batch_id,
                     crypto.encrypt_json({k: v[k] for k in pii_fields}),
                     json.dumps({k: v[k] for k in attr_fields}, sort_keys=True),
                     file_id, file_id)).lastrowid
                outcome = "created"
            else:
                # Merge: later row overwrites only the fields where it has a value.
                lead_id = existing["lead_id"]
                pii = crypto.decrypt_json(existing["pii_enc"])
                attrs = json.loads(existing["attrs_json"])
                for k in pii_fields:
                    if v[k] is not None:
                        pii[k] = v[k]
                for k in attr_fields:
                    if v[k] is not None:
                        attrs[k] = v[k]
                conn.execute(
                    "UPDATE leads SET pii_enc=?, attrs_json=?, external_ref=?, updated_file_id=? "
                    "WHERE lead_id=?",
                    (crypto.encrypt_json(pii), json.dumps(attrs, sort_keys=True),
                     attrs[ref_name], file_id, lead_id))
                outcome = "merged"
            conn.execute(
                "INSERT INTO lead_rows (file_id, row_number, outcome, lead_id, field_warnings) "
                "VALUES (?, ?, ?, ?, ?)", (file_id, row_number, outcome, lead_id, res.warnings))
        conn.execute("COMMIT")
        return True
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def effective_attributes(schema: WorkflowSchema, attrs: dict) -> dict:
    """Stored attributes with schema defaults applied to fields still null after merging."""
    out = dict(attrs)
    for f in schema.fields:
        if not f.pii and out.get(f.name) is None and f.default is not None:
            out[f.name] = f.default
    return out
