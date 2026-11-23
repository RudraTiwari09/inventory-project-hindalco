"""
PTW (Permit to Work) view for Recon Dashboard.
Operations department permit logs, maintenance work orders, and consumption.
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app import state
from app.ui.components import (
    apply_chart_theme,
    chart_card,
    empty_state,
    inject_css,
    kpi_card,
    page_header,
)
from app.ui.theme import (
    ACCENT_AMBER,
    ACCENT_BLUE,
    ACCENT_CORAL,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_PURPLE,
    COLOR_TEXT,
)


def render() -> None:
    """Render the Permit to Work view."""
    inject_css()

    page_header(
        "Permit to work",
        "Operations department permit logs and spares consumption",
    )

    tab_dash, tab_search, tab_insights = st.tabs(
        ["Dashboard", "Search & list", "Insights"]
    )

    selected_period = state.get("selected_period")
    ptw_df = state.get("ptw_df")
    if ptw_df is None or ptw_df.empty:
        try:
            from core.database import get_engine
            from core.data_access import get_ptw
            eng = get_engine()
            ptw_df = get_ptw(eng, selected_period)
        except Exception:
            pass

    has_data = ptw_df is not None and not ptw_df.empty


    with tab_dash:
        if not has_data:
            empty_state("📋", "No permit data loaded yet.")
            col_l, col_btn, col_r = st.columns([2, 1, 2])
            with col_btn:
                if st.button("Upload data", key="btn_ptw_upload", type="primary", width="stretch"):
                    st.switch_page("views/upload.py")
        else:
            df = ptw_df.copy()

            total_permits = len(df)
            departments = df["department"].nunique() if "department" in df.columns else 0

            equip_count = (
                df["equipment_tag"].nunique() if "equipment_tag" in df.columns else 0
            )

            total_qty_used = 0
            if "quantity_used" in df.columns:
                total_qty_used = int(
                    pd.to_numeric(df["quantity_used"], errors="coerce").fillna(0).sum()
                )

            # KPI cards
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                kpi_card("Total permits", f"{total_permits:,}", "Active work permits", ACCENT_CYAN)
            with c2:
                kpi_card("Departments", str(departments), "Operating units", ACCENT_PURPLE)
            with c3:
                kpi_card("Spares used", f"{total_qty_used:,}", "Logged parts consumed", ACCENT_GREEN)
            with c4:
                kpi_card("Equipment units", f"{equip_count:,}", "Unique tagged units", ACCENT_AMBER)

            st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)

            # Charts row
            col_dept, col_equip = st.columns(2)

            with col_dept:
                if "department" in df.columns:
                    dept_counts = df["department"].value_counts().reset_index()
                    dept_counts.columns = ["department", "count"]
                    fig_dept = px.bar(dept_counts, x="department", y="count", text="count")
                    fig_dept.update_traces(
                        marker_color=ACCENT_CYAN,
                        textposition="outside",
                        textfont=dict(color=COLOR_TEXT, size=11),
                    )
                    fig_dept.update_layout(
                        yaxis_title="Permits count",
                        xaxis_title="",
                        height=260,
                    )
                    chart_card("Permits by department", fig_dept)

            with col_equip:
                if "equipment_tag" in df.columns:
                    top_equip = df["equipment_tag"].value_counts().head(8).reset_index()
                    top_equip.columns = ["equipment_tag", "count"]
                    fig_equip = px.bar(top_equip, x="equipment_tag", y="count", text="count")
                    fig_equip.update_traces(
                        marker_color=ACCENT_PURPLE,
                        textposition="outside",
                        textfont=dict(color=COLOR_TEXT, size=11),
                    )
                    fig_equip.update_layout(
                        yaxis_title="Permits count",
                        xaxis_title="",
                        height=260,
                    )
                    chart_card("Top equipment serviced", fig_equip)

            # Permits log preview
            st.markdown(
                "<p style='font-size:0.95rem; font-weight:600; color:#E8F1F2; margin-top:10px; margin-bottom:8px;'>Permit logs</p>",
                unsafe_allow_html=True,
            )
            display_cols = [c for c in df.columns if not c.startswith("_")]
            st.dataframe(df[display_cols].head(50), width="stretch")

    with tab_search:
        empty_state("🔍", "Permit log search coming soon.")

    with tab_insights:
        empty_state("💡", "Work permit analytics coming soon.")


if __name__ == "__main__":
    render()

