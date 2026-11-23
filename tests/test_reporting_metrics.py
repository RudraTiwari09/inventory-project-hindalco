"""
Unit tests for core/reporting/metrics.py (Step 4).
Verifies build_report_data canonical model, fingerprinting, and edge cases.
"""

from datetime import date, datetime, timezone
import pytest
from sqlalchemy import text

from core.database import get_engine, initialize_database
from core.settings import initialize_settings, set_setting
from core.reporting.metrics import (
    build_report_data,
    compute_report_fingerprint,
    format_inr,
)


@pytest.fixture(scope="module")
def report_test_engine():
    """Isolated database for report metrics testing."""
    import tempfile
    import os
    tmp_db = tempfile.mktemp(suffix="_report_test.db")
    engine = get_engine(tmp_db)
    initialize_database(engine)
    initialize_settings(engine)
    yield engine
    try:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
    except Exception:
        pass


def test_format_inr():
    """Verify Indian currency formatting helper."""
    assert format_inr(15000000) == "Rs. 1.50 Cr"
    assert format_inr(250000) == "Rs. 2.50 L"
    assert format_inr(45000) == "Rs. 45.0 K"
    assert format_inr(500) == "Rs. 500"
    assert format_inr(None) == "—"


def test_fingerprint_generation():
    """Verify fingerprint detects any change in components."""
    fp1 = compute_report_fingerprint(1, 1, 2, 1, None, "2026-10-01", "2026-10-02", "set1")
    fp2 = compute_report_fingerprint(1, 1, 2, 1, None, "2026-10-01", "2026-10-02", "set1")
    assert fp1 == fp2
    assert len(fp1) == 16

    # Change in savings timestamp
    fp3 = compute_report_fingerprint(1, 1, 2, 1, None, "2026-10-01", "2026-10-03", "set1")
    assert fp1 != fp3

    # Change in version
    fp4 = compute_report_fingerprint(1, 2, 2, 1, None, "2026-10-01", "2026-10-02", "set1")
    assert fp1 != fp4


def test_build_report_data_baseline_and_empty(report_test_engine):
    """Verify first period / baseline behavior and empty savings handling."""
    # Insert a single active inventory upload for Oct 2026
    with report_test_engine.begin() as conn:
        res = conn.execute(
            text(
                """
                INSERT INTO uploads (
                    dataset, period, as_of_date, file_name, file_hash, uploaded_by,
                    uploaded_at, status, version, is_baseline
                ) VALUES (
                    'inventory', 'Oct 2026', '2026-10-31', 'inv_oct.csv', 'hash_oct_12345678',
                    'admin', '2026-10-31T12:00:00Z', 'active', 1, 1
                )
                """
            )
        )
        uid = res.lastrowid

        conn.execute(
            text(
                """
                INSERT INTO inventory_items (
                    upload_id, item_key, material_code, description, qty, unit_price, value
                ) VALUES (
                    :uid, 'BEARING-100', '100', 'Ball Bearing 100', 10.0, 500.0, 5000.0
                )
                """
            ),
            {"uid": uid},
        )

    data = build_report_data(report_test_engine, "Oct 2026", user="tiwari")
    assert data["cover"]["period"] == "Oct 2026"
    assert data["cover"]["is_baseline"] is True
    assert "Baseline period" in data["cover"]["comparison_label"]
    assert len(data["executive_summary"]["sentences"]) >= 1
    assert "methodology" in data
    assert len(data["methodology"]["limitations"]) == 4

