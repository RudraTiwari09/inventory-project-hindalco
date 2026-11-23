"""
Landing view for the Recon Dashboard.
Shown immediately after login. Offers two equal paths:
1. Update data (Admin only)
2. Open reports (View previous analysis)
"""

import streamlit as st

from app import state
from app.ui.components import inject_css
from app.ui.icons import icon
from app.ui.theme import (
    COLOR_CARD,
    COLOR_CARD_BORDER,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
    ACCENT_GREEN,
    ACCENT_AMBER,
    ACCENT_CYAN,
)
from core.database import get_engine
from core.data_access import (
    get_latest_snapshot_info,
    get_latest_period,
    is_snapshot_stale,
)
from core.auth import is_admin


def render_landing():
    """Render the post-login landing screen."""
    inject_css()
    engine = get_engine()

    # Hide sidebar on landing screen per Section 3
    st.markdown(
        """
        <style>
        body:has(.landing-wrapper) [data-testid="stSidebar"],
        body:has(.landing-wrapper) [data-testid="collapsedControl"] {
            display: none !important;
        }
        .landing-wrapper {
            max-width: 1000px;
            margin: 6vh auto 0 auto;
            padding: 0 16px;
        }
        /* Custom card styling for landing page */
        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 14px !important;
            border: 1px solid #1E4A52 !important;
            background-color: #112B34 !important;
            padding: 28px !important;
            height: 100% !important;
            min-height: 260px !important;
            display: flex !important;
            flex-direction: column !important;
            justify-content: space-between !important;
            transition: border-color 0.2s ease, transform 0.2s ease;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:hover {
            border-color: #2ED573 !important;
        }
        .landing-card-dimmed div[data-testid="stVerticalBlockBorderWrapper"],
        div[data-testid="stVerticalBlockBorderWrapper"].card-dimmed {
            opacity: 0.60 !important;
            border-color: #1E4A52 !important;
        }
        .landing-card-dimmed div[data-testid="stVerticalBlockBorderWrapper"]:hover {
            border-color: #1E4A52 !important;
            transform: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    user = state.get("user") or {}
    name = user.get("full_name") or user.get("username") or "User"
    role = user.get("role", "viewer")
    admin = is_admin(user)

    # Top header bar: Left aligned with cards, Right aligned with cards
    st.markdown('<div class="landing-wrapper">', unsafe_allow_html=True)

    header_col1, header_col2 = st.columns([5, 3])
    with header_col1:
        st.markdown(
            f"""
            <div style="font-size: 26px; font-weight: 700; color: {COLOR_TEXT}; margin-top: 2px;">
                Recon Dashboard
            </div>
            <div style="font-size: 14px; color: {COLOR_TEXT_MUTED}; margin-top: 4px; margin-bottom: 28px;">
                Captive Power Plant &middot; Monthly data reconciliation
            </div>
            """,
            unsafe_allow_html=True,
        )
    with header_col2:
        user_col, logout_col = st.columns([1.6, 1])
        with user_col:
            role_label = role.capitalize()
            user_svg = icon("user", size=14, color=ACCENT_CYAN, extra_style="margin-right: 6px;")
            st.markdown(
                f"""
                <div style="display: flex; justify-content: flex-end; align-items: center; height: 38px; margin-top: 4px;">
                    <span style="background: rgba(255,255,255,0.06); border: 1px solid {COLOR_CARD_BORDER}; border-radius: 20px; padding: 6px 14px; font-size: 13px; color: {COLOR_TEXT}; display: inline-flex; align-items: center;">
                        {user_svg} {name} &bull; <strong style="color: {ACCENT_CYAN}; margin-left: 4px;">{role_label}</strong>
                    </span>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with logout_col:
            st.markdown('<div style="margin-top: 4px;"></div>', unsafe_allow_html=True)
            if st.button("Log out", key="landing_logout_btn", type="secondary", use_container_width=True):
                state.clear_all_state()
                st.rerun()

    # Query snapshot metadata
    inv_info = get_latest_snapshot_info(engine, "inventory")
    ptw_info = get_latest_snapshot_info(engine, "ptw")
    latest_period = get_latest_period(engine)
    has_data = bool(inv_info or ptw_info or latest_period)

    # Two large equal cards
    card_col1, card_col2 = st.columns(2)

    with card_col1:
        with st.container(border=True):
            # Tinted rounded square with upload SVG
            upload_svg = icon("upload", size=24, color=ACCENT_GREEN)
            st.markdown(
                f"""
                <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(46,213,115,0.12); display: flex; align-items: center; justify-content: center; margin-bottom: 20px;">
                    {upload_svg}
                </div>
                <div style="font-size: 20px; font-weight: 700; color: {COLOR_TEXT}; margin-bottom: 6px;">
                    Update data
                </div>
                <div style="font-size: 13px; color: {COLOR_TEXT_MUTED}; min-height: 38px; margin-bottom: 24px; line-height: 1.4;">
                    Upload monthly files or continue with active snapshot
                </div>
                """,
                unsafe_allow_html=True,
            )
            if admin:
                if st.button("Update data", key="btn_start_upload", type="primary", use_container_width=True):
                    state.reset_upload_state()
                    state.set("flow_stage", "upload")
                    st.rerun()
            else:
                st.button(
                    "Admin only",
                    key="btn_upload_disabled",
                    disabled=True,
                    help="Viewer accounts cannot upload files",
                    use_container_width=True,
                )

    with card_col2:
        with st.container(border=True):
            # Tinted rounded square with analytics SVG
            analytics_svg = icon("analytics", size=24, color=ACCENT_CYAN)
            period_hint = f"Latest period: {latest_period}" if latest_period else "No data loaded yet"
            st.markdown(
                f"""
                <div style="width: 48px; height: 48px; border-radius: 12px; background: rgba(28,200,238,0.12); display: flex; align-items: center; justify-content: center; margin-bottom: 20px;">
                    {analytics_svg}
                </div>
                <div style="font-size: 20px; font-weight: 700; color: {COLOR_TEXT}; margin-bottom: 6px;">
                    Open reports
                </div>
                <div style="font-size: 13px; color: {COLOR_TEXT_MUTED}; min-height: 38px; margin-bottom: 24px; line-height: 1.4;">
                    {period_hint}
                </div>
                """,
                unsafe_allow_html=True,
            )
            if has_data:
                if st.button("Open reports", key="btn_open_dashboard", type="secondary", use_container_width=True):
                    state.set("flow_stage", "main")
                    state.set("selected_period", latest_period)
                    st.rerun()
            else:
                st.button(
                    "No data yet — ask an admin to upload",
                    key="btn_dashboard_disabled",
                    disabled=True,
                    help="Upload data first to view analysis",
                    use_container_width=True,
                )

    # Status strip below cards
    st.markdown('<div style="margin-top: 24px;"></div>', unsafe_allow_html=True)
    inv_p = inv_info["period"] if inv_info else "No data"
    ptw_p = ptw_info["period"] if ptw_info else "No data"

    inv_stale = is_snapshot_stale(engine, inv_info["as_of_date"]) if inv_info else False
    ptw_stale = is_snapshot_stale(engine, ptw_info["as_of_date"]) if ptw_info else False

    inv_dot = f'<span style="color: {ACCENT_GREEN}; margin-right: 4px;">●</span>' if inv_info and not inv_stale else (
        f'<span style="color: {ACCENT_AMBER}; margin-right: 4px;">●</span>' if inv_info else f'<span style="color: {COLOR_TEXT_MUTED}; margin-right: 4px;">●</span>'
    )
    ptw_dot = f'<span style="color: {ACCENT_GREEN}; margin-right: 4px;">●</span>' if ptw_info and not ptw_stale else (
        f'<span style="color: {ACCENT_AMBER}; margin-right: 4px;">●</span>' if ptw_info else f'<span style="color: {COLOR_TEXT_MUTED}; margin-right: 4px;">●</span>'
    )

    inv_suffix = "(Active)" if (inv_info and not inv_stale) else ("(Stale)" if inv_stale else "")
    ptw_suffix = "(Active)" if (ptw_info and not ptw_stale) else ("(Stale)" if ptw_stale else "")

    inv_stale_html = (
        f'<span style="background: rgba(246,196,83,0.18); color: {ACCENT_AMBER}; border: 1px solid {ACCENT_AMBER}; padding: 2px 8px; border-radius: 12px; font-size: 11px; margin-left: 6px; font-weight: 600;">Stale</span>'
        if inv_stale
        else ""
    )
    ptw_stale_html = (
        f'<span style="background: rgba(246,196,83,0.18); color: {ACCENT_AMBER}; border: 1px solid {ACCENT_AMBER}; padding: 2px 8px; border-radius: 12px; font-size: 11px; margin-left: 6px; font-weight: 600;">Stale</span>'
        if ptw_stale
        else ""
    )

    st.markdown(
        f"""
        <div style="display: flex; gap: 16px; align-items: center; padding: 12px 20px; background: {COLOR_CARD}; border: 1px solid {COLOR_CARD_BORDER}; border-radius: 12px;">
            <span style="font-size: 13px; color: {COLOR_TEXT_MUTED}; font-weight: 500;">Status:</span>
            <span style="font-size: 13px; color: {COLOR_TEXT}; display: inline-flex; align-items: center;">
                {inv_dot} Inventory: {inv_p} {inv_suffix} {inv_stale_html}
            </span>
            <span style="color: {COLOR_CARD_BORDER};">&bull;</span>
            <span style="font-size: 13px; color: {COLOR_TEXT}; display: inline-flex; align-items: center;">
                {ptw_dot} PTW: {ptw_p} {ptw_suffix} {ptw_stale_html}
            </span>
        </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    render_landing()
