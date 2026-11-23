"""
Tests for PDF Exporter (core/reporting/pdf.py).
Verifies PDF generation, pypdf readability, page count bounds (6-9 pages),
and searchable headline metrics.
"""

import os
import tempfile
import pytest
from pypdf import PdfReader

from core.database import get_engine, initialize_database
from core.settings import initialize_settings
from core.reporting.metrics import build_report_data
from core.reporting.pdf import export_monthly_report_pdf


@pytest.fixture(scope="module")
def sample_report_data():
    """Create sample report data using test DB."""
    tmp_db = tempfile.mktemp(suffix="_pdf_test.db")
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


def test_pdf_export_page_count_and_content(sample_report_data):
    """Verify generated PDF opens with pypdf, has 6-9 pages, and includes key text."""
    tmp_out = tempfile.mkdtemp()
    pdf_path = export_monthly_report_pdf(sample_report_data, output_dir=tmp_out)
    
    assert os.path.exists(pdf_path)
    assert pdf_path.endswith(".pdf")

    reader = PdfReader(pdf_path)
    page_count = len(reader.pages)
    assert 6 <= page_count <= 9, f"Expected 6-9 pages, got {page_count}"

    # Extract text from all pages
    full_text = "\n".join([page.extract_text() or "" for page in reader.pages])

    # Verify key markers and headline text
    assert "Captive Power Plant" in full_text
    assert "Oct 2026" in full_text
    assert "CLOSING VALUATION" in full_text
    assert "STORE VS PTW GAP" in full_text
    assert "Cost Avoidance" in full_text
    assert "Offline report" in full_text

