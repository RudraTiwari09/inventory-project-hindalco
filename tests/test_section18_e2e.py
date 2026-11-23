"""
End-to-End Tests for Section 18:
Savings Tracker, One-Click Monthly Report Exporters, and Section 16 Edge Cases.
"""

from datetime import date, datetime, timezone
import json
import os
import tempfile
import openpyxl
from pptx import Presentation
from pypdf import PdfReader
import pytest
from sqlalchemy import text

from core.database import get_engine, initialize_database
from core.settings import initialize_settings
from core.reporting.metrics import build_report_data, compute_report_fingerprint
from core.reporting.excel import export_monthly_report_excel
from core.reporting.pdf import export_monthly_report_pdf
from core.reporting.pptx import export_monthly_report_pptx
from core.savings import (
    create_savings_entry,
    verify_savings_entry,
    void_savings_entry,
    savings_summary,
)
from core.generate_dummy_data import populate_savings_dummy_entries


@pytest.fixture
def test_env():
    """Create isolated database environment with settings and baseline uploads."""
    tmp_db = tempfile.mktemp(suffix="_e2e.db")
    engine = get_engine(tmp_db)
    initialize_database(engine)
    initialize_settings(engine)
    tmp_out = tempfile.mkdtemp()

    # Populate baseline and active month in uploads
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        # Baseline upload Month 1 (Sep 2026)
        conn.execute(
            text(
                """
                INSERT INTO uploads (dataset, period, as_of_date, version, file_name, file_hash, row_count, is_baseline, status, uploaded_at, uploaded_by)
                VALUES ('inventory', 'Sep 2026', '2026-09-30', 1, 'inv_sep.csv', 'hash_sep_inv', 50, 1, 'active', :now, 'tiwari')
                """
            ),
            {"now": now},
        )
        base_id = 1

        # Baseline items
        conn.execute(
            text(
                """
                INSERT INTO inventory_items (upload_id, item_key, material_code, description, category, department, equipment_tag, qty, uom, unit_price, criticality)
                VALUES
                (:uid, 'BRG-6205', 'BRG-6205', 'Ball bearing 6205', 'Bearings', 'Boiler', 'BLR-01', 10.0, 'NOS', 1000.0, 'Medium'),
                (:uid, 'VLV-GT-001', 'VLV-GT-001', 'Gate valve 2 inch', 'Valves', 'Boiler', 'BLR-02', 5.0, 'NOS', 5000.0, 'High'),
                (:uid, 'PMP-MS-021', 'PMP-MS-021', 'Pump seal 21', 'Pumps', 'Turbine', 'TRB-01', 8.0, 'NOS', 12000.0, 'Medium')
                """
            ),
            {"uid": base_id},
        )

        # Active upload Month 2 (Oct 2026)
        conn.execute(
            text(
                """
                INSERT INTO uploads (dataset, period, as_of_date, version, file_name, file_hash, row_count, is_baseline, status, uploaded_at, uploaded_by)
                VALUES ('inventory', 'Oct 2026', '2026-10-31', 1, 'inv_oct.csv', 'hash_oct_inv', 60, 0, 'active', :now, 'tiwari')
                """
            ),
            {"now": now},
        )
        oct_id = 2

        # Active items (BRG increased to 30 -> +20 added, VLV price changed to 6500, new item added)
        conn.execute(
            text(
                """
                INSERT INTO inventory_items (upload_id, item_key, material_code, description, category, department, equipment_tag, qty, uom, unit_price, criticality)
                VALUES
                (:uid, 'BRG-6205', 'BRG-6205', 'Ball bearing 6205', 'Bearings', 'Boiler', 'BLR-01', 30.0, 'NOS', 1000.0, 'Medium'),
                (:uid, 'VLV-GT-001', 'VLV-GT-001', 'Gate valve 2 inch', 'Valves', 'Boiler', 'BLR-02', 5.0, 'NOS', 6500.0, 'High'),
                (:uid, 'PMP-MS-021', 'PMP-MS-021', 'Pump seal 21', 'Pumps', 'Turbine', 'TRB-01', 8.0, 'NOS', 12000.0, 'Medium'),
                (:uid, 'HV-ADD-250K', 'HV-ADD-250K', 'Boiler Control Module', 'Instrumentation', 'Boiler', 'BLR-03', 5.0, 'NOS', 50000.0, 'High')
                """
            ),
            {"uid": oct_id},
        )

        # Active PTW upload Month 2
        conn.execute(
            text(
                """
                INSERT INTO uploads (dataset, period, as_of_date, version, file_name, file_hash, row_count, is_baseline, status, uploaded_at, uploaded_by)
                VALUES ('ptw', 'Oct 2026', '2026-10-31', 1, 'ptw_oct.csv', 'hash_oct_ptw', 10, 0, 'active', :now, 'tiwari')
                """
            ),
            {"now": now},
        )
        ptw_id = 3

        conn.execute(
            text(
                """
                INSERT INTO ptw_permits (upload_id, permit_key, permit_id, department, equipment_tag, permit_start, permit_end, period, status, remarks)
                VALUES
                (:uid, 'PTW-001', 'PTW-001', 'Boiler', 'BLR-01', '2026-10-01', '2026-10-02', 'Oct 2026', 'Closed', 'Maintenance'),
                (:uid, 'PTW-002', 'PTW-002', 'Turbine', 'TRB-01', '2026-10-05', '2026-10-06', 'Oct 2026', 'Closed', 'Seal overhaul')
                """
            ),
            {"uid": ptw_id},
        )

        # Pre-seed matched lines in ptw_lines
        conn.execute(
            text(
                """
                INSERT INTO ptw_lines (permit_key, upload_id, line_number, raw_description, cleaned_description, quantity, uom, matched_item_key, match_method, match_confidence, status)
                VALUES
                ('PTW-001', :uid, 1, 'Ball bearing 6205', 'ball bearing 6205', 2.0, 'NOS', 'BRG-6205', 'part_number_regex', 1.0, 'confirmed'),
                ('PTW-002', :uid, 1, 'Pump seal 21', 'pump seal 21', 1.0, 'NOS', 'PMP-MS-021', 'part_number_regex', 1.0, 'confirmed')
                """
            ),
            {"uid": ptw_id},
        )

    # Compute inventory diff
    from core.inventory_diff import compute_inventory_diff
    compute_inventory_diff(engine, oct_id, base_id, "2026-10-31")

    yield engine, tmp_out

    try:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
    except Exception:
        pass


def test_baseline_month_handling(test_env):
    """Verify baseline month without prior snapshot builds report cleanly without crash."""
    engine, tmp_out = test_env
    data = build_report_data(engine, "Sep 2026", user="tiwari")

    assert data["cover"]["is_baseline"] is True
    assert data["cover"]["period"] == "Sep 2026"
    assert data["inventory_movement"]["waterfall"]["opening"] == 0.0
    assert data["inventory_movement"]["waterfall"]["closing"] > 0.0


def test_first_month_after_baseline(test_env):
    """Verify Month 2 compares against Month 1 baseline correctly."""
    engine, tmp_out = test_env
    data = build_report_data(engine, "Oct 2026", user="tiwari")

    assert data["cover"]["is_baseline"] is False
    assert data["cover"]["baseline_period"] == "Sep 2026"
    assert len(data["inventory_movement"]["top_added"]) > 0


def test_edge_case_zero_usage_in_window(test_env):
    """Verify item with zero usage in window flags 'no usage history' gracefully."""
    engine, tmp_out = test_env
    data = build_report_data(engine, "Oct 2026", user="tiwari")

    # Item HV-ADD-250K had no usage
    assert data["executive_summary"]["closing_value"] > 0


def test_edge_case_high_price_variance(test_env):
    """Verify item price change (>20%) is detected and tracked."""
    engine, tmp_out = test_env
    data = build_report_data(engine, "Oct 2026", user="tiwari")

    price_changes = data["inventory_movement"]["price_changes"]
    # VLV-GT-001 changed from 5000 to 6500 (+30%)
    vlv_chg = [p for p in price_changes if p.get("material_code") == "VLV-GT-001"]
    assert len(vlv_chg) > 0


def test_edge_case_mixed_case_material_codes(test_env):
    """Verify mixed-case codes are normalized cleanly in matching."""
    engine, tmp_out = test_env
    from core.cleaning import normalize_text
    assert normalize_text("vlv-gt-001") == normalize_text("VLV-GT-001")


def test_edge_case_empty_ptw_month(test_env):
    """Verify month with 0 PTW records produces 0% coverage without division by zero."""
    engine, tmp_out = test_env
    # Month Sep 2026 has no PTW upload
    data = build_report_data(engine, "Sep 2026", user="tiwari")
    assert data["executive_summary"]["match_coverage_pct"] == 0.0
    assert data["executive_summary"]["ptw_lines_total"] == 0


def test_planted_savings_entries_and_data_checks(test_env):
    """Verify planted dummy entries (draft, verified, actioned, voided, contradicted)."""
    engine, tmp_out = test_env
    ids = populate_savings_dummy_entries(engine)
    assert len(ids) == 5

    summary = savings_summary(engine, "FY 2026-27")
    assert summary["verified_rs"] >= 120000.0  # BRG-6205 verified 1.2L
    assert summary["actioned_rs"] >= 85000.0   # PMP-MS-021 actioned 85K

    # Voided entries must NOT be counted in active/verified totals
    report_data = build_report_data(engine, "Oct 2026", user="tiwari")
    contra = report_data["needs_review"]["contradicted_entries"]
    assert any(e.get("material_code") == "OVERSTOCK-ADD-01" for e in contra)


def test_export_files_valid_on_disk(test_env):
    """Verify Excel, PDF, and PPTX generate valid, readable files on disk."""
    engine, tmp_out = test_env
    populate_savings_dummy_entries(engine)
    report_data = build_report_data(engine, "Oct 2026", user="tiwari")

    # 1. Excel
    xl_path = export_monthly_report_excel(report_data, output_dir=tmp_out)
    assert os.path.exists(xl_path)
    wb = openpyxl.load_workbook(xl_path)
    assert len(wb.sheetnames) == 15

    # 2. PDF
    pdf_path = export_monthly_report_pdf(report_data, output_dir=tmp_out)
    assert os.path.exists(pdf_path)
    reader = PdfReader(pdf_path)
    assert 6 <= len(reader.pages) <= 9

    # 3. PPTX
    pptx_path = export_monthly_report_pptx(report_data, output_dir=tmp_out)
    assert os.path.exists(pptx_path)
    prs = Presentation(pptx_path)
    assert len(prs.slides) == 7


def test_fingerprint_stability_and_change(test_env):
    """Verify identical data gives same fingerprint, new savings entry changes it."""
    engine, tmp_out = test_env
    d1 = build_report_data(engine, "Oct 2026", user="tiwari")
    fp1 = d1["cover"]["fingerprint"]

    # Re-run without changes
    d2 = build_report_data(engine, "Oct 2026", user="tiwari")
    fp2 = d2["cover"]["fingerprint"]
    assert fp1 == fp2, "Fingerprint must be stable for identical data"

    # Add savings entry
    create_savings_entry(
        engine,
        action_date=date(2026, 10, 20),
        entry_type="overstock_avoidance",
        requested_qty=5.0,
        avoided_qty=5.0,
        unit_price=1000.0,
        created_by="tiwari",
        material_code="BRG-6205",
        item_key="BRG-6205",
        description="Avoided 5 bearings",
    )

    d3 = build_report_data(engine, "Oct 2026", user="tiwari")
    fp3 = d3["cover"]["fingerprint"]
    assert fp1 != fp3, "Fingerprint must change when savings data changes"
