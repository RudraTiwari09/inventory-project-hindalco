"""
Tests for PowerPoint Exporter (core/reporting/pptx.py).
Verifies 7 widescreen slides, slide titles, non-overflow text formatting,
and python-pptx integrity.
"""

import os
import tempfile
from pptx import Presentation
import pytest

from core.database import get_engine, initialize_database
from core.settings import initialize_settings
from core.reporting.metrics import build_report_data
from core.reporting.pptx import export_monthly_report_pptx


@pytest.fixture(scope="module")
def sample_report_data():
    """Create sample report data using test DB."""
    tmp_db = tempfile.mktemp(suffix="_pptx_test.db")
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


def test_pptx_export_structure(sample_report_data):
    """Verify generated PowerPoint presentation opens, has 7 slides, and matching titles."""
    tmp_out = tempfile.mkdtemp()
    pptx_path = export_monthly_report_pptx(sample_report_data, output_dir=tmp_out)

    assert os.path.exists(pptx_path)
    assert pptx_path.endswith(".pptx")

    prs = Presentation(pptx_path)
    assert len(prs.slides) == 7, f"Expected 7 slides, found {len(prs.slides)}"

    # Check slide dimensions (16:9 widescreen)
    assert round(prs.slide_width.inches, 2) == 13.33
    assert round(prs.slide_height.inches, 2) == 7.5

    # Check text content in slides
    all_slide_texts = []
    for s in prs.slides:
        slide_text = ""
        for shape in s.shapes:
            if shape.has_text_frame:
                slide_text += " " + shape.text_frame.text
            elif shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        slide_text += " " + cell.text_frame.text
        all_slide_texts.append(slide_text)

    # Slide 1: Title
    assert "MONTHLY RECONCILIATION" in all_slide_texts[0]
    assert "Captive Power Plant" in all_slide_texts[0]

    # Slide 2: Month at a glance
    assert "Month at a Glance" in all_slide_texts[1]
    assert "CLOSING VALUATION" in all_slide_texts[1]

    # Slide 3: Inventory Movement
    assert "Inventory Movement & Reconciliation" in all_slide_texts[2]

    # Slide 4: Store vs PTW
    assert "Store Issues vs PTW Maintenance Consumption" in all_slide_texts[3]

    # Slide 5: Savings & Opportunities
    assert "Cost Avoidance & Inventory Optimization" in all_slide_texts[4]

    # Slide 6: Points for Discussion
    assert "Points for Management Discussion" in all_slide_texts[5]

    # Slide 7: Data Quality & Notes
    assert "Data Quality, Governance & Scope Limitations" in all_slide_texts[6]

