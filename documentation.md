# Local Setup & Usage

All commands run from **inside the `solution/` folder**:

```bash
cd sde-assignment-junior-2026/solution
```

Everything the commands produce (`.venv/`, `data/`, `reports/`, `*.db`, `*.key`, `*.egg-info/`) is
git-ignored, so none of it will be committed by accident.

---

## 1. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Python | 3.11 or newer (tested on 3.14) | `python3 --version` |
| `sqlite3` CLI | any. *Optional*: only for looking inside the DB in the manual tests | `sqlite3 --version` |

No Docker or Postgres needed. The pipeline uses SQLite through Python's standard library.

## 2. One-time setup

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r ../requirements.txt   # cryptography + pytest (plus Assignment B's deps; harmless)
pip install -e .                     # installs the `ledger` command from this folder
```

Check it worked:

```bash
ledger --help
```

Expected: a usage line `ledger [-h] [--db DB] [--schemas SCHEMAS] [--key-file KEY_FILE] {ingest,report} ...`

> Every new terminal needs `source .venv/bin/activate` again.

## 3. Generate the input data

```bash
python ../assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
```

Expected:
```
Generated seed=42 scale=small into data/
  drop_1: 4247 event lines
  drop_2: 4498 event lines
```

Optional large dataset (~144k lead rows, ~633k event lines), for the performance check:
```bash
python ../assignment-a-data/tools/generate_data.py --seed 42 --scale large --out data/large
```

## 4. Run the pipeline

```bash
ledger ingest data/drop_1
ledger ingest data/drop_2
ledger report --out reports/final
```

This creates `ledger.db` (the database) and `ledger.db.key` (the PII key, permission `0600`) in
`solution/`, and writes five files to `reports/final/`:

| File | Contents |
|---|---|
| `batch_summary.csv` | leads per workflow and batch, split by bucket |
| `daily_overview.csv` | calls, connected calls, connectivity rate and success leads per workflow per IST date |
| `lead_report.csv` | one row per lead (phone masked) |
| `rejections.csv` | rejected lead rows and their reasons (no PII) |
| `data_quality.json` | counters: rows, rejections, merges, warnings, events, malformed, duplicates, orphans, unknown dispositions |

### Command reference

```
ledger [--db PATH] [--schemas DIR] [--key-file PATH] ingest PATH [PATH ...]
ledger [--db PATH] [--schemas DIR] [--key-file PATH] report --out DIR
```

| Option | Default | Meaning |
|---|---|---|
| `--db` | `$LEDGER_DB`, else `./ledger.db` | SQLite database file |
| `--schemas` | `../assignment-a-data/schemas` (found automatically) | workflow schemas + `dispositions.csv` |
| `--key-file` | `<db>.key` | PII key file, used only when `$LEDGER_PII_KEY` is not set |
| `ingest PATH` | — | a drop folder (has `leads/` and/or `events/`), or a folder of drops (e.g. `data`), processed in name order |
| `report --out DIR` | — | folder for the five report files (created if missing) |

| Env var | Meaning |
|---|---|
| `LEDGER_PII_KEY` | PII master key, urlsafe base64 of 32 bytes. Generate one: `python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `LEDGER_DB` | default database path |
| `LEDGER_SCHEMAS_DIR` | default schemas folder |

**Exit codes:** `0` OK · `2` wrong PII key for this database · `3` batch already ingested with
different content.

> **The key and the database belong together.** If you delete `ledger.db.key` but keep
> `ledger.db`, the next run is refused (exit 2). To start fresh, delete both:
> `rm -f ledger.db* `.

## 5. Run the automated tests

```bash
pytest            # all 88 tests, ~2 s
pytest -v         # one line per test
```

Details of every test, plus the manual checks: [`test-suite.md`](test-suite.md).

## 6. There are no endpoints

Assignment A is a **command-line batch pipeline**. There is no HTTP server, API or UI. The
two "entry points" are `ledger ingest` and `ledger report`. (The FastAPI mock server in
`../assignment-b-workflow` belongs to Assignment B and isn't used here.)

## 7. Code map

```
ledger/
  cli.py            the `ledger` command: ingest / report
  normalize.py      header, phone, date, decimal, enum, timestamp (IST) rules
  config.py         loads workflow schemas + disposition catalogue
  crypto.py         PII: HMAC blind index for phones, Fernet encryption
  db.py             SQLite tables, append-only triggers, key-fingerprint check
  ingest_leads.py   CSV → validate / reject / merge → leads, lead_rows
  ingest_events.py  JSONL → malformed / duplicate → events (the ledger), event_rejects
  facts.py          linking, calls, effective disposition, buckets (pure functions)
  report.py         writes the five report files
tests/              pytest suite
```

Design, decisions and trade-offs: [`../SUBMISSION.md`](../SUBMISSION.md).

## 8. Reset / clean up

```bash
rm -rf ledger.db* all.db* mini.db* s123_*.db* large.db* reports/ data/
```

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ledger: command not found` | venv not active → `source .venv/bin/activate`; or `pip install -e .` not run |
| `No module named ledger` | same as above (or you're using a different Python) |
| `ModuleNotFoundError: cryptography` | `pip install -r ../requirements.txt` |
| `error: PII key does not match ...` (exit 2) | the key changed for an existing DB. Use the original key, or delete `ledger.db*` and re-ingest |
| `error: ... already ingested with different content` (exit 3) | a batch file with the same name was changed after loading. Batches are immutable; use a new batch name or start a fresh DB |
| Every event is an orphan | leads not ingested into **the same** `--db`, or the wrong `--schemas` folder |
