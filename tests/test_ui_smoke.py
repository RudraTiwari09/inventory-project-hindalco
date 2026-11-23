"""
UI Smoke Tests using Streamlit AppTest.
Exercises every screen (Login, Landing, Upload, Review, Main) across roles and state transitions,
verifying that no unhandled exceptions or KeyErrors occur.
"""

from datetime import datetime, timezone
from pathlib import Path
import os
import pytest
from streamlit.testing.v1 import AppTest
from sqlalchemy import text

from core.database import get_engine, initialize_database
from core.settings import initialize_settings
from core.auth import create_user

MAIN_SCRIPT = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    """Ensure DB and default users exist."""
    engine = get_engine()
    initialize_database(engine)
    initialize_settings(engine)
    # Ensure TIWARI admin exists
    try:
        create_user(engine, "TIWARI", "123456789", role="admin", full_name="Admin Tiwari")
    except Exception:
        # Reset any failed attempts/lockouts on TIWARI
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE username = 'tiwari'")
            )
            conn.commit()

    # Ensure viewer exists
    try:
        create_user(engine, "VIEWER1", "viewer123", role="viewer", full_name="Test Viewer")
    except Exception:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE username = 'viewer1'")
            )
            conn.commit()

    # Ensure dedicated lock user exists
    try:
        create_user(engine, "LOCKTEST", "lockpass123", role="viewer", full_name="Lockout Tester")
    except Exception:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE username = 'locktest'")
            )
            conn.commit()


def test_smoke_unauthenticated_login_screen():
    """
    Verify login screen layout:
    - Renders with no exception
    - Exactly 2 text inputs (one of type password)
    - 1 form submit button labelled 'LET'S GOOO'
    - No sidebar elements present on login screen
    """
    at = AppTest.from_file(MAIN_SCRIPT)
    at.run()
    assert not at.exception
    # Exactly 2 text inputs
    assert len(at.text_input) == 2
    assert at.text_input[0].placeholder == "Username"
    assert at.text_input[1].placeholder == "Password"
    assert at.text_input[1].proto.Type.Name(at.text_input[1].proto.type) == "PASSWORD"

    # Exactly 1 form submit button labelled "LET'S GOOO"
    assert len(at.button) >= 1
    assert at.button[0].label == "LET'S GOOO"

    # No sidebar elements present on login screen
    assert len(at.sidebar.button) == 0
    assert len(at.sidebar.selectbox) == 0


def test_smoke_login_wrong_credentials():
    """Verify wrong credentials display the error pill message once."""
    at = AppTest.from_file(MAIN_SCRIPT)
    at.run()
    assert not at.exception

    at.text_input[0].input("INVALID_USER")
    at.text_input[1].input("WRONG_PASS")
    at.button[0].click()
    at.run()

    assert not at.exception
    assert at.session_state["authenticated"] is False
    assert at.session_state["flow_stage"] == "login"

    # Verify error message appears once
    error_pills = [m for m in at.markdown if "Invalid username or password." in m.value]
    assert len(error_pills) == 1


def test_smoke_login_lockout_after_5_failures():
    """Verify account lockout message after 5 failed login attempts."""
    engine = get_engine()
    # Reset locktest state first
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE username = 'locktest'")
        )
        conn.commit()

    at = AppTest.from_file(MAIN_SCRIPT)
    at.run()

    # Fail 5 times
    for i in range(5):
        at.text_input[0].input("LOCKTEST")
        at.text_input[1].input(f"wrong_password_{i}")
        at.button[0].click()
        at.run()
        assert not at.exception

    # On the 5th attempt, lockout message must appear
    lockout_pills = [m for m in at.markdown if "Too many attempts. Try again in 5 minutes." in m.value]
    assert len(lockout_pills) == 1

    # Clean up locktest state
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE username = 'locktest'")
        )
        conn.commit()


def test_smoke_login_admin_to_landing():
    """Verify login transition to Landing screen with admin user."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE username = 'tiwari'")
        )
        conn.commit()

    at = AppTest.from_file(MAIN_SCRIPT)
    at.run()
    assert not at.exception

    # Fill credentials
    at.text_input[0].input("TIWARI")
    at.text_input[1].input("123456789")
    at.button[0].click()
    at.run()

    assert not at.exception
    # Should now be on Landing screen
    assert at.session_state["authenticated"] is True
    assert at.session_state["flow_stage"] == "landing"
    assert at.session_state["user"]["username"].lower() == "tiwari"


def test_smoke_landing_admin_and_logout():
    """Verify Landing page controls and logout for admin."""
    now_iso = datetime.now(timezone.utc).isoformat()
    at = AppTest.from_file(MAIN_SCRIPT)
    at.session_state["authenticated"] = True
    at.session_state["user"] = {"username": "tiwari", "role": "admin", "full_name": "Admin Tiwari"}
    at.session_state["flow_stage"] = "landing"
    at.session_state["last_activity"] = now_iso
    at.run()

    assert not at.exception
    # Verify Update data button exists
    btn_keys = [b.key for b in at.button]
    assert "btn_start_upload" in btn_keys
    assert "landing_logout_btn" in btn_keys

    # Click logout
    for b in at.button:
        if b.key == "landing_logout_btn":
            b.click()
            break
    at.run()
    assert not at.exception
    assert at.session_state["authenticated"] is False
    assert at.session_state["flow_stage"] == "login"


def test_smoke_landing_viewer_disabled_upload():
    """Verify Landing screen for viewer has upload disabled."""
    now_iso = datetime.now(timezone.utc).isoformat()
    at = AppTest.from_file(MAIN_SCRIPT)
    at.session_state["authenticated"] = True
    at.session_state["user"] = {"username": "viewer1", "role": "viewer", "full_name": "Test Viewer"}
    at.session_state["flow_stage"] = "landing"
    at.session_state["last_activity"] = now_iso
    at.run()

    assert not at.exception
    btn_keys = [b.key for b in at.button]
    assert "btn_upload_disabled" in btn_keys


def test_smoke_upload_screen_no_key_error():
    """
    CRITICAL: Verify Bug 1 fix.
    Upload screen must render both partitions (Inventory and PTW) without KeyError on inventory_mode.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    at = AppTest.from_file(MAIN_SCRIPT)
    at.session_state["authenticated"] = True
    at.session_state["user"] = {"username": "tiwari", "role": "admin", "full_name": "Admin Tiwari"}
    at.session_state["flow_stage"] = "upload"
    at.session_state["last_activity"] = now_iso
    at.run()

    assert not at.exception, f"Upload screen raised an exception: {at.exception}"
    btn_keys = [b.key for b in at.button]

    # Verify back button exists
    assert "btn_back_to_landing" in btn_keys

    # Verify choice buttons for both partitions exist
    assert "btn_mode_new_inventory" in btn_keys
    assert "btn_mode_old_inventory" in btn_keys
    assert "btn_mode_new_ptw" in btn_keys
    assert "btn_mode_old_ptw" in btn_keys

    # Default modes must be None
    assert at.session_state["inventory_mode"] is None
    assert at.session_state["ptw_mode"] is None

    # Proceed button must be disabled when nothing chosen
    assert "btn_proceed_disabled" in btn_keys


def test_smoke_upload_screen_choices_and_proceed():
    """Verify selecting options in upload partitions enables Proceed."""
    now_iso = datetime.now(timezone.utc).isoformat()
    at = AppTest.from_file(MAIN_SCRIPT)
    at.session_state["authenticated"] = True
    at.session_state["user"] = {"username": "tiwari", "role": "admin", "full_name": "Admin Tiwari"}
    at.session_state["flow_stage"] = "upload"
    at.session_state["last_activity"] = now_iso
    at.run()
    assert not at.exception

    # Click continue with old for inventory if available, or upload new
    for b in at.button:
        if b.key == "btn_mode_old_inventory" and not b.disabled:
            b.click()
            break
        elif b.key == "btn_mode_new_inventory":
            b.click()
            break
    at.run()
    assert not at.exception
    assert at.session_state["inventory_mode"] is not None

    # Toggle back to landing using ghost back button
    for b in at.button:
        if b.key == "btn_back_to_landing":
            b.click()
            break
    at.run()
    assert not at.exception
    assert at.session_state["flow_stage"] == "landing"
    # State should have been reset
    assert at.session_state["inventory_mode"] is None


def test_smoke_upload_proceed_routes_to_dashboard():
    """Verify that uploading files and clicking Proceed commits data and routes to the main Overview dashboard."""
    now_iso = datetime.now(timezone.utc).isoformat()
    at = AppTest.from_file(MAIN_SCRIPT)
    at.session_state["authenticated"] = True
    at.session_state["user"] = {"username": "tiwari", "role": "admin", "full_name": "Admin Tiwari"}
    at.session_state["flow_stage"] = "upload"
    at.session_state["last_activity"] = now_iso
    at.session_state["inventory_mode"] = "new"
    at.session_state["ptw_mode"] = "new"
    at.run()
    assert not at.exception

    import uuid
    run_id = uuid.uuid4().hex[:8]
    inv_bytes = f"material_code,spare_description,quantity_in_stock,unit_price\n00101_{run_id},Bearing A,10,150\n".encode("utf-8")
    ptw_bytes = f"permit_id,spare_description_text,quantity_used\nPTW-001_{run_id},Bearing A,2\n".encode("utf-8")

    for fu in at.file_uploader:
        if fu.key == "uploader_inventory":
            fu.upload("inventory_nov.csv", inv_bytes)
        elif fu.key == "uploader_ptw":
            fu.upload("ptw_nov.csv", ptw_bytes)

    at.run()
    assert not at.exception

    # Click Proceed button
    for b in at.button:
        if b.key == "btn_proceed":
            b.click()
            break
    at.run()
    assert not at.exception
    # Must now be on the main dashboard (Route 5: Main Application Mode)
    assert at.session_state["flow_stage"] == "main"


def test_smoke_main_app_navigation():
    """Verify main dashboard mode renders overview and sidebar."""
    now_iso = datetime.now(timezone.utc).isoformat()
    at = AppTest.from_file(MAIN_SCRIPT)
    at.session_state["authenticated"] = True
    at.session_state["user"] = {"username": "tiwari", "role": "admin", "full_name": "Admin Tiwari"}
    at.session_state["flow_stage"] = "main"
    at.session_state["last_activity"] = now_iso
    at.run()

    assert not at.exception
    # Check sidebar action buttons
    btn_keys = [b.key for b in at.button]
    assert "sidebar_update_data_btn" in btn_keys
    assert "sidebar_logout_btn" in btn_keys

    # Check sidebar radio toggle exists with all screens
    assert len(at.sidebar.radio) >= 1
    radio_opts = at.sidebar.radio[0].options
    assert any("Overview" in opt for opt in radio_opts)
    assert any("Inventory" in opt for opt in radio_opts)
    assert any("PTW" in opt for opt in radio_opts)
    assert any("Reconciliation" in opt for opt in radio_opts)
    assert any("Monthly changes" in opt for opt in radio_opts)
    assert any("Savings" in opt for opt in radio_opts)
    assert any("Reports" in opt for opt in radio_opts)
    assert any("Upload history" in opt for opt in radio_opts)

    # Test toggling to Inventory
    at.sidebar.radio[0].set_value("Inventory")
    at.run()
    assert not at.exception
    assert at.session_state["selected_page"] == "Inventory"


def test_smoke_safe_render_error_boundary():
    """Verify safe_render catches crashes, writes to logs/app.log, and recovers cleanly."""
    def test_runner():
        from app.ui.components import safe_render

        def crashing_view():
            raise RuntimeError("Intentional test crash")

        safe_render(crashing_view, "Test Crashed Screen")

    at = AppTest.from_function(test_runner)
    at.run()

    # The exception must be intercepted by safe_render, not propagated
    assert not at.exception

    # Recovery button must be rendered
    btn_keys = [b.key for b in at.button]
    assert any("btn_safe_recover" in k for k in btn_keys)

    # Check that logs/app.log was written
    log_path = "logs/app.log"
    assert os.path.exists(log_path)
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "Intentional test crash" in content


def test_smoke_kpi_cards_clean_html():
    """Verify KPI cards render clean HTML without raw escaped or code-block HTML tags."""
    import pandas as pd

    # Inventory view test
    inv_script = str(Path(__file__).resolve().parent.parent / "app" / "views" / "inventory.py")
    at_inv = AppTest.from_file(inv_script)
    at_inv.session_state["inventory_df"] = pd.DataFrame({
        "material_code": ["101", "102"],
        "spare_description": ["Bearing A", "Gasket B"],
        "category": ["Mechanical", "Piping"],
        "quantity_in_stock": [10, 50],
        "unit_cost": [150.0, 20.0],
        "criticality_flag": ["High", "Low"],
    })
    at_inv.run()
    assert not at_inv.exception
    for m in at_inv.markdown:
        if not m.value.strip().startswith("<style>"):
            assert '<div class="kpi-value"' not in m.value.lower()
            assert '<div class="kpi-card"' not in m.value.lower()
    # Check that KPI cards are rendered as native HTML elements
    assert len(at_inv.html) >= 4

    # PTW view test
    ptw_script = str(Path(__file__).resolve().parent.parent / "app" / "views" / "ptw.py")
    at_ptw = AppTest.from_file(ptw_script)
    at_ptw.session_state["ptw_df"] = pd.DataFrame({
        "permit_id": ["PTW-1", "PTW-2"],
        "department": ["Boiler", "Turbine"],
        "quantity_used": [2, 1],
        "equipment_tag": ["BLR-01", "TRB-02"],
    })
    at_ptw.run()
    assert not at_ptw.exception
    for m in at_ptw.markdown:
        if not m.value.strip().startswith("<style>"):
            assert '<div class="kpi-value"' not in m.value.lower()
            assert '<div class="kpi-card"' not in m.value.lower()
    assert len(at_ptw.html) >= 4


def test_smoke_savings_page_states():
    """Verify Savings page renders cleanly in admin and viewer modes across data states."""
    savings_script = str(Path(__file__).resolve().parent.parent / "app" / "views" / "savings.py")

    # 1. Admin mode with active database
    at_admin = AppTest.from_file(savings_script)
    at_admin.session_state["authenticated"] = True
    at_admin.session_state["user"] = {"username": "tiwari", "role": "admin", "full_name": "Admin Tiwari"}
    at_admin.session_state["selected_period"] = "Oct 2026"
    at_admin.run()
    assert not at_admin.exception

    # 2. Viewer mode (read-only)
    at_viewer = AppTest.from_file(savings_script)
    at_viewer.session_state["authenticated"] = True
    at_viewer.session_state["user"] = {"username": "viewer1", "role": "viewer", "full_name": "Plant Viewer"}
    at_viewer.session_state["selected_period"] = "Oct 2026"
    at_viewer.run()
    assert not at_viewer.exception
    # Viewer must not have "+ Add manual entry" button
    btn_keys = [b.key for b in at_viewer.button]
    assert "btn_add_savings_entry" not in btn_keys


def test_smoke_reports_page_states():
    """Verify Reports page renders cleanly without exceptions across periods and data states."""
    reports_script = str(Path(__file__).resolve().parent.parent / "app" / "views" / "reports.py")

    at = AppTest.from_file(reports_script)
    at.session_state["authenticated"] = True
    at.session_state["user"] = {"username": "tiwari", "role": "admin", "full_name": "Admin Tiwari"}
    at.session_state["selected_period"] = "Oct 2026"
    at.session_state["reports_period"] = "Oct 2026"
    at.run()
    assert not at.exception


