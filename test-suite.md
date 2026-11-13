# Test Suite

Run everything from **inside `solution/`** with the venv active (see
[`documentation.md`](documentation.md) §2–3). Tick each box as you confirm it.

- **Part A:** automated tests (`pytest`, 88 tests)
- **Part B:** manual checks, one by one, with the exact expected output
- **Part C:** requirement → test traceability

There are **no HTTP endpoints** in Assignment A. It's a CLI pipeline (`ledger ingest`,
`ledger report`), so the manual checks exercise those two commands and inspect the database.

---

## Part A: Automated tests

```bash
pytest -v
```

**Expected last line:** `88 passed in ~2s`

Run a single file or test:
```bash
pytest tests/test_normalize.py -v
pytest tests/test_facts.py::test_bucket_precedence -v
```

### A1. `tests/test_normalize.py`: value rules (64 tests)

| ✓ | Test | What it proves |
|---|---|---|
| ☐ | `test_phone_all_generator_formats` ×8 | `9876543210`, `+91 98765 43210`, `09876543210`, `91-98765-43210`, `(+91) 9876543210`, `+919876543210`, ` 98765-43210 `, `919876543210` → all `+919876543210` |
| ☐ | `test_phone_invalid` ×6 | 9 digits, starts with 5, landline `022-…`, `N/A`, `+1 415 555 0134`, `0091…` (14 digits) are invalid |
| ☐ | `test_ten_digits_starting_91_is_a_plain_mobile` | `9198765432` is a normal mobile; the `91` is not stripped |
| ☐ | `test_phone_12_digits_not_starting_91_is_invalid` | 12 digits without a `91` prefix are invalid |
| ☐ | `test_phone_prefix_stripped_then_validated` | `91`/`0` prefix removed, then the 6–9 first-digit rule still applies |
| ☐ | `test_dates_valid` ×8 | `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY`, `DD-Mon-YYYY` (any case), leap day, 1-digit day/month; day-first |
| ☐ | `test_dates_invalid` ×10 | `31/02`, `29/02/2026`, month 13, `soon`, `TBD`, US `12/31/2026`, bad month name, `2026/12/01`, mixed separators, empty |
| ☐ | `test_decimal_valid` ×11 | `₹`, `Rs`, `Rs.`, `INR` (any case, optional space), commas incl. Indian grouping, half-up rounding to 2 dp (`2.005 → 2.01`) |
| ☐ | `test_decimal_invalid` ×9 | `N/A`, `-`, words, negatives, bare `Rs`, `1.2.3`, `USD`, suffix currency |
| ☐ | `test_enum_case_insensitive_and_lowercased` | `HI → hi`; `Hindi` invalid |
| ☐ | `test_header_normalisation` ×5 | case, trim, runs of `_`/whitespace → one space |
| ☐ | `test_three_timestamp_forms_are_the_same_instant` | `…Z`, `…+05:30` and naive (= IST) are the same moment |
| ☐ | `test_ist_date_crosses_midnight` | `2026-10-01T19:00:00Z` → IST date `2026-10-02`, printed `2026-10-02T00:30:00+05:30` |
| ☐ | `test_bad_timestamp` | `None`, empty, text and numbers are rejected |

### A2. `tests/test_leads.py`: lead ingestion (9 tests)

| ✓ | Test | What it proves |
|---|---|---|
| ☐ | `test_first_failure_in_schema_order_wins` | single reason = first failing field in schema order; `missing_required:<f>` vs `invalid_phone` |
| ☐ | `test_optional_invalid_is_null_plus_one_warning_each` | bad optional date / decimal / enum → null, one warning **each** (3) |
| ☐ | `test_values_are_normalised` | stored values are canonical (phone, ISO date, `27554.00`, lower-case enum) |
| ☐ | `test_read_rows_aliases_blank_rows_extra_columns` | alias + re-ordered headers map; blank rows skipped **and not numbered**; extra columns ignored |
| ☐ | `test_read_rows_short_row_is_missing_values` | a row with fewer cells → missing values |
| ☐ | `test_merge_last_write_wins_and_batch_is_permanent` | duplicate across batches: `batch_id` stays `batch_001`; non-null values overwrite, nulls don't; merge counted |
| ☐ | `test_default_not_applied_before_merge` | defaults apply only after merging (a later explicit value wins) |
| ☐ | `test_pii_not_stored_in_plaintext` | names and phones (valid **and** rejected rows) are absent from the raw DB + WAL bytes |
| ☐ | `test_reingest_same_file_is_noop_and_changed_file_conflicts` | same file again = no-op; same batch with changed content = refused, DB unchanged |

### A3. `tests/test_facts.py`: linking, dispositions, buckets (11 tests)

| ✓ | Test | What it proves |
|---|---|---|
| ☐ | `test_override_beats_set_even_if_set_is_later` | any override beats any `disposition.set` |
| ☐ | `test_latest_override_and_latest_set_win` | latest by `occurred_at`, not by arrival order |
| ☐ | `test_override_links_through_call_id_and_arrival_order_is_irrelevant` | override (no phone) links via `call_id`, even if it arrives first |
| ☐ | `test_unknown_disposition_is_counted_and_ignored` | `RENEWD` and another workflow's code are counted and ignored |
| ☐ | `test_orphans` | unknown phone, missing phone, wrong workflow, override on unknown call → orphans; not counted as calls |
| ☐ | `test_orphan_becomes_linked_once_lead_exists` | orphan events count once their lead appears (late leads) |
| ☐ | `test_unknown_disposition_on_orphan_counts_only_as_orphan` | orphan check runs before the unknown-disposition check |
| ☐ | `test_call_needs_initiated` | no `call.initiated` → not a call (but not an orphan either) |
| ☐ | `test_bucket_precedence` | `completed` > `in_progress_telephony_failed` (all calls failed) > `in_progress_dialed`; an override can un-complete |
| ☐ | `test_current_disposition_is_most_recent_call_that_has_one` | skips the latest call if it has no disposition; `last_call_at` = latest initiated |
| ☐ | `test_duplicate_initiated_for_same_call_counts_once` | one call per `call_id` |

### A4. `tests/test_pipeline.py`: end to end through the CLI, on seed **7** (4 tests)

| ✓ | Test | What it proves |
|---|---|---|
| ☐ | `test_incremental_equals_all_at_once_and_reingest_is_noop` | drop_1 → drop_2 = re-ingest again = all at once: **byte-identical** reports |
| ☐ | `test_report_invariants` | leads = rows − rejected − merged; batch totals = lead count; daily calls = lead calls; `events_total` = non-empty lines; masking, sort order, 4-decimal rate |
| ☐ | `test_ledger_is_append_only` | `UPDATE`/`DELETE` on `events` fail with "append-only" |
| ☐ | `test_wrong_pii_key_is_refused` | a different key on an existing DB → exit code 2 |

---

## Part B: Manual checks (one by one)

Start clean, with the venv active and inside `solution/`:
```bash
rm -rf ledger.db* all.db* mini.db* s123_*.db* reports/
python ../assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
```
The expected numbers below are for **seed 42, small**.

### M1. First ingest of drop 1 ☐
```bash
ledger ingest data/drop_1
```
Expected:
```
drop data/drop_1
  ingested: data/drop_1/leads/motor_renewal/batch_001.csv
  ingested: data/drop_1/leads/motor_renewal/batch_002.csv
  ingested: data/drop_1/leads/policy_feedback/batch_001.csv
  ingested: data/drop_1/leads/policy_feedback/batch_002.csv
  ingested: data/drop_1/events/events_0001.jsonl
ingest finished in 0.2s
```
Also check: `ls -l ledger.db.key` shows `-rw-------` (key readable only by you).

### M2. Re-ingest is a no-op (idempotency) ☐
```bash
ledger ingest data/drop_1
```
Expected: all five lines say `skipped (already ingested): …`, exit code 0 (`echo $?` → `0`).

### M3. Report after drop 1 only (orphans waiting for their leads) ☐
```bash
ledger report --out reports/drop1_only
```
Expected last line: `leads rows=1515 rejected=71 events=4247 orphans=1123`

### M4. Drop 2 and the final reports ☐
```bash
ledger ingest data/drop_2
ledger report --out reports/final
ls reports/final
cat reports/final/data_quality.json
```
Expected last line of `report`: `leads rows=2057 rejected=108 events=8745 orphans=462`.
**Orphans dropped from 1123 → 462** because drop 2's leads claimed the earlier events.

`ls` shows: `batch_summary.csv  daily_overview.csv  data_quality.json  lead_report.csv  rejections.csv`

`data_quality.json`:
```json
{
  "lead_rows_total": 2057,
  "lead_rows_rejected": 108,
  "rejections_by_reason": {
    "invalid_phone": 42,
    "missing_required:customer_name": 25,
    "missing_required:order_id": 13,
    "missing_required:phone": 14,
    "missing_required:policy_number": 14
  },
  "duplicate_lead_rows_merged": 57,
  "field_warnings": 122,
  "events_total": 8745,
  "malformed_events": 8,
  "duplicate_events_dropped": 237,
  "orphan_events": 462,
  "unknown_disposition_events": 18
}
```
Arithmetic check: 2057 − 108 − 57 = **1892 leads** → `wc -l reports/final/lead_report.csv` = `1893` (1892 + header).

### M5. Matches the examples in the assignment README ☐
```bash
head -2 reports/final/daily_overview.csv
grep "MOT/2026/100000," reports/final/lead_report.csv
```
Expected:
```
workflow_id,date,total_calls,connected_calls,connectivity_rate,success_leads
motor_renewal,2026-10-01,80,45,0.5625,8
motor_renewal,MOT/2026/100000,batch_003,+91******1819,completed,4,1,2026-10-14T17:55:24+05:30,not_interested,false
```
`0.5625` and `2026-10-14T17:55:24+05:30` are the exact values quoted in the README.

### M6. Incremental = everything at once ☐
```bash
ledger --db all.db ingest data
ledger --db all.db report --out reports/all_at_once
diff -r reports/final reports/all_at_once && echo NO_DIFF
```
Expected: `NO_DIFF`

### M7. Report formats ☐
```bash
head -3 reports/final/batch_summary.csv reports/final/rejections.csv
```
Check by eye:
- headers exactly as in the README's output contract
- `rejections.csv` has only `workflow_id,batch_id,row_number,reason` (**no names or phones**)
- `lead_report.csv` phones look like `+91******1819`, booleans are `true`/`false`, `last_call_at` ends with `+05:30`
- `connectivity_rate` always has 4 decimals

### M8. PII is not stored in plain text ☐
Take a name and phone from the first data row of the input:
```bash
sed -n 2p data/drop_1/leads/motor_renewal/batch_001.csv
```
(seed 42: `Aarav Menon,+916463745499,…`). Then search the raw database files for them:
```bash
python - <<'EOF'
import glob
blob = b"".join(open(p, "rb").read() for p in glob.glob("ledger.db*") if not p.endswith(".key"))
for s in ["Aarav Menon", "6463745499"]:
    print(f"{s!r}: {'FOUND' if s.encode() in blob else 'not found'}")
EOF
```
Expected: both `not found`.

Look at what *is* stored (needs `sqlite3`):
```bash
sqlite3 -header -column ledger.db "SELECT lead_id, workflow_id, substr(phone_token,1,12) AS phone_token, phone_last4, external_ref, batch_id, substr(pii_enc,1,12) AS pii_enc FROM leads LIMIT 2;"
```
Expected shape: `phone_token` is hex (an HMAC), `pii_enc` starts with `gAAAAA` (Fernet ciphertext);
only `phone_last4`, the policy number and the batch are readable.

### M9. The ledger is append-only ☐
```bash
sqlite3 ledger.db "DELETE FROM events;"
sqlite3 ledger.db "UPDATE events SET disposition_code='renewed';"
sqlite3 ledger.db "SELECT COUNT(*) FROM events;"
```
Expected:
```
Error: stepping, events ledger is append-only (19)
Error: stepping, events ledger is append-only (19)
8500
```
(8500 = 8745 lines − 8 malformed − 237 duplicates.)

### M10. Wrong PII key is refused ☐
```bash
LEDGER_PII_KEY=eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXl5eXk= ledger report --out reports/x; echo "exit=$?"
```
Expected:
```
error: PII key does not match the key this database was created with; phone matching would silently break. Refusing to continue.
exit=2
```

### M11. A changed batch is refused (batches are immutable) ☐
```bash
cp -r data/drop_1 data/tampered
echo "Extra Person,9123456789,MOT/9,,," >> data/tampered/leads/motor_renewal/batch_001.csv
ledger ingest data/tampered; echo "exit=$?"
rm -rf data/tampered
```
Expected:
```
error: motor_renewal/batch_001 was already ingested with different content; refusing to re-ingest (batches are immutable once loaded)
exit=3
```

### M12. Hand-made dirty data: every messy case in one tiny drop ☐
Create the files:
```bash
mkdir -p data/mini/drop_1/leads/motor_renewal data/mini/drop_1/events
cat > data/mini/drop_1/leads/motor_renewal/batch_001.csv <<'EOF'
Insured Name,Mobile_No,Policy No,Expiry Date,Premium,Language,agent_notes
Asha Rao,+91 98765 43210,MOT/1,01-Dec-2026,"₹27,554",HI,VIP
,,,,,,
,9876500000,MOT/2,,,,
Ravi K,12345,MOT/3,,,,
Meena S,09876511111,,,,,
Asha R,919876543210,MOT/1,31/02/2026,Rs. 30000,Hindi,
Kiran P,7000000001,MOT/4,2026-11-05,N/A,,
EOF
cat > data/mini/drop_1/events/events_0001.jsonl <<'EOF'
{"event_id":"e1","event_type":"call.initiated","workflow_id":"motor_renewal","call_id":"c1","occurred_at":"2026-10-01T19:00:00Z","phone":"9876543210","payload":{}}
{"event_id":"e2","event_type":"call.connected","workflow_id":"motor_renewal","call_id":"c1","occurred_at":"2026-10-02 00:30:10","phone":"+91 98765 43210","payload":{}}
{"event_id":"e3","event_type":"disposition.set","workflow_id":"motor_renewal","call_id":"c1","occurred_at":"2026-10-02T00:35:00+05:30","phone":"09876543210","payload":{"disposition_code":"callback_requested"}}
{"event_id":"e4","event_type":"disposition.override","workflow_id":"motor_renewal","call_id":"c1","occurred_at":"2026-10-02T10:00:00+05:30","payload":{"disposition_code":"renewed","overridden_by":"sup_1","reason":"qa_review"}}
{"event_id":"e4","event_type":"disposition.override","workflow_id":"motor_renewal","call_id":"c1","occurred_at":"2026-10-02T10:00:00+05:30","payload":{"disposition_code":"renewed","overridden_by":"sup_1","reason":"qa_review"}}
{"event_id":"e5","event_type":"call.initiated","workflow_id":"motor_renewal","call_id":"c2","occurred_at":"2026-10-03T10:00:00+05:30","phone":"7000000001","payload":{}}
{"event_id":"e6","event_type":"call.telephony_failed","workflow_id":"motor_renewal","call_id":"c2","occurred_at":"2026-10-03T10:00:05+05:30","phone":"7000000001","payload":{"reason":"sip_503"}}
{"event_id":"e7","event_type":"disposition.set","workflow_id":"motor_renewal","call_id":"c2","occurred_at":"2026-10-03T10:01:00+05:30","phone":"7000000001","payload":{"disposition_code":"RENEWD"}}
{"event_id":"e8","event_type":"call.initiated","workflow_id":"motor_renewal","call_id":"c3","occurred_at":"2026-10-03T11:00:00+05:30","phone":"8111111111","payload":{}}
{"event_id":"e9","event_type":"call.initiat
{"event_type":"call.initiated","workflow_id":"motor_renewal"}

EOF
```
Run:
```bash
ledger --db mini.db ingest data/mini
ledger --db mini.db report --out reports/mini
cat reports/mini/*
```

What each input line is meant to trigger:

| Input | Expected handling |
|---|---|
| Headers `Insured Name`, `Mobile_No`, `Policy No`… + `agent_notes` | alias headers matched; `agent_notes` ignored |
| row 1 Asha Rao | valid; `₹27,554` → 27554.00, `HI` → hi, `01-Dec-2026` → 2026-12-01 |
| `,,,,,,` | blank: skipped, **not numbered** |
| row 2 (no name) | rejected `missing_required:customer_name` |
| row 3 phone `12345` | rejected `invalid_phone` |
| row 4 (no policy) | rejected `missing_required:policy_number` |
| row 5 Asha R `919876543210` | same phone as row 1 → **merged duplicate**; `31/02/2026` and `Hindi` invalid → 2 warnings |
| row 6 Kiran premium `N/A` | valid, 1 warning |
| e1 `19:00Z` | = 00:30 IST on **2026-10-02** (date crosses midnight) |
| e2, e3 | phone in other formats, same lead; naive timestamp = IST |
| e4 (twice) | override with no phone links via `call_id`; second copy = duplicate |
| e7 `RENEWD` | unknown disposition |
| e8 phone `8111111111` | not an uploaded lead → orphan |
| e9 truncated line, line without `event_id` | malformed |
| empty last line | not counted |

Expected output:
```
--- batch_summary.csv
workflow_id,batch_id,total_leads,yet_to_reach_out,in_progress_telephony_failed,in_progress_dialed,completed
motor_renewal,batch_001,2,0,1,0,1
--- daily_overview.csv
workflow_id,date,total_calls,connected_calls,connectivity_rate,success_leads
motor_renewal,2026-10-02,1,1,1.0000,1
motor_renewal,2026-10-03,1,0,0.0000,0
--- data_quality.json
  lead_rows_total 6, lead_rows_rejected 3 (invalid_phone 1, missing_required:customer_name 1,
  missing_required:policy_number 1), duplicate_lead_rows_merged 1, field_warnings 3,
  events_total 11, malformed_events 2, duplicate_events_dropped 1, orphan_events 1,
  unknown_disposition_events 1
--- lead_report.csv
motor_renewal,MOT/1,batch_001,+91******3210,completed,1,1,2026-10-02T00:30:00+05:30,renewed,true
motor_renewal,MOT/4,batch_001,+91******0001,in_progress_telephony_failed,1,0,2026-10-03T10:00:00+05:30,,false
--- rejections.csv
motor_renewal,batch_001,2,missing_required:customer_name
motor_renewal,batch_001,3,invalid_phone
motor_renewal,batch_001,4,missing_required:policy_number
```
Note on MOT/1: the AI set `callback_requested`, the supervisor override `renewed` wins →
`completed`, `ever_success=true`. MOT/4's only call failed at telephony → `in_progress_telephony_failed`.

### M13. A different seed (the reviewers use one we haven't seen) ☐
```bash
python ../assignment-a-data/tools/generate_data.py --seed 123 --scale small --out data/seed123
ledger --db s123_inc.db ingest data/seed123/drop_1
ledger --db s123_inc.db ingest data/seed123/drop_2
ledger --db s123_inc.db report --out reports/s123_inc
ledger --db s123_all.db ingest data/seed123
ledger --db s123_all.db report --out reports/s123_all
diff -r reports/s123_inc reports/s123_all && echo NO_DIFF
```
Expected: report line `leads rows=2048 rejected=98 events=9125 orphans=450`, then `NO_DIFF`.

### M14. Performance on the large dataset (stretch goal) ☐
```bash
python ../assignment-a-data/tools/generate_data.py --seed 42 --scale large --out data/large
time ledger --db large.db ingest data/large/drop_1
time ledger --db large.db ingest data/large/drop_2
time ledger --db large.db report --out reports/large
```
Expected: last report line `leads rows=143990 rejected=7097 events=632602 orphans=34424`.
Reference timings on the dev laptop: ~22 s + ~21 s + ~6.5 s ≈ **49 s total** (target < 2 min).

### Clean up afterwards
```bash
rm -rf ledger.db* all.db* mini.db* s123_*.db* large.db* reports/ data/
```

---

## Part C: Requirement → test traceability

| Requirement (assignment README) | Automated | Manual |
|---|---|---|
| Header matching via name/aliases, unknown columns ignored | A2 `read_rows_aliases…`, A1 `header_normalisation` | M12 |
| Blank rows skipped and not counted; row numbering | A2 `read_rows_aliases…` | M12 |
| Phone / date / decimal / enum rules | A1 | M12 |
| Rejection reasons, schema order, single reason | A2 `first_failure…` | M4, M12 |
| Optional invalid → null + warning | A2 `optional_invalid…` | M12 |
| Duplicate merge, permanent `batch_id`, last write wins | A2 `merge_last_write_wins…` | M12 |
| Defaults after merging | A2 `default_not_applied_before_merge` | — |
| Malformed / duplicate / orphan / unknown-disposition events | A3, A4 `report_invariants` | M4, M12 |
| Override links via `call_id` | A3 `override_links…` | M12 |
| Timestamps: UTC / offset / naive = IST; IST report dates | A1 timestamp tests | M5, M12 |
| Events before their lead count once it arrives | A3 `orphan_becomes_linked…` | M3 → M4 |
| Effective disposition (override > set, latest wins) | A3 | M12 |
| Bucket rules and precedence | A3 `bucket_precedence` | M12 |
| Real DB, append-only ledger | A4 `ledger_is_append_only` | M9 |
| PII not in plain text, still matched by phone | A2 `pii_not_stored…` | M8 |
| Idempotent re-ingest | A2 `reingest…`, A4 `incremental…` | M2, M11 |
| Incremental = all at once | A4 `incremental…` | M6, M13 |
| Five report files, exact format and sort order | A4 `report_invariants` | M4, M5, M7 |
| Works on an unseen seed | A4 (seed 7) | M13 |
| Performance stretch | — | M14 |
