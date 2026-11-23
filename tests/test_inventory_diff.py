"""
Unit tests for Milestone 4: Inventory diff engine and exact reconciliation identity.
"""

from datetime import date
from decimal import Decimal
import pandas as pd
import pytest
from sqlalchemy import text

from core.database import get_engine, initialize_database
from core.settings import initialize_settings
from core.pipeline import stage_inventory_upload, commit_and_publish
from core.inventory_diff import compute_inventory_diff


@pytest.fixture
def diff_db(tmp_path):
    db_path = tmp_path / "test_diff.db"
    engine = get_engine(str(db_path))
    initialize_database(engine)
    initialize_settings(engine)
    return engine


def test_reconciliation_identity_holds_exactly(diff_db):
    """
    Assert: Opening + Added - Reduced + Revaluation = Closing
    exactly within Rs 1 tolerance across a complex mix of item changes.
    """
    # Month 1 (Baseline)
    df_m1 = pd.DataFrame({
        "material_code": ["001", "002", "003", "004"],
        "spare_description": ["Bearing A", "Seal B", "Valve C", "Gasket D"],
        "quantity_in_stock": [10.0, 20.0, 5.0, 50.0],
        "unit_price": [100.0, 200.0, 500.0, 50.0],
    })
    uid1 = stage_inventory_upload(
        diff_db, "Sep 2026", date(2026, 9, 30), "inv_m1.csv", b"raw1", "hash_m1", df_m1, [], "admin"
    )
    commit_and_publish(diff_db, uid1, "admin")

    # Month 2 (New)
    # - 001: Qty increase (10 -> 15), price same
    # - 002: Qty decrease (20 -> 12), price increase (200 -> 220)
    # - 003: Removed (dropped)
    # - 004: Unchanged (50, 50.0)
    # - 005: New item (qty 8, price 300.0)
    df_m2 = pd.DataFrame({
        "material_code": ["001", "002", "004", "005"],
        "spare_description": ["Bearing A", "Seal B", "Gasket D", "Pump E"],
        "quantity_in_stock": [15.0, 12.0, 50.0, 8.0],
        "unit_price": [100.0, 220.0, 50.0, 300.0],
    })
    uid2 = stage_inventory_upload(
        diff_db, "Oct 2026", date(2026, 10, 31), "inv_m2.csv", b"raw2", "hash_m2", df_m2, [], "admin"
    )

    diff_res = compute_inventory_diff(diff_db, uid2, uid1, "2026-10-31")

    assert diff_res["is_reconciled"] is True
    assert diff_res["reconciliation_gap"] <= Decimal("0.05")

    # Verify identity explicitly:
    opening = diff_res["opening_val"]
    added = diff_res["added_val"]
    reduced = diff_res["reduced_val"]
    reval = diff_res["revaluation_val"]
    closing = diff_res["closing_val"]

    # Opening = 10*100 + 20*200 + 5*500 + 50*50 = 1000 + 4000 + 2500 + 2500 = 10000.00
    assert opening == Decimal("10000.00")

    # Closing = 15*100 + 12*220 + 50*50 + 8*300 = 1500 + 2640 + 2500 + 2400 = 9040.00
    assert closing == Decimal("9040.00")

    # Added = 001 (5*100=500) + 005 (8*300=2400) = 2900.00
    assert added == Decimal("2900.00")

    # Reduced = 002 (8*200=1600) + 003 (5*500=2500) = 4100.00
    assert reduced == Decimal("4100.00")

    # Reval = 002 (12 * (220 - 200) = 240.00)
    assert reval == Decimal("240.00")

    # Check formula: 10000 + 2900 - 4100 + 240 = 9040
    assert opening + added - reduced + reval == closing


def test_transfers_excluded_from_added_and_reduced(diff_db):
    """
    Assert: Equal move between locations of same material is classified
    as Transfer and excluded from Added/Reduced totals.
    """
    # M1: Bin A has 20 units
    df_m1 = pd.DataFrame({
        "material_code": ["001"],
        "storage_location": ["BIN-A"],
        "quantity_in_stock": [20.0],
        "unit_price": [50.0],
    })
    uid1 = stage_inventory_upload(
        diff_db, "Sep 2026", date(2026, 9, 30), "inv_m1.csv", b"raw1", "hash_m1_t", df_m1, [], "admin"
    )
    commit_and_publish(diff_db, uid1, "admin")

    # M2: 5 units moved from BIN-A to BIN-B (BIN-A: 15, BIN-B: 5)
    df_m2 = pd.DataFrame({
        "material_code": ["001", "001"],
        "storage_location": ["BIN-A", "BIN-B"],
        "quantity_in_stock": [15.0, 5.0],
        "unit_price": [50.0, 50.0],
    })
    uid2 = stage_inventory_upload(
        diff_db, "Oct 2026", date(2026, 10, 31), "inv_m2.csv", b"raw2", "hash_m2_t", df_m2, [], "admin"
    )

    diff_res = compute_inventory_diff(diff_db, uid2, uid1, "2026-10-31")

    assert diff_res["transfers_count"] == 1
    # Added and Reduced should be 0 since this is a pure transfer
    assert diff_res["added_val"] == Decimal("0.00")
    assert diff_res["reduced_val"] == Decimal("0.00")
    assert diff_res["closing_val"] == Decimal("1000.00")
    assert diff_res["opening_val"] == Decimal("1000.00")
    assert diff_res["is_reconciled"] is True


def test_price_carry_forward(diff_db):
    """
    Assert: Item with missing price in new snapshot carries forward
    price from baseline and flags 'Price carried'.
    """
    df_m1 = pd.DataFrame({
        "material_code": ["001"],
        "quantity_in_stock": [10.0],
        "unit_price": [125.0],
    })
    uid1 = stage_inventory_upload(
        diff_db, "Sep 2026", date(2026, 9, 30), "inv_m1.csv", b"raw1", "hash_m1_p", df_m1, [], "admin"
    )
    commit_and_publish(diff_db, uid1, "admin")

    # M2: unit_price missing (None)
    df_m2 = pd.DataFrame({
        "material_code": ["001"],
        "quantity_in_stock": [10.0],
        "unit_price": [None],
    })
    uid2 = stage_inventory_upload(
        diff_db, "Oct 2026", date(2026, 10, 31), "inv_m2.csv", b"raw2", "hash_m2_p", df_m2, [], "admin"
    )

    diff_res = compute_inventory_diff(diff_db, uid2, uid1, "2026-10-31")
    row = diff_res["diff_rows"][0]
    assert row["price_new"] == 125.0
    assert "Price carried" in row["flags"]


def test_above_rs_2l_flag(diff_db):
    """
    Assert: Addition above Rs 2,00,000 gets 'Above Rs 2L' flag.
    """
    df_m1 = pd.DataFrame({"material_code": ["001"], "quantity_in_stock": [1.0], "unit_price": [100.0]})
    uid1 = stage_inventory_upload(
        diff_db, "Sep 2026", date(2026, 9, 30), "inv_m1.csv", b"raw1", "hash_m1_2l", df_m1, [], "admin"
    )
    commit_and_publish(diff_db, uid1, "admin")

    # Addition worth Rs 2,50,000 (50 units * Rs 5,000)
    df_m2 = pd.DataFrame({
        "material_code": ["001", "002"],
        "quantity_in_stock": [1.0, 50.0],
        "unit_price": [100.0, 5000.0],
    })
    uid2 = stage_inventory_upload(
        diff_db, "Oct 2026", date(2026, 10, 31), "inv_m2.csv", b"raw2", "hash_m2_2l", df_m2, [], "admin"
    )

    diff_res = compute_inventory_diff(diff_db, uid2, uid1, "2026-10-31")
    item2_row = [r for r in diff_res["diff_rows"] if r["item_key"] == "002"][0]
    assert "Above Rs 2L" in item2_row["flags"]

