"""
Unit tests for Milestone 5: PTW merge, history tracking, 3-tier matching, and store-vs-PTW gap.
"""

from datetime import date
import pandas as pd
import pytest
from sqlalchemy import text

from core.database import get_engine, initialize_database
from core.settings import initialize_settings
from core.pipeline import (
    stage_inventory_upload,
    stage_ptw_upload,
    commit_and_publish,
)
from core.ptw_merge import (
    normalize_permit_key,
    parse_quantity_and_uom,
    classify_permit_status,
    detect_file_type,
    merge_and_upsert_ptw,
)
from core.matching import (
    match_ptw_lines_to_inventory,
    compute_store_vs_ptw_gap,
    confirm_match,
)
from core.inventory_diff import compute_inventory_diff


@pytest.fixture
def match_db(tmp_path):
    db_path = tmp_path / "test_matching.db"
    engine = get_engine(str(db_path))
    initialize_database(engine)
    initialize_settings(engine)
    return engine


def test_ptw_key_and_quantity_parsing():
    assert normalize_permit_key("  ptw-2026-001 \n") == "PTW-2026-001"
    assert normalize_permit_key("ptw   002") == "PTW 002"

    qty, uom = parse_quantity_and_uom("2 nos")
    assert qty == 2.0
    assert uom == "nos"

    qty2, uom2 = parse_quantity_and_uom("15.5 MTR")
    assert qty2 == 15.5
    assert uom2 == "mtr"


def test_permit_status_classification():
    assert classify_permit_status("Closed") == "Closed"
    assert classify_permit_status("completed") == "Closed"
    assert classify_permit_status("Open") == "Pending"
    assert classify_permit_status("In progress") == "Pending"
    assert classify_permit_status("Cancelled") == "Cancelled"
    assert classify_permit_status("Rejected") == "Cancelled"


def test_ptw_upsert_and_history_tracking(match_db):
    # Upload 1: Permit PTW-100 is Open
    df1 = pd.DataFrame({
        "permit_id": ["PTW-100"],
        "spare_description_text": ["Ball bearing"],
        "quantity_used": [2.0],
        "status": ["Open"],
        "permit_start": ["2026-10-05"],
        "permit_end": ["2026-10-06"],
    })
    uid1 = stage_ptw_upload(match_db, "Oct 2026", date(2026, 10, 31), "ptw_v1.csv", b"raw1", "h_p1", df1, [], "admin")
    commit_and_publish(match_db, uid1, "admin")

    with match_db.connect() as conn:
        p_count = conn.execute(text("SELECT COUNT(*) FROM ptw_permits WHERE permit_key = 'PTW-100'")).fetchone()[0]
        assert p_count == 1

    # Upload 2: Permit PTW-100 is now Closed (status updated), and PTW-101 is new
    df2 = pd.DataFrame({
        "permit_id": ["PTW-100", "PTW-101"],
        "spare_description_text": ["Ball bearing", "Gasket"],
        "quantity_used": [2.0, 1.0],
        "status": ["Closed", "Closed"],
        "permit_start": ["2026-10-05", "2026-10-15"],
        "permit_end": ["2026-10-06", "2026-10-16"],
    })
    uid2 = stage_ptw_upload(match_db, "Oct 2026", date(2026, 10, 31), "ptw_v2.csv", b"raw2", "h_p2", df2, [], "admin")
    commit_and_publish(match_db, uid2, "admin", replace_existing=True)

    with match_db.connect() as conn:
        total_p = conn.execute(text("SELECT COUNT(*) FROM ptw_permits")).fetchone()[0]
        assert total_p == 2

        # Check history table for PTW-100
        h_row = conn.execute(
            text("SELECT field_name, old_value, new_value FROM ptw_permit_history WHERE permit_key = 'PTW-100'")
        ).fetchone()
        assert h_row is not None
        assert h_row[0] == "status"
        assert h_row[1] == "Pending"
        assert h_row[2] == "Closed"


def test_three_tier_matching_engine(match_db):
    # Setup inventory items
    df_inv = pd.DataFrame({
        "material_code": ["BRG-6204", "SEAL-100", "VLV-200"],
        "spare_description": ["Deep groove ball bearing 6204", "Mechanical seal type B", "Gate valve 2 inch"],
        "quantity_in_stock": [10.0, 10.0, 10.0],
        "unit_price": [500.0, 1200.0, 3000.0],
        "equipment_tag": ["PUMP-01", "PUMP-01", "BOILER-01"],
    })
    inv_uid = stage_inventory_upload(match_db, "Oct 2026", date(2026, 10, 31), "inv.csv", b"i", "h_i", df_inv, [], "admin")
    commit_and_publish(match_db, inv_uid, "admin")

    # Tier 2 prep: Confirm user match for "custom gasket set" -> "SEAL-100"
    confirm_match(match_db, "custom gasket set", "SEAL-100", "admin")

    # PTW lines:
    # 1. Has regex code "Need BRG-6204 for motor replacement" -> Tier 1 (code_regex, 100%)
    # 2. "custom gasket set" -> Tier 2 (confirmed, 100%)
    # 3. "Gate valve 2 inch" with tag "BOILER-01" -> Tier 3 (fuzzy_bonus, >= 90%)
    # 4. "Random completely unknown component" -> Tier 3 (< 70%, Unmatched)
    df_ptw = pd.DataFrame({
        "permit_id": ["P1", "P2", "P3", "P4"],
        "spare_description_text": [
            "Need BRG-6204 for motor replacement",
            "custom gasket set",
            "Gate valve 2 inch",
            "Random completely unknown component",
        ],
        "quantity_used": [2.0, 1.0, 1.0, 5.0],
        "status": ["Closed", "Closed", "Closed", "Closed"],
        "equipment_tag": ["PUMP-01", "PUMP-01", "BOILER-01", "TURBINE-01"],
        "permit_start": ["2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"],
    })
    ptw_uid = stage_ptw_upload(match_db, "Oct 2026", date(2026, 10, 31), "ptw.csv", b"p", "h_p", df_ptw, [], "admin")
    commit_and_publish(match_db, ptw_uid, "admin")

    match_stats = match_ptw_lines_to_inventory(match_db, ptw_uid, inv_uid)
    assert match_stats["auto"] >= 3  # Regex, Confirmed, and High fuzzy match
    assert match_stats["unmatched"] == 1


def test_gap_analysis_within_window(match_db):
    # Month 1: 20 units of BRG-6204
    df_m1 = pd.DataFrame({"material_code": ["BRG-6204"], "quantity_in_stock": [20.0], "unit_price": [100.0]})
    uid1 = stage_inventory_upload(match_db, "Sep 2026", date(2026, 9, 30), "m1.csv", b"m1", "h_m1", df_m1, [], "admin")
    commit_and_publish(match_db, uid1, "admin")

    # Month 2: 15 units of BRG-6204 (store reduced by 5 units)
    df_m2 = pd.DataFrame({"material_code": ["BRG-6204"], "quantity_in_stock": [15.0], "unit_price": [100.0]})
    uid2 = stage_inventory_upload(match_db, "Oct 2026", date(2026, 10, 31), "m2.csv", b"m2", "h_m2", df_m2, [], "admin")
    commit_and_publish(match_db, uid2, "admin")
    compute_inventory_diff(match_db, uid2, uid1, "2026-10-31")

    # PTW recorded 5 units used in Oct (exact match)
    df_ptw = pd.DataFrame({
        "permit_id": ["PTW-OCT-1"],
        "spare_description_text": ["BRG-6204 bearing"],
        "quantity_used": [5.0],
        "status": ["Closed"],
        "permit_start": ["2026-10-15"],
    })
    ptw_uid = stage_ptw_upload(match_db, "Oct 2026", date(2026, 10, 31), "ptw.csv", b"p", "h_p_gap", df_ptw, [], "admin")
    commit_and_publish(match_db, ptw_uid, "admin")
    match_ptw_lines_to_inventory(match_db, ptw_uid, uid2)

    # Calculate gap in window (2026-09-30, 2026-10-31]
    gap_res = compute_store_vs_ptw_gap(match_db, uid2, ptw_uid, "2026-09-30", "2026-10-31")

    assert len(gap_res["gap_table"]) == 1
    row = gap_res["gap_table"][0]
    assert row["store_reduced_qty"] == 5.0
    assert row["ptw_qty"] == 5.0
    assert row["difference_qty"] == 0.0
    assert row["status"] == "Within tolerance"
