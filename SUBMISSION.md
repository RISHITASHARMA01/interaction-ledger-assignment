# Submission

## Candidate
- **Name:** RISHITA SHARMA
- **Assignment chosen:** A (Data): Interaction Ledger & Reporting Pipeline
- **Time spent (honest estimate):** 15-20 HOURS

## How to run
```bash
# setup (Python 3.11+; developed and tested on 3.14)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # only `cryptography` (+ pytest) is used by solution A

# generate data
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

# run the pipeline (the package lives in solution/; either export PYTHONPATH or `pip install -e solution`)
export PYTHONPATH=solution
python -m ledger ingest data/drop_1
python -m ledger ingest data/drop_2
python -m ledger report --out reports/

# everything at once gives identical reports (a dir of drops is taken in name order):
python -m ledger --db all.db ingest data && python -m ledger --db all.db report --out reports_all/

# tests (one command, from the repo root)
pytest
```
Options: `--db PATH` (default `./ledger.db`, or `$LEDGER_DB`), `--schemas DIR` (default
`assignment-a-data/schemas`), `--key-file PATH`. The PII master key is read from `$LEDGER_PII_KEY`
(urlsafe base64 of 32 bytes). If that is unset, a key is generated once into `<db>.key` (0600,
git-ignored). **The key and the DB belong together.** A run with a different key is refused (exit 2).

## Design

```
 CSV batches ──► ingest_leads ──► leads (1 row / workflow+phone) ─┐
   (validate, normalise,          lead_rows (created|merged|      │   report time (pure Python,
    reject, merge)                 rejected + reason, no PII)     ├──► facts.derive(): link → calls →
 JSONL events ─► ingest_events ─► events  ◄── APPEND-ONLY LEDGER  │    effective disposition → bucket
   (malformed? duplicate?)        event_rejects (malformed|dup)  ─┘    ──► 5 report files
 every file ──► source_files (sha256; the idempotency guard + per-file line counts)
```

The **one idea that drives the design:** ingestion stores only *facts that never change*
(a row was rejected for reason X, an event with id Y arrived). Anything that depends on *what else
has arrived* (is this event an orphan? which lead does an override belong to? is this disposition
known in a linked context?) is **derived at report time** from the ledger. That is why an orphan in
drop_1 counts as soon as its lead arrives in drop_2, why incremental and all-at-once give
byte-identical reports, and why the ledger never needs an `UPDATE`.

**Schema (SQLite, `solution/ledger/db.py`):**

| Table | Key / constraints | Why |
|---|---|---|
| `source_files` | unique `(workflow_id, batch_id)` for leads, unique `sha256` for event files | Idempotency guard. Each file is ingested in **one transaction** together with its `source_files` row, so a crash leaves the file either fully in or fully out. Re-running skips it. Also stores `lines_total` for `events_total`. |
| `leads` | `UNIQUE(workflow_id, phone_token)` | Lead identity enforced by the DB. `batch_id` is set on insert and never updated. `pii_enc` = Fernet-encrypted JSON of every `pii:true` field. `attrs_json` = non-PII fields (nulls kept; defaults applied on read). `external_ref` and `phone_last4` are plaintext columns for the report. |
| `lead_rows` | PK `(file_id, row_number)`, CHECK that rejected ⇔ has reason | Row-level lineage: `created` / `merged` / `rejected` + `field_warnings`. `rejections.csv` and all lead counters in `data_quality.json` are plain `COUNT`s over it, with no separate counters to drift. |
| `events` | PK `event_id` (dedupe across drops is the DB's job), triggers abort any `UPDATE`/`DELETE` | **The interaction ledger.** Stores normalised columns (UTC epoch µs, phone *token*, disposition code) plus the raw timestamp string and payload for audit. Orphans are kept: they may link later. Indexed on `(workflow_id, phone_token)` and `call_id`. |
| `event_rejects` | PK `(file_id, line_number)` | Malformed lines and duplicate deliveries, with the reason / event_id, **never the raw line** (it may contain a phone). |

**PII at rest (`crypto.py`):** one master secret → two derived keys (HMAC-based KDF):
- **Blind index:** `phone_token = HMAC-SHA256(k_index, "+91XXXXXXXXXX")`. Deterministic, so event→lead
  matching is a plain indexed equality join, but it is keyed, so it can't be reversed by
  brute-forcing the ~4·10⁹ possible mobile numbers (an unkeyed SHA-256 could be).
- **Encryption:** Fernet (AES-CBC + HMAC, random IV) for name and phone, so an authorised process can
  still recover them (to actually dial).
- Event phones are never stored, only their token. Rejected rows store no values at all.
  `phone_last4` is stored in plaintext. Four digits are not identifying on their own, and the report
  needs them for the mask. A test greps the raw DB files (incl. WAL) for names and phones.

**Linking rules (`facts.py`):** a non-override event links via `(workflow_id, token)`. A call's lead
is the lead of its `call.initiated` (or, if absent, of its earliest linked event). A
`disposition.override` links via `(workflow_id, call_id)` to that lead. Only linked events whose lead
equals the call's lead contribute to a call.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Orphan / unknown-disposition / override-linking are computed at report time, not stored | Correctness under late-arriving leads with zero bookkeeping; ledger stays append-only | Materialised `event_links` table refreshed on ingest (faster reports; must be invalidated correctly) |
| Lead batch re-ingested with **different content** → refuse (exit 3) | Batch ids are permanent and order-sensitive (first row wins), so silently re-merging would rewrite history | Treat as a new batch / last-write-wins |
| Event files are identified by content hash; event dedupe by `event_id` (first seen wins) | A renamed/copied file is still a no-op; overlapping files are still safe | Path-based identity |
| A required non-phone field that is present but invalid → reject `invalid_required:<field>` | Spec only names two reasons; no shipped schema hits this. A lead lacking a required value is unusable | Treat like optional (null + warning) |
| Two CSV columns mapping to the same field → the first one wins | Deterministic; the generator never does it | Last wins / reject file |
| Short CSV rows → missing cells are missing values; extra cells ignored | Lenient, matches "ignore columns that match nothing" | Reject the row |
| Defaults are not stored; applied on read (`effective_attributes`) | Equivalent to "apply after merging" and keeps what the client actually sent | Write default into the row after merge |
| Event with valid JSON + `event_id` but missing/unparseable `occurred_at` (or a non-object line) → **malformed** | It can't be dated or ordered, so it can't be used anywhere. Never happens in generated data | Store with NULL time |
| `event_id` is trimmed; `phone` absent/blank and `phone` invalid are both orphans (unless override) | Spec: linking uses the phone rules | — |
| Ties: latest override/set by `(occurred_at, event_id)`; most recent call by `(initiated_at, call_id)`; duplicate `call.initiated` for one call → earliest | Deterministic regardless of ingestion order | Arrival order (would make reports depend on how drops were split) |
| An event linked to a lead but whose call has no `call.initiated` is **not** an orphan (it just isn't a call) | Spec defines orphan as "can't be linked to a valid lead" | Count it as orphan |
| An unknown disposition on an **orphan** event counts as orphan only | Spec order: orphan check (3) precedes unknown check (4) | Count in both |
| Disposition validity is per workflow (`feedback_captured` on a motor call is unknown) | Catalogue is keyed by workflow | Global code list |
| `connectivity_rate` rounded half-up via `Decimal` | Exact and reproducible | float `:.4f` (banker-ish binary edge cases) |
| `last_call_at` prints microseconds only if the input had them | Python `isoformat`; inputs are whole seconds | Always truncate to seconds |
| `lead_report` tiebreak after `(workflow_id, external_ref)`: `batch_id`, `phone_masked` | `external_ref` isn't guaranteed unique across phones | — |
| Batches whose rows were all rejected don't appear in `batch_summary` | "total_leads" is about leads; such a batch has none | Emit an all-zero row |
| Reports written to `*.tmp` then `os.replace` | A crash never leaves a half-written report | — |
| SQLite (WAL) | Zero setup, single-writer workload, plenty fast | Postgres (see production note) |

## Failure modes considered
- **Re-runs:** every file is checked against `source_files` inside the same transaction that would
  ingest it, so re-runs are no-ops (tested: re-ingest, mixed re-ingest, all-at-once → identical bytes).
- **Crash mid-ingest:** one transaction per file → the file is either fully committed with its
  `source_files` row or not at all; next run picks it up. Crash mid-report → old reports intact.
- **Duplicates:** lead rows merge by `(workflow, phone)` (DB-enforced unique); events dedupe by PK,
  including within a file and across drops.
- **Dirty input:** blank rows, BOM, renamed/re-ordered/extra columns, short rows, six phone
  formats, invalid dates (31/02), currency formats, truncated JSON lines, non-object JSON, missing
  event_id/phone, unknown codes, unknown workflow folders (skipped with a warning and *not* recorded, so
  they ingest once a schema exists).
- **Wrong PII key:** detected through a stored key fingerprint, refused (otherwise every event would
  silently become an orphan).
- **Tampering with the ledger:** `UPDATE`/`DELETE` on `events` abort via triggers (tested).
- **Not handled:** lead *deletions/opt-outs* (no such input); a schema change between drops (old rows
  aren't re-validated); concurrent ingests are serialised by SQLite's write lock but not
  coordinated beyond that; a corrected re-delivery of a lead batch has no supported path (it's refused);
  key rotation (would need re-tokenising all leads and events).

## Testing
`pytest` from the repo root runs 88 tests in ~2s:
- `test_normalize.py`: phone (all six generator formats, landline, +1, 9-digit, bad prefix after
  stripping), dates (4 formats, case-insensitive months, leap years, impossible dates, mixed
  separators), decimals (prefixes, Indian comma grouping, half-up rounding, negatives), enum,
  header normalisation, the three timestamp forms, IST midnight crossing.
- `test_leads.py`: rejection order, warnings, alias headers + blank rows + extra columns, merge
  last-write-wins with permanent `batch_id`, defaults after merge, **PII absent from the raw DB
  bytes**, idempotent re-ingest and conflicting re-ingest.
- `test_facts.py`: effective disposition (override beats a later set, latest wins), override linking
  via call_id regardless of arrival order, unknown codes (incl. another workflow's code),
  orphans (unknown phone, no phone, wrong workflow, override for unknown call), orphan → linked when
  the lead appears, bucket precedence, current disposition, duplicate `call.initiated`.
- `test_pipeline.py`: end-to-end on **seed 7** through the CLI: incremental == re-ingested ==
  all-at-once (byte-identical), cross-report invariants, append-only triggers, wrong-key refusal.
- Also cross-checked by hand: a throwaway, DB-free recomputation from the raw files reproduced
  `daily_overview.csv`, orphan and unknown counts exactly for seed 42. Two values quoted in the
  README (`0.5625`, `2026-10-14T17:55:24+05:30` for `MOT/2026/100000`) appear in my seed-42 output.
- Not tested: concurrency, very large single files beyond the `large` scale, Postgres.

## Results
Seed 42, `small`: 2057 lead rows, 108 rejected, 57 merged → 1892 leads; 8745 event lines,
8 malformed, 237 duplicates, 462 orphans (1123 after drop_1 alone, which shows late leads resolving),
18 unknown dispositions, 122 field warnings.

Timings (laptop, 12 cores but single-threaded, Python 3.14, SQLite WAL), `--scale large`
(143,990 lead rows / 632,602 event lines):

| Step | Time |
|---|---|
| `ingest drop_1` | 21.7 s |
| `ingest drop_2` | 20.6 s |
| `report` | 6.5 s |
| **Total** | **~49 s** (target < 2 min) |

Surprises in the data: the same phone appears with different formats *within one call's events*
(normalisation before hashing is essential); rejected leads still receive calls (→ orphans); overrides
can land a drop later than the call; `"english"`/`"Hindi"` are invalid enum values, not aliases.

**Stretch goals done:** performance (above); truly incremental ingest (drop_2 never re-reads or
re-processes drop_1's files. Trade-off: *reports* re-derive from the whole ledger each time, which
is O(all events) but is what makes late-arriving leads trivially correct); production note (below).

### Running this in production at 100k leads/day
- **Storage:** Postgres. `events` partitioned by month on `occurred_at`, same unique/PK constraints;
  the append-only rule enforced by revoking `UPDATE/DELETE` from the app role (plus triggers).
  PII key in KMS, envelope-encrypted, with the fingerprint check kept.
- **Scheduling:** an orchestrator (Airflow/Temporal/cron + queue) triggers `ingest` per arriving file
  (object-store notification), and `report` on a schedule (e.g. 06:00 IST) and after each drop.
  Each file is an idempotent task keyed by its hash, so retries are free.
- **Failures:** per-file transactions + retries with backoff; a poison file (e.g. batch conflict)
  goes to a dead-letter area and alerts ops instead of blocking other files. Data-quality thresholds
  (rejection rate, orphan rate, malformed rate spikes) alert before the client sees the report.
- **Scale:** at ~5× the `large` set per day the full re-derive will eventually get slow. Then:
  materialise links/call facts incrementally (recompute only leads touched by new events or new
  leads, which is exactly the set we know at ingest time), and do aggregation in SQL.
- **Backfills:** because reports are a pure function of the ledger, a backfill is "ingest the missing
  files, re-run report". Historical snapshots come from filtering by `source_files.ingested_at`.

## If I had another day
1. Push report aggregation into SQL (window functions for effective disposition), and materialise
   per-call facts incrementally for leads touched by a new drop.
2. A supported path for corrected batch re-uploads (versioned batches with explicit supersede).
3. Validate config: catalogue/schema sanity checks, schema versioning per batch.
4. Property-based tests (Hypothesis) for normalisers and for "any split of the input into drops gives
   the same reports".
5. Postgres backend behind the same interface plus a migration tool.
