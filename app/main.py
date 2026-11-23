"""
Recon Dashboard — Main application entry point.
Captive Power Plant (CPP) Inventory x Permit to Work (PTW) reconciliation platform.
Implements authentication check, flow routing (Login -> Landing -> Upload -> Review -> Main),
sidebar navigation, and global period selector.
"""

from pathlib import Path
import sys
from datetime import datetime, timezone

# Ensure project root is in Python module search path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

# Centralized session state initialization (must run before any accesses)
from app import state
state.init_state()

from app.ui.components import inject_css, safe_render
from app.ui.theme import COLOR_TEXT, COLOR_TEXT_MUTED, ACCENT_GREEN, ACCENT_CYAN
from core.database import get_engine, initialize_database
from core.settings import initialize_settings
from core.auth import check_session_timeout, is_admin
from core.data_access import get_available_periods, get_latest_period, get_latest_snapshot_info

# Initialize local SQLite database
engine = get_engine()
initialize_database(engine)
initialize_settings(engine)

# Page configuration
st.set_page_config(
    page_title="Recon",
    layout="wide",
    page_icon="🏭",
    initial_sidebar_state="expanded",
)

# Apply global dark styling
inject_css()

# Default period check
if not state.get("selected_period"):
    state.set("selected_period", get_latest_period(engine) or "Oct 2026")

# Inactivity timeout check (30 minutes)
if state.get("authenticated"):
    last_act = state.get("last_activity")
    if last_act and check_session_timeout(last_act, timeout_minutes=30):
        state.clear_all_state()
    else:
        state.set("last_activity", datetime.now(timezone.utc).isoformat())

# ROUTE 1: Unauthenticated -> Login view
if not state.get("authenticated") or state.get("flow_stage") == "login":
    from app.views.login import render_login
    safe_render(render_login, "Login Screen")
    st.stop()

# ROUTE 2: Landing screen
if state.get("flow_stage") == "landing":
    from app.views.landing import render_landing
    safe_render(render_landing, "Landing Screen")
    st.stop()

# ROUTE 3: Upload screen
if state.get("flow_stage") == "upload":
    from app.views.upload import render_upload
    safe_render(
        render_upload,
        "Upload Screen",
        on_back=lambda: (state.reset_upload_state(), state.set("flow_stage", "landing")),
    )
    st.stop()

# ROUTE 4: Review screen
if state.get("flow_stage") == "review":
    # If no staged uploads exist, redirect to the main project dashboard
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            staged_count = conn.execute(
                text("SELECT COUNT(*) FROM uploads WHERE status = 'staged'")
            ).scalar()
    except Exception:
        staged_count = 0

    if not staged_count:
        state.set("flow_stage", "main")
        st.rerun()

    def _run_review():
        try:
            from app.views.monthly_changes import render_monthly_changes
            render_monthly_changes(mode="review")
        except Exception:
            from app.views.upload import render_upload
            render_upload()

    safe_render(
        _run_review,
        "Review Screen",
        on_back=lambda: (state.reset_upload_state(), state.set("flow_stage", "landing")),
    )
    st.stop()

# ROUTE 5: Main Application Mode (Sidebar + Navigation Pages)
# Force sidebar and navigation controls to be visible
st.markdown(
    """
    <style>
    [data-testid="stSidebar"],
    section[data-testid="stSidebar"] {
        display: flex !important;
        visibility: visible !important;
        opacity: 1 !important;
    }
    [data-testid="collapsedControl"],
    header[data-testid="stHeader"] [data-testid="collapsedControl"],
    [data-testid="stExpandSidebarButton"] {
        display: flex !important;
        visibility: visible !important;
        opacity: 1 !important;
        z-index: 100000 !important;
    }
    header[data-testid="stHeader"] {
        display: flex !important;
        visibility: visible !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Auto-expand sidebar if it was collapsed
st.html(
    """
    <script>
    try {
        const expandBtn = document.querySelector('[data-testid="stExpandSidebarButton"], [data-testid="collapsedControl"] button');
        if (expandBtn) {
            expandBtn.click();
        }
    } catch(e) {}
    </script>
    """
)

# Sidebar brand wordmark, period selector, page toggle, actions, and status
with st.sidebar:
    st.markdown('<div class="recon-wordmark">RECON</div>', unsafe_allow_html=True)

    # Global Period Selector
    available_periods = get_available_periods(engine)
    if not available_periods:
        available_periods = ["Oct 2026", "Sep 2026", "Aug 2026"]

    current_idx = 0
    selected_p = state.get("selected_period")
    if selected_p in available_periods:
        current_idx = available_periods.index(selected_p)

    sel_period = st.selectbox(
        "Period",
        options=available_periods,
        index=current_idx,
        key="global_period_selector",
        help="Global reporting period for all dashboard pages",
    )
    state.set("selected_period", sel_period)

    st.markdown('<div style="margin: 14px 0 10px 0; border-top: 1px solid rgba(255,255,255,0.08);"></div>', unsafe_allow_html=True)
    st.markdown(
        "<p style='font-size:0.75rem; font-weight:700; letter-spacing:0.08em; color:#8FA9AE; text-transform:uppercase; margin-bottom:8px;'>Navigation</p>",
        unsafe_allow_html=True,
    )

    # Left-hand side Screen Navigation Toggle
    PAGES = [
        "Overview",
        "Inventory",
        "PTW",
        "Reconciliation",
        "Monthly changes",
        "Savings",
        "Reports",
    ]
    if is_admin(state.get("user")):
        PAGES.append("Upload history")

    PAGE_ICONS = {
        "Overview": "📊",
        "Inventory": "📦",
        "PTW": "📝",
        "Reconciliation": "⚖️",
        "Monthly changes": "📅",
        "Savings": "💰",
        "Reports": "📑",
        "Upload history": "🕒",
    }

    current_page = state.get("selected_page") or "Overview"
    if current_page not in PAGES:
        current_page = "Overview"

    selected_nav = st.radio(
        "Navigation",
        options=PAGES,
        index=PAGES.index(current_page),
        format_func=lambda p: f"{PAGE_ICONS.get(p, '📄')}  {p}",
        key="main_nav_radio",
        label_visibility="collapsed",
    )
    state.set("selected_page", selected_nav)

    st.markdown('<div style="margin: 14px 0 10px 0; border-top: 1px solid rgba(255,255,255,0.08);"></div>', unsafe_allow_html=True)

    # Actions: Update data, Log out
    col_act1, col_act2 = st.columns(2)
    with col_act1:
        if st.button("Update data", key="sidebar_update_data_btn", use_container_width=True):
            state.reset_upload_state()
            state.set("flow_stage", "upload")
            st.rerun()
    with col_act2:
        if st.button("Log out", key="sidebar_logout_btn", use_container_width=True):
            state.clear_all_state()
            st.rerun()

    # Status indicators at bottom of sidebar
    inv_info = get_latest_snapshot_info(engine, "inventory")
    ptw_info = get_latest_snapshot_info(engine, "ptw")

    inv_loaded = inv_info is not None or state.get("inventory_df") is not None
    ptw_loaded = ptw_info is not None or state.get("ptw_df") is not None

    inv_status = inv_info["period"] if inv_info else ("Loaded" if inv_loaded else "Not loaded")
    inv_dot = '<span class="status-dot-green">●</span>' if inv_loaded else '<span class="status-dot-muted">●</span>'

    ptw_status = ptw_info["period"] if ptw_info else ("Loaded" if ptw_loaded else "Not loaded")
    ptw_dot = '<span class="status-dot-green">●</span>' if ptw_loaded else '<span class="status-dot-muted">●</span>'

    status_html = f"""
    <div class="sidebar-status-box" style="margin-top: 24px;">
        <div>Inventory {inv_dot} {inv_status}</div>
        <div>PTW {ptw_dot} {ptw_status}</div>
    </div>
    """
    st.markdown(status_html, unsafe_allow_html=True)

# ── Render Selected View on the Dashboard ───────────────────────────────────
if selected_nav == "Overview":
    from app.views.overview import render as render_overview
    safe_render(render_overview, "Overview")
elif selected_nav == "Inventory":
    from app.views.inventory import render as render_inventory
    safe_render(render_inventory, "Inventory")
elif selected_nav == "PTW":
    from app.views.ptw import render as render_ptw
    safe_render(render_ptw, "PTW")
elif selected_nav == "Reconciliation":
    from app.views.reconciliation import render as render_reconciliation
    safe_render(render_reconciliation, "Reconciliation")
elif selected_nav == "Monthly changes":
    from app.views.monthly_changes import render_monthly_changes
    safe_render(lambda: render_monthly_changes(mode="published"), "Monthly changes")
elif selected_nav == "Savings":
    from app.views.savings import render_savings
    safe_render(render_savings, "Savings")
elif selected_nav == "Reports":
    from app.views.reports import render_reports
    safe_render(render_reports, "Reports")
elif selected_nav == "Upload history":
    from app.views.upload_history import render_upload_history
    safe_render(render_upload_history, "Upload history")
