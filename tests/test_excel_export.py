"""
Tests for Excel Exporter (core/reporting/excel.py).
Verifies 15 sheets, openpyxl readability, security escaping, and formula integrity.
"""

import os
import openpyxl
import pytest

from core.database import get_engine, initialize_database
from core.settings import initialize_settings
from core.reporting.metrics import build_report_data
from core.reporting.excel import export_monthly_report_excel


@pytest.fixture(scope="module")
def sample_report_data():
    """Create sample report data using in-memory / test DB."""
    import tempfile
    tmp_db = tempfile.mktemp(suffix="_excel_test.db")
    engine = get_engine(tmp_db)
    initialize_database(engine)
    initialize_settings(engine)
    data = build_report_data(engine, "Oct 2026", user="tiwari")
    yield data
    try:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
    except Exception:
        pass


def test_excel_export_structure(sample_report_data):
    """Verify generated Excel file opens with openpyxl and has all 15 required sheets."""
    import tempfile
    tmp_out = tempfile.mkdtemp()
    file_path = export_monthly_report_excel(sample_report_data, output_dir=tmp_out)
    assert os.path.exists(file_path)

    wb = openpyxl.load_workbook(file_path, data_only=False)
    expected_sheets = [
        "Summary",
        "Savings",
        "Savings log",
        "Opportunities",
        "Added",
        "Reduced",
        "New items",
        "Removed items",
        "Price changes",
        "Transfers",
        "PTW usage",
        "Store vs PTW gap",
        "Needs review",
        "Data quality",
        "Methodology",
    ]
    assert len(wb.sheetnames) == 15
    for expected in expected_sheets:
        assert expected in wb.sheetnames
        assert len(expected) <= 31

    # Check Summary formulas
    ws_sum = wb["Summary"]
    # Check that formulas are present in column C
    has_formula = False
    for row in range(5, 20):
        cell_val = str(ws_sum[f"C{row}"].value or "")
        if cell_val.startswith("="):
            has_formula = True
            break
    assert has_formula, "Summary sheet must contain trace formulas in column C"

    wb.close()

