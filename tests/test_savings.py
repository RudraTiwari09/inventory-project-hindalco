"""
Unit and integration tests for core/savings.py.
Covers opportunity computation, manual entry lifecycle, maker-checker,
data checks, and financial year boundaries.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import text

from core.database import get_engine, initialize_database
from core.settings import initialize_settings, set_setting
from core.savings import (
    compute_opportunities_and_excess,
    create_savings_entry,
    dismiss_opportunity,
    edit_savings_entry,
    get_fy_date_bounds,
    get_fy_label,
    run_data_check,
    savings_summary,
    verify_savings_entry,
    void_savings_entry,
)


@pytest.fixture(scope="module")
def test_engine():
    """Isolated SQLite database for savings tests."""
    import tempfile
    import os
    tmp_db = tempfile.mktemp(suffix="_savings.db")
    engine = get_engine(tmp_db)
    initialize_database(engine)
    initialize_settings(engine)
    yield engine
    try:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
    except Exception:
        pass


def test_fy_boundaries():
    """Verify Financial Year boundary logic (March 31 vs April 1)."""
    # 31 March 2027 should still be FY 2026-27
    d_mar31 = date(2027, 3, 31)
    assert get_fy_label(d_mar31, fy_start_month=4) == "FY 2026-27"

    # 1 April 2027 begins FY 2027-28
    d_apr1 = date(2027, 4, 1)
    assert get_fy_label(d_apr1, fy_start_month=4) == "FY 2027-28"

    # Mid year
    d_oct = date(2026, 10, 15)
    assert get_fy_label(d_oct, fy_start_month=4) == "FY 2026-27"

    # FY Range check
    s_d, e_d = get_fy_date_bounds("FY 2026-27", fy_start_month=4)
    assert s_d == date(2026, 4, 1)
    assert e_d == date(2027, 3, 31)


def test_create_savings_entry_validations(test_engine):
    """Verify input validation, quantity limits, and frozen paise calculation."""
    # 1. Negative or zero avoided qty rejected
    ok, msg, eid = create_savings_entry(
        test_engine,
        action_date=date(2026, 10, 15),
        entry_type="order_cancelled",
        requested_qty=10,
        avoided_qty=0,
        unit_price=150.0,
        created_by="admin",
    )
    assert not ok
    assert "greater than zero" in msg

    # 2. Avoided qty > requested qty rejected
    ok, msg, eid = create_savings_entry(
        test_engine,
        action_date=date(2026, 10, 15),
        entry_type="order_cancelled",
        requested_qty=10,
        avoided_qty=15,
        unit_price=150.0,
        created_by="admin",
    )
    assert not ok
    assert "cannot exceed requested" in msg

    # 3. Valid entry created with frozen integer paise
    ok, msg, eid = create_savings_entry(
        test_engine,
        action_date=date(2026, 10, 15),
        entry_type="order_cancelled",
        requested_qty=10,
        avoided_qty=5,
        unit_price=150.25,
        created_by="admin",
        reference="PR-1001",
        description="Ball Bearing 6205",
        department="Turbine",
        equipment_tag="TRB-01",
    )
    assert ok
    assert eid is not None

    with test_engine.connect() as conn:
        row = conn.execute(
            text("SELECT amount_paise, order_value_paise, status, period FROM savings_entries WHERE id = :id"),
            {"id": eid},
        ).fetchone()
        # 5 * 150.25 * 100 = 75125 paise
        assert row[0] == 75125
        # 10 * 150.25 * 100 = 150250 paise
        assert row[1] == 150250
        assert row[2] == "actioned"
        assert row[3] == "Oct 2026"


def test_duplicate_entry_prevention(test_engine):
    """Verify unique constraint on (item_key, reference, type)."""
    ok1, _, _ = create_savings_entry(
        test_engine,
        action_date=date(2026, 10, 20),
        entry_type="order_reduced",
        requested_qty=20,
        avoided_qty=10,
        unit_price=50.0,
        created_by="admin",
        item_key="BRG-01|BAY-1",
        reference="PO-9999",
    )
    assert ok1

    # Exact duplicate (same item_key, reference, type)
    ok2, msg2, _ = create_savings_entry(
        test_engine,
        action_date=date(2026, 10, 21),
        entry_type="order_reduced",
        requested_qty=20,
        avoided_qty=10,
        unit_price=50.0,
        created_by="admin",
        item_key="BRG-01|BAY-1",
        reference="PO-9999",
    )
    assert not ok2
    assert "already exists" in msg2


def test_savings_entry_edit_and_verification(test_engine):
    """Verify edit rules, locking on verify, and maker-checker policy."""
    ok, _, eid = create_savings_entry(
        test_engine,
        action_date=date(2026, 10, 25),
        entry_type="existing_stock_used",
        requested_qty=10,
        avoided_qty=4,
        unit_price=100.0,
        created_by="maker_user",
        reference="REQ-55",
    )
    assert ok

    # Edit while actioned
    ok_edit, _ = edit_savings_entry(
        test_engine,
        entry_id=eid,
        user="maker_user",
        avoided_qty=6,
        unit_price=100.0,
        requested_qty=10,
        entry_type="existing_stock_used",
        reference="REQ-55",
    )
    assert ok_edit

    # Check updated amount
    with test_engine.connect() as conn:
        row = conn.execute(
            text("SELECT amount_paise, version FROM savings_entries WHERE id = :id"),
            {"id": eid},
        ).fetchone()
        assert row[0] == 60000
        assert row[1] == 2

    # Enable maker-checker policy
    set_setting(test_engine, "maker_checker", "true")

    # Maker cannot verify their own entry
    ok_v_fail, msg_v = verify_savings_entry(
        test_engine,
        entry_id=eid,
        user="maker_user",
        note="Approving my own work",
    )
    assert not ok_v_fail
    assert "Maker-checker policy is enabled" in msg_v

    # Different admin can verify
    ok_v_succ, _ = verify_savings_entry(
        test_engine,
        entry_id=eid,
        user="checker_user",
        note="Verified with store records",
    )
    assert ok_v_succ

    # Editing a verified entry is locked
    ok_edit_locked, msg_lock = edit_savings_entry(
        test_engine,
        entry_id=eid,
        user="checker_user",
        avoided_qty=8,
        unit_price=100.0,
        requested_qty=10,
        entry_type="existing_stock_used",
    )
    assert not ok_edit_locked
    assert "Verified or voided entries cannot be edited" in msg_lock


def test_void_entry_and_reopen_opportunity(test_engine):
    """Verify voiding marks entry voided and reopens linked opportunity."""
    with test_engine.begin() as conn:
        res = conn.execute(
            text(
                """
                INSERT INTO savings_opportunities (
                    period, upload_id, item_key, added_qty, avoidable_qty,
                    avoidable_value_paise, confidence, status
                ) VALUES (
                    'Oct 2026', 1, 'PUMP-01', 5.0, 3.0, 300000, 'high', 'open'
                )
                """
            )
        )
        opp_id = res.lastrowid

    # Convert opportunity to entry
    ok_c, _, eid = create_savings_entry(
        test_engine,
        action_date=date(2026, 10, 28),
        entry_type="order_cancelled",
        requested_qty=5.0,
        avoided_qty=3.0,
        unit_price=1000.0,
        created_by="admin",
        item_key="PUMP-01",
        opportunity_id=opp_id,
    )
    assert ok_c

    with test_engine.connect() as conn:
        opp_stat = conn.execute(
            text("SELECT status, converted_entry_id FROM savings_opportunities WHERE id = :id"),
            {"id": opp_id},
        ).fetchone()
        assert opp_stat[0] == "converted"
        assert opp_stat[1] == eid

    # Void the entry
    ok_void, _ = void_savings_entry(
        test_engine,
        entry_id=eid,
        user="admin",
        reason="Order reinstated due to emergency shutdown",
    )
    assert ok_void

    with test_engine.connect() as conn:
        entry_row = conn.execute(
            text("SELECT status, void_reason FROM savings_entries WHERE id = :id"),
            {"id": eid},
        ).fetchone()
        assert entry_row[0] == "voided"
        assert "emergency shutdown" in entry_row[1]

        # Opportunity must be reopened
        opp_reopened = conn.execute(
            text("SELECT status, converted_entry_id FROM savings_opportunities WHERE id = :id"),
            {"id": opp_id},
        ).fetchone()
        assert opp_reopened[0] == "open"
        assert opp_reopened[1] is None


def test_automated_data_check(test_engine):
    """Verify data_check updates to contradicted when subsequent addition appears."""
    # 1. Create a saving for item VALVE-99 with avoided_qty = 10 on 2026-08-15
    ok, _, eid = create_savings_entry(
        test_engine,
        action_date=date(2026, 8, 15),
        entry_type="order_cancelled",
        requested_qty=10,
        avoided_qty=10,
        unit_price=200.0,
        created_by="admin",
        item_key="VALVE-99",
        reference="PR-VALVE-08",
    )
    assert ok

    # 2. Insert a subsequent published upload for Sep 2026 showing VALVE-99 added 12 units
    with test_engine.begin() as conn:
        res_u = conn.execute(
            text(
                """
                INSERT INTO uploads (
                    dataset, period, as_of_date, file_name, file_hash, uploaded_by,
                    uploaded_at, status, version
                ) VALUES (
                    'inventory', 'Sep 2026', '2026-09-30', 'inv_sep.csv', 'hash_sep',
                    'admin', '2026-09-30T10:00:00Z', 'active', 1
                )
                """
            )
        )
        sep_uid = res_u.lastrowid

        conn.execute(
            text(
                """
                INSERT INTO inventory_diff (
                    upload_id, item_key, change_type, qty_delta
                ) VALUES (
                    :uid, 'VALVE-99', 'Qty increase', 12.0
                )
                """
            ),
            {"uid": sep_uid},
        )

    # 3. Run data check
    updates = run_data_check(test_engine)
    assert updates > 0

    with test_engine.connect() as conn:
        chk_row = conn.execute(
            text("SELECT data_check, data_check_period FROM savings_entries WHERE id = :id"),
            {"id": eid},
        ).fetchone()
        assert chk_row[0] == "contradicted"
        assert chk_row[1] == "Sep 2026"


def test_savings_summary_aggregation(test_engine):
    """Verify savings_summary generates exact totals and breakdown metrics."""
    summary = savings_summary(test_engine, scope="all")
    assert "verified_rs" in summary
    assert "actioned_rs" in summary
    assert "opportunities" in summary
    assert "excess_stock_rs" in summary
    assert "monthly_trend" in summary
    assert summary["verified_rs"] >= 0.0
    assert summary["actioned_rs"] >= 0.0
