# Solution: Assignment A (Interaction Ledger & Reporting Pipeline)

See [`../SUBMISSION.md`](../SUBMISSION.md) for run commands, design and decisions.

```
ledger/
  normalize.py      pure value rules: header, phone, date, decimal, enum, timestamps (IST)
  config.py         workflow schemas + disposition catalogue loader
  crypto.py         PII: HMAC blind index for phones + Fernet encryption
  db.py             SQLite DDL (append-only ledger triggers), key-fingerprint check
  ingest_leads.py   CSV → validate / reject / merge → leads, lead_rows
  ingest_events.py  JSONL → malformed / duplicate → events (ledger), event_rejects
  facts.py          pure: linking, calls, effective disposition, buckets
  report.py         the five output files
  cli.py            `python -m ledger [--db ..] ingest PATH... | report --out DIR`
tests/              pytest suite (run `pytest` from the repo root)
```
