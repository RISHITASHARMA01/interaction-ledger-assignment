"""End-to-end: generated data on a seed other than 42, through the real CLI."""
import csv
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ledger.cli import main

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "assignment-a-data" / "tools" / "generate_data.py"
FILES = ["batch_summary.csv", "daily_overview.csv", "lead_report.csv", "rejections.csv",
         "data_quality.json"]


@pytest.fixture(scope="module")
def data(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("gen") / "data"
    subprocess.run([sys.executable, str(GENERATOR), "--seed", "7", "--scale", "small",
                    "--out", str(out)], check=True, capture_output=True)
    return out


def reports(d: Path) -> dict[str, str]:
    return {f: (d / f).read_text() for f in FILES}


def cli(db: Path, *args):
    assert main(["--db", str(db), *map(str, args)]) == 0


def test_incremental_equals_all_at_once_and_reingest_is_noop(data, tmp_path, pii_env):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    cli(a, "ingest", data / "drop_1")
    cli(a, "ingest", data / "drop_2")
    cli(a, "report", "--out", tmp_path / "r1")
    cli(a, "ingest", data / "drop_1")          # re-runs change nothing
    cli(a, "ingest", data / "drop_2", data / "drop_1")
    cli(a, "report", "--out", tmp_path / "r2")
    cli(b, "ingest", data)                     # everything at once
    cli(b, "report", "--out", tmp_path / "r3")
    r1 = reports(tmp_path / "r1")
    assert r1 == reports(tmp_path / "r2") == reports(tmp_path / "r3")


def test_report_invariants(data, tmp_path, pii_env):
    db = tmp_path / "x.db"
    cli(db, "ingest", data)
    cli(db, "report", "--out", tmp_path / "r")
    r = tmp_path / "r"
    dq = json.loads((r / "data_quality.json").read_text())
    leads = list(csv.DictReader(open(r / "lead_report.csv")))
    batches = list(csv.DictReader(open(r / "batch_summary.csv")))
    daily = list(csv.DictReader(open(r / "daily_overview.csv")))
    rej = list(csv.DictReader(open(r / "rejections.csv")))

    assert len(leads) == (dq["lead_rows_total"] - dq["lead_rows_rejected"]
                          - dq["duplicate_lead_rows_merged"])
    assert len(rej) == dq["lead_rows_rejected"] == sum(dq["rejections_by_reason"].values())
    assert sum(int(b["total_leads"]) for b in batches) == len(leads)
    assert sum(int(d["total_calls"]) for d in daily) == sum(int(l["total_calls"]) for l in leads)
    assert sum(int(d["connected_calls"]) for d in daily) == sum(
        int(l["connected_calls"]) for l in leads)
    lines = sum(1 for f in data.glob("drop_*/events/*.jsonl")
                for ln in f.read_text().splitlines() if ln.strip())
    assert dq["events_total"] == lines
    for l in leads:
        assert l["phone_masked"].startswith("+91******") and len(l["phone_masked"]) == 13
        assert (l["bucket"] == "yet_to_reach_out") == (l["total_calls"] == "0")
    assert [(l["workflow_id"], l["external_ref"]) for l in leads] == sorted(
        (l["workflow_id"], l["external_ref"]) for l in leads)
    for d in daily:
        assert len(d["connectivity_rate"].split(".")[1]) == 4


def test_ledger_is_append_only(data, tmp_path, pii_env):
    db = tmp_path / "x.db"
    cli(db, "ingest", data / "drop_1")
    c = sqlite3.connect(db)
    for sql in ("UPDATE events SET disposition_code='renewed'", "DELETE FROM events"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute(sql)


def test_wrong_pii_key_is_refused(data, tmp_path, monkeypatch, pii_env):
    db = tmp_path / "x.db"
    cli(db, "ingest", data / "drop_1")
    monkeypatch.setenv("LEDGER_PII_KEY", "eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXk=")
    assert main(["--db", str(db), "report", "--out", str(tmp_path / "r")]) == 2
