"""
Unit tests for Milestone 2: Validation engine, Gating, and Data Formatting.
"""

import pytest
from datetime import date, timedelta
import pandas as pd
from core.database import get_engine, initialize_database
from core.validation import (
    clean_material_code,
    detect_wrong_slot,
    validate_and_normalize,
    suggest_column_mappings,
    INVENTORY_REQUIRED_FIELDS,
    PTW_REQUIRED_FIELDS,
)


@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_gating.db"
    engine = get_engine(str(db_path))
    initialize_database(engine)
    return engine


def test_clean_material_code_text_preservation():
    # Leading zeros preserved
    assert clean_material_code("0012345") == "0012345"
    assert clean_material_code("0000") == "0000"

    # Float trailing .0 stripped
    assert clean_material_code("10452.0") == "10452"
    assert clean_material_code("00452.0") == "00452"

    # String with spaces
    assert clean_material_code(" BRG-6204 ") == "BRG-6204"

    # Scientific notation rejected
    with pytest.raises(ValueError, match="scientific notation"):
        clean_material_code("1.25E+11")

    with pytest.raises(ValueError, match="scientific notation"):
        clean_material_code("3.4e-05")


def test_wrong_slot_detection():
    inv_cols = ["material_code", "description", "quantity_in_stock", "unit_cost", "bin_location"]
    ptw_cols = ["permit_id", "spare_description_text", "quantity_used", "permit_start", "remarks"]

    # PTW file uploaded to inventory slot
    assert detect_wrong_slot(ptw_cols, "inventory") is not None
    # Inventory file uploaded to PTW slot
    assert detect_wrong_slot(inv_cols, "ptw") is not None

    # Correct slots
    assert detect_wrong_slot(inv_cols, "inventory") is None
    assert detect_wrong_slot(ptw_cols, "ptw") is None


def test_validation_future_date(test_db):
    df = pd.DataFrame({
        "spare_code": ["SP-01"],
        "spare_description": ["Bearing"],
        "quantity_in_stock": [10],
    })
    mapping = {"material_code": "spare_code", "spare_description": "spare_description", "quantity_in_stock": "quantity_in_stock"}
    future_date = date.today() + timedelta(days=5)

    res = validate_and_normalize(df, "inventory", mapping, future_date, test_db, "dummy_hash")
    assert res["valid"] is False
    assert any("future" in err.lower() for err in res["errors"])


def test_validation_missing_required_fields(test_db):
    df = pd.DataFrame({"some_col": [1, 2, 3]})
    # Incomplete mapping
    mapping = {"spare_description": "some_col"}
    res = validate_and_normalize(df, "inventory", mapping, date.today(), test_db, "dummy_hash")
    assert res["valid"] is False
    assert any("required" in err.lower() for err in res["errors"])


def test_validation_total_rows_excluded(test_db):
    df = pd.DataFrame({
        "spare_code": ["00101", "00102", "TOTAL", "00103", "Grand Total"],
        "spare_description": ["Pump seal", "O-ring", "Total", "Gasket", "Grand Total"],
        "quantity_in_stock": [5, 10, 15, 20, 35],
    })
    mapping = {
        "material_code": "spare_code",
        "spare_description": "spare_description",
        "quantity_in_stock": "quantity_in_stock",
    }
    res = validate_and_normalize(df, "inventory", mapping, date.today(), test_db, "dummy_hash_totals")
    assert res["valid"] is True
    # 2 total rows rejected
    assert res["valid_rows_count"] == 3
    assert len(res["rejected_rows"]) == 2
    assert all("total" in r["reason"].lower() for r in res["rejected_rows"])


def test_validation_negative_quantity_rejected(test_db):
    df = pd.DataFrame({
        "spare_code": ["00101", "00102"],
        "spare_description": ["Pump seal", "Defective part"],
        "quantity_in_stock": [5, -2],
    })
    mapping = {
        "material_code": "spare_code",
        "spare_description": "spare_description",
        "quantity_in_stock": "quantity_in_stock",
    }
    res = validate_and_normalize(df, "inventory", mapping, date.today(), test_db, "dummy_hash_neg")
    assert res["valid"] is True
    assert res["valid_rows_count"] == 1
    assert any("negative" in w.lower() for w in res["warnings"])
    assert any("negative" in r["reason"].lower() for r in res["rejected_rows"])

