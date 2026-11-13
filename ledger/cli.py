"""Command line interface.

    python -m ledger [--db ledger.db] ingest data/drop_1 [data/drop_2 ...]
    python -m ledger [--db ledger.db] ingest data            # every drop_* under data/, in order
    python -m ledger [--db ledger.db] report --out reports/
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .config import DEFAULT_SCHEMAS_DIR, Config
from .crypto import Crypto
from .db import KeyMismatch, connect
from .ingest_events import ingest_event_file
from .ingest_leads import BatchConflict, ingest_lead_file
from .report import write_reports


def _is_drop(p: Path) -> bool:
    return (p / "leads").is_dir() or (p / "events").is_dir()


def resolve_drops(paths: list[str]) -> list[Path]:
    """Each argument is a drop, or a directory of drops (taken in name order)."""
    drops: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_dir():
            raise SystemExit(f"error: {p} is not a directory")
        if _is_drop(p):
            drops.append(p)
        else:
            subs = sorted(d for d in p.iterdir() if d.is_dir() and _is_drop(d))
            if not subs:
                raise SystemExit(f"error: {p} has no leads/ or events/ (and no drops inside)")
            drops.extend(subs)
    return drops


def ingest(conn, crypto: Crypto, config: Config, drops: list[Path], log=print) -> None:
    for drop in drops:
        leads_dir = drop / "leads"
        if leads_dir.is_dir():
            for wf_dir in sorted(d for d in leads_dir.iterdir() if d.is_dir()):
                schema = config.schemas.get(wf_dir.name)
                if schema is None:
                    # Not recorded, so it is picked up once a schema exists.
                    log(f"  WARNING skipping {wf_dir}: no schema for workflow '{wf_dir.name}'")
                    continue
                for f in sorted(wf_dir.glob("*.csv")):
                    done = ingest_lead_file(conn, crypto, schema, f)
                    log(f"  {'ingested' if done else 'skipped (already ingested)'}: {f}")
        events_dir = drop / "events"
        if events_dir.is_dir():
            for f in sorted(events_dir.glob("*.jsonl")):
                done = ingest_event_file(conn, crypto, f)
                log(f"  {'ingested' if done else 'skipped (already ingested)'}: {f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ledger", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("LEDGER_DB", "ledger.db"),
                    help="SQLite database path (default: $LEDGER_DB or ./ledger.db)")
    ap.add_argument("--schemas", default=str(DEFAULT_SCHEMAS_DIR),
                    help="directory with <workflow>.json schemas and dispositions.csv")
    ap.add_argument("--key-file", default=None,
                    help="PII key file used when $LEDGER_PII_KEY is unset (default: <db>.key)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_ing = sub.add_parser("ingest", help="ingest one or more drops (idempotent)")
    p_ing.add_argument("paths", nargs="+")
    p_rep = sub.add_parser("report", help="write the five report files")
    p_rep.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    config = Config.load(args.schemas)
    key_file = Path(args.key_file) if args.key_file else Path(args.db + ".key")
    crypto = Crypto.from_env_or_file(key_file)
    try:
        conn = connect(args.db, crypto)
    except KeyMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    t0 = time.perf_counter()
    try:
        if args.cmd == "ingest":
            drops = resolve_drops(args.paths)
            for d in drops:
                print(f"drop {d}")
            ingest(conn, crypto, config, drops)
            print(f"ingest finished in {time.perf_counter() - t0:.2f}s")
        else:
            dq = write_reports(conn, config, Path(args.out))
            print(f"reports written to {args.out} in {time.perf_counter() - t0:.2f}s")
            print(f"  leads rows={dq['lead_rows_total']} rejected={dq['lead_rows_rejected']} "
                  f"events={dq['events_total']} orphans={dq['orphan_events']}")
    except BatchConflict as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
