"""Report generation: the five output files of the contract."""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .config import Config
from .facts import BUCKETS, EventRec, Facts, LeadRec, derive
from .normalize import ist_date, ist_iso


def load_facts(conn: sqlite3.Connection, config: Config) -> Facts:
    leads = [LeadRec(*r) for r in conn.execute(
        "SELECT lead_id, workflow_id, phone_token, external_ref, batch_id, phone_last4 FROM leads")]
    events = [EventRec(*r) for r in conn.execute(
        "SELECT event_id, event_type, workflow_id, call_id, occurred_at_us, phone_token, "
        "disposition_code FROM events")]
    return derive(leads, events, config.dispositions)


def _rate(num: int, den: int) -> str:
    return str((Decimal(num) / Decimal(den)).quantize(Decimal("0.0001"), ROUND_HALF_UP))


def _bool(b: bool) -> str:
    return "true" if b else "false"


def _write_csv(path: Path, header: list[str], rows) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)  # never leave a half-written report behind


def batch_summary(facts: Facts) -> list[list]:
    counts: dict[tuple, dict[str, int]] = defaultdict(lambda: dict.fromkeys(BUCKETS, 0))
    for lf in facts.leads.values():
        counts[(lf.lead.workflow_id, lf.lead.batch_id)][lf.bucket] += 1
    return [[wf, b, sum(c.values()), c["yet_to_reach_out"], c["in_progress_telephony_failed"],
             c["in_progress_dialed"], c["completed"]]
            for (wf, b), c in sorted(counts.items())]


def daily_overview(facts: Facts, config: Config) -> list[list]:
    agg: dict[tuple, list] = defaultdict(lambda: [0, 0, set()])
    for call in facts.calls:
        lead = facts.leads[call.lead_id].lead
        a = agg[(lead.workflow_id, ist_date(call.initiated_us))]
        a[0] += 1
        a[1] += call.connected
        d = call.effective_disposition
        if d and config.dispositions[lead.workflow_id][d].is_success:
            a[2].add(call.lead_id)
    return [[wf, day, total, conn, _rate(conn, total), len(succ)]
            for (wf, day), (total, conn, succ) in sorted(agg.items())]


def lead_report(facts: Facts) -> list[list]:
    rows = []
    for lf in facts.leads.values():
        l = lf.lead
        rows.append([l.workflow_id, l.external_ref, l.batch_id, "+91******" + l.phone_last4,
                     lf.bucket, len(lf.calls), lf.connected_calls,
                     ist_iso(lf.last_call_us) if lf.last_call_us is not None else "",
                     lf.current_disposition or "", _bool(lf.ever_success)])
    # (workflow_id, external_ref) is not guaranteed unique (two phones, same policy number):
    # batch_id and the masked phone make the order deterministic anyway.
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    return rows


def rejections(conn: sqlite3.Connection) -> list[list]:
    return [list(r) for r in conn.execute(
        "SELECT f.workflow_id, f.batch_id, r.row_number, r.reason "
        "FROM lead_rows r JOIN source_files f USING (file_id) "
        "WHERE r.outcome = 'rejected' ORDER BY f.workflow_id, f.batch_id, r.row_number")]


def data_quality(conn: sqlite3.Connection, facts: Facts) -> dict:
    one = lambda sql: conn.execute(sql).fetchone()[0] or 0  # noqa: E731
    by_reason = dict(conn.execute(
        "SELECT reason, COUNT(*) FROM lead_rows WHERE outcome='rejected' "
        "GROUP BY reason ORDER BY reason").fetchall())
    return {
        "lead_rows_total": one("SELECT COUNT(*) FROM lead_rows"),
        "lead_rows_rejected": one("SELECT COUNT(*) FROM lead_rows WHERE outcome='rejected'"),
        "rejections_by_reason": by_reason,
        "duplicate_lead_rows_merged": one("SELECT COUNT(*) FROM lead_rows WHERE outcome='merged'"),
        "field_warnings": one(
            "SELECT SUM(field_warnings) FROM lead_rows WHERE outcome <> 'rejected'"),
        "events_total": one("SELECT SUM(lines_total) FROM source_files WHERE kind='events'"),
        "malformed_events": one("SELECT COUNT(*) FROM event_rejects WHERE kind='malformed'"),
        "duplicate_events_dropped": one(
            "SELECT COUNT(*) FROM event_rejects WHERE kind='duplicate'"),
        "orphan_events": facts.orphan_events,
        "unknown_disposition_events": facts.unknown_disposition_events,
    }


def write_reports(conn: sqlite3.Connection, config: Config, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    conn.execute("BEGIN")  # one consistent snapshot even if an ingest runs concurrently
    try:
        facts = load_facts(conn, config)
        rej = rejections(conn)
        dq = data_quality(conn, facts)
    finally:
        conn.execute("COMMIT")

    _write_csv(out_dir / "batch_summary.csv",
               ["workflow_id", "batch_id", "total_leads", "yet_to_reach_out",
                "in_progress_telephony_failed", "in_progress_dialed", "completed"],
               batch_summary(facts))
    _write_csv(out_dir / "daily_overview.csv",
               ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate",
                "success_leads"],
               daily_overview(facts, config))
    _write_csv(out_dir / "lead_report.csv",
               ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket",
                "total_calls", "connected_calls", "last_call_at", "current_disposition",
                "ever_success"],
               lead_report(facts))
    _write_csv(out_dir / "rejections.csv",
               ["workflow_id", "batch_id", "row_number", "reason"], rej)
    tmp = out_dir / "data_quality.json.tmp"
    tmp.write_text(json.dumps(dq, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out_dir / "data_quality.json")
    return dq
