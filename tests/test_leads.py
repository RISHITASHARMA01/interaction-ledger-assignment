import json
from pathlib import Path

import pytest

from ledger.ingest_leads import (BatchConflict, effective_attributes, ingest_lead_file, read_rows,
                                 validate_row)


def motor(config):
    return config.schemas["motor_renewal"]


def row(**kw):
    base = {"customer_name": "A B", "phone": "9876543210", "policy_number": "P1",
            "policy_expiry": "", "premium_amount": "", "preferred_language": ""}
    base.update(kw)
    return base


# ------------------------------------------------------------------ rejection rules
def test_first_failure_in_schema_order_wins(config):
    # customer_name comes before phone in the schema
    r = validate_row(motor(config), row(customer_name="", phone="N/A"))
    assert r.reason == "missing_required:customer_name"
    r = validate_row(motor(config), row(phone="N/A", policy_number=""))
    assert r.reason == "invalid_phone"
    r = validate_row(motor(config), row(phone=""))
    assert r.reason == "missing_required:phone"
    r = validate_row(motor(config), row(policy_number=""))
    assert r.reason == "missing_required:policy_number"


def test_optional_invalid_is_null_plus_one_warning_each(config):
    r = validate_row(motor(config), row(policy_expiry="31/02/2026", premium_amount="N/A",
                                        preferred_language="Hindi"))
    assert r.reason is None and r.warnings == 3
    assert r.values["policy_expiry"] is None and r.values["preferred_language"] is None


def test_values_are_normalised(config):
    r = validate_row(motor(config), row(phone="(+91) 9876543210", policy_expiry="01-Dec-2026",
                                        premium_amount="₹27,554", preferred_language="HI"))
    assert r.values["phone"] == "+919876543210"
    assert r.values["policy_expiry"] == "2026-12-01"
    assert r.values["premium_amount"] == "27554.00"
    assert r.values["preferred_language"] == "hi"


# ------------------------------------------------------------------ CSV reading
def test_read_rows_aliases_blank_rows_extra_columns(config):
    text = ("Language,Insured Name,Policy No,Mobile_No,Expiry Date,Premium,agent_notes\r\n"
            "en,A,P1,9876543210,,,VIP\r\n"
            ",,,,,,\r\n"
            "  ,\t, ,,,,\r\n"
            "hi,B,P2,9876543211,,,\r\n")
    rows = list(read_rows(text, motor(config)))
    assert [n for n, _ in rows] == [1, 2]  # blank rows skipped and not counted
    assert rows[1][1]["customer_name"] == "B" and rows[1][1]["phone"] == "9876543211"
    assert "agent_notes" not in rows[0][1]


def test_read_rows_short_row_is_missing_values(config):
    text = "customer_name,phone,policy_number\nA,9876543210\n"
    (_, raw), = read_rows(text, motor(config))
    assert raw["policy_number"] == ""


# ------------------------------------------------------------------ ingestion + merge
def write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / "motor_renewal" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


HEADER = "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"


def test_merge_last_write_wins_and_batch_is_permanent(tmp_path, conn, crypto, config):
    s = motor(config)
    ingest_lead_file(conn, crypto, s, write(tmp_path, "batch_001.csv",
                     HEADER + "Asha,9876543210,P1,2026-12-01,100,\n"))
    ingest_lead_file(conn, crypto, s, write(tmp_path, "batch_002.csv",
                     HEADER + "Asha K,+91 98765 43210,P1,,200,soon\n"))
    lead, = conn.execute("SELECT * FROM leads").fetchall()
    assert lead["batch_id"] == "batch_001"
    attrs = json.loads(lead["attrs_json"])
    assert attrs["policy_expiry"] == "2026-12-01"   # null in later row: kept
    assert attrs["premium_amount"] == "200.00"      # overwritten
    assert attrs["preferred_language"] is None      # invalid -> null, default not stored
    assert effective_attributes(s, attrs)["preferred_language"] == "en"  # default on read
    assert crypto.decrypt_json(lead["pii_enc"])["customer_name"] == "Asha K"
    outcomes = [r[0] for r in conn.execute("SELECT outcome FROM lead_rows ORDER BY file_id")]
    assert outcomes == ["created", "merged"]
    assert conn.execute("SELECT SUM(field_warnings) FROM lead_rows").fetchone()[0] == 1


def test_default_not_applied_before_merge(tmp_path, conn, crypto, config):
    """A later row with an explicit value must win over a default."""
    s = motor(config)
    ingest_lead_file(conn, crypto, s, write(tmp_path, "batch_001.csv",
                     HEADER + "A,9876543210,P1,,,\nA,9876543210,P1,,,ta\n"))
    attrs = json.loads(conn.execute("SELECT attrs_json FROM leads").fetchone()[0])
    assert effective_attributes(s, attrs)["preferred_language"] == "ta"


def test_pii_not_stored_in_plaintext(tmp_path, conn, crypto, config):
    s = motor(config)
    ingest_lead_file(conn, crypto, s, write(tmp_path, "batch_001.csv",
                     HEADER + "Zebediah Quux,9876543210,P1,,,\nBad Row,12345,P2,,,\n"))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    blob = b"".join(p.read_bytes() for p in tmp_path.glob("t.db*"))
    for needle in (b"Zebediah", b"9876543210", b"Bad Row", b"12345"):
        assert needle not in blob


def test_reingest_same_file_is_noop_and_changed_file_conflicts(tmp_path, conn, crypto, config):
    s = motor(config)
    p = write(tmp_path, "batch_001.csv", HEADER + "A,9876543210,P1,,,\n")
    assert ingest_lead_file(conn, crypto, s, p) is True
    assert ingest_lead_file(conn, crypto, s, p) is False
    assert conn.execute("SELECT COUNT(*) FROM lead_rows").fetchone()[0] == 1
    p.write_text(HEADER + "A,9876543210,P1,,,\nB,9876543211,P2,,,\n")
    with pytest.raises(BatchConflict):
        ingest_lead_file(conn, crypto, s, p)
    assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
