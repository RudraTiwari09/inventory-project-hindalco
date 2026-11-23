"""
Overview page for Recon Dashboard.
Presents high-level metrics, stock ageing, department value, and top spares.
"""

from datetime import datetime
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app import state
from app.ui.components import (
    apply_chart_theme,
    chart_card,
    empty_state,
    format_currency_inr,
    gauge_card,
    inject_css,
    kpi_card,
    page_header,
)
from app.ui.demo_data import load_demo_datasets
from app.ui.theme import (
    ACCENT_AMBER,
    ACCENT_BLUE,
    ACCENT_CORAL,
    ACCENT_CYAN,
    ACCENT_GREEN,
    COLOR_CARD,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
)


def render() -> None:
    """Render the Overview dashboard view."""
    inject_css()

    page_header(
        "Overview",
        "Plant inventory and permit to work reconciliation status",
    )

    selected_period = state.get("selected_period")
    inv_df = state.get("inventory_df")
    ptw_df = state.get("ptw_df")

    # If not in session state, load from period data access layer
    if inv_df is None or inv_df.empty:
        try:
            from core.database import get_engine
            from core.data_access import get_inventory, get_ptw
            eng = get_engine()
            inv_df = get_inventory(eng, selected_period)
            ptw_df = get_ptw(eng, selected_period)
        except Exception:
            pass

    has_data = inv_df is not None and not inv_df.empty


    # ── State: No data loaded ─────────────────────────────────────────────────
    if not has_data:
        # Row 1: KPI placeholders with muted dashes
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            kpi_card("Inventory value", "—", "Awaiting data", ACCENT_BLUE, icon="₹")
        with col2:
            kpi_card("Total spares", "—", "Awaiting data", ACCENT_GREEN)
        with col3:
            kpi_card("PTW records", "—", "Awaiting data", ACCENT_CYAN)
        with col4:
            gauge_card("Match rate", None, max_val=100.0, accent_color=ACCENT_AMBER)
        with col5:
            kpi_card("Open anomalies", "—", "Awaiting data", ACCENT_CORAL)

        # Center empty state box
        st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)
        empty_state("📊", "No data loaded yet. Upload your datasets or load synthetic plant data to begin.")

        col_l, col_upload, col_demo, col_r = st.columns([1.5, 1, 1, 1.5])
        with col_upload:
            if st.button("Upload data", key="btn_ov_upload", type="primary", width="stretch"):
                st.switch_page("views/upload.py")
        with col_demo:
            if st.button("Use demo data", key="btn_ov_demo", type="secondary", width="stretch"):
                if load_demo_datasets():
                    st.toast("Demo data loaded successfully", icon="✅")
                    st.rerun()
        return

    # ── State: Data loaded ────────────────────────────────────────────────────

    # Calculate metrics
    inv_calc = inv_df.copy()
    if "quantity_in_stock" in inv_calc.columns and "unit_cost" in inv_calc.columns:
        inv_calc["total_value"] = (
            pd.to_numeric(inv_calc["quantity_in_stock"], errors="coerce").fillna(0)
            * pd.to_numeric(inv_calc["unit_cost"], errors="coerce").fillna(0)
        )
        total_inv_val = inv_calc["total_value"].sum()
    else:
        total_inv_val = 0.0

    total_spares = len(inv_df)
    total_ptw = len(ptw_df) if ptw_df is not None else 0

    # Row 1: 5 KPI cards
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        kpi_card("Inventory value", format_currency_inr(total_inv_val), "Total active valuation", ACCENT_BLUE, icon="₹")
    with col2:
        kpi_card("Total spares", f"{total_spares:,}", "Catalog SKU items", ACCENT_GREEN)
    with col3:
        kpi_card("PTW records", f"{total_ptw:,}", "Maintenance permits", ACCENT_CYAN)
    with col4:
        gauge_card("Match rate", None, max_val=100.0, accent_color=ACCENT_AMBER)
    with col5:
        kpi_card("Open anomalies", "0", "Reconciliation pending", ACCENT_CORAL)

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    # Row 2: 3 Chart cards
    c_dept, c_age, c_top = st.columns([1, 1, 1.25])

    # 1. Department Value Bar Chart
    with c_dept:
        if "department" in inv_calc.columns and "total_value" in inv_calc.columns:
            dept_df = (
                inv_calc.groupby("department")["total_value"]
                .sum()
                .reset_index()
                .sort_values("total_value", ascending=False)
            )
            dept_df["display_val"] = dept_df["total_value"] / 100_000.0  # In Lakhs

            fig_dept = px.bar(
                dept_df,
                x="department",
                y="display_val",
                text=dept_df["display_val"].apply(lambda v: f"{v:.1f}L"),
            )
            fig_dept.update_traces(
                marker_color=ACCENT_GREEN,
                textposition="outside",
                textfont=dict(color=COLOR_TEXT, size=10),
            )
            fig_dept.update_layout(
                yaxis_title="Value (Rs. Lakhs)",
                xaxis_title="",
                height=280,
            )
            chart_card("Value by department", fig_dept)
        else:
            fig_empty = go.Figure()
            fig_empty.update_layout(height=280)
            chart_card("Value by department", fig_empty)

    # 2. Stock Ageing Bar Chart
    with c_age:
        if "last_used_date" in inv_calc.columns:
            dates = pd.to_datetime(inv_calc["last_used_date"], errors="coerce")
            now = pd.Timestamp.now()
            months = (now - dates).dt.days // 30

            bins = [-1, 3, 6, 12, 24, 1000]
            labels = ["< 3 mos", "3-6 mos", "6-12 mos", "1-2 yrs", "> 2 yrs"]
            age_cat = pd.cut(months, bins=bins, labels=labels)

            age_counts = age_cat.value_counts().reindex(labels, fill_value=0).reset_index()
            age_counts.columns = ["bracket", "count"]

            never_used = dates.isna().sum()
            if never_used > 0:
                never_row = pd.DataFrame([{"bracket": "Never used", "count": never_used}])
                age_counts = pd.concat([age_counts, never_row], ignore_index=True)

            fig_age = px.bar(
                age_counts,
                x="bracket",
                y="count",
                text="count",
            )
            fig_age.update_traces(
                marker_color=ACCENT_CYAN,
                textposition="outside",
                textfont=dict(color=COLOR_TEXT, size=10),
            )
            fig_age.update_layout(
                yaxis_title="Spares count",
                xaxis_title="",
                height=280,
            )
            chart_card("Stock ageing", fig_age)
        else:
            fig_empty = go.Figure()
            fig_empty.update_layout(height=280)
            chart_card("Stock ageing", fig_empty)

    # 3. Top 10 Spares with Value / Quantity pill toggle
    with c_top:
        with st.container():
            col_title, col_pill = st.columns([1.5, 1])
            with col_title:
                st.markdown(
                    "<p style='font-size:0.95rem; font-weight:600; color:#E8F1F2; margin-bottom:4px; margin-top:6px;'>Top 10 spares</p>",
                    unsafe_allow_html=True,
                )
            with col_pill:
                metric_choice = st.segmented_control(
                    "Top metric",
                    ["Value", "Quantity"],
                    default="Value",
                    label_visibility="collapsed",
                    key="top_spares_pill",
                )

            desc_col = (
                "spare_description"
                if "spare_description" in inv_calc.columns
                else "_cleaned_description"
                if "_cleaned_description" in inv_calc.columns
                else inv_calc.columns[1]
            )

            metric_col = "total_value" if metric_choice == "Value" else "quantity_in_stock"
            if metric_col not in inv_calc.columns:
                metric_col = "quantity_in_stock" if "quantity_in_stock" in inv_calc.columns else inv_calc.columns[0]

            top_df = (
                inv_calc.sort_values(metric_col, ascending=False)
                .head(10)
                .sort_values(metric_col, ascending=True)  # ascending for horizontal bar
            )

            # Truncate labels for neat display
            top_df["short_desc"] = (
                top_df[desc_col].astype(str).apply(lambda s: s[:22] + "…" if len(s) > 22 else s)
            )

            fig_top = px.bar(
                top_df,
                x=metric_col,
                y="short_desc",
                orientation="h",
            )
            bar_color = ACCENT_GREEN if metric_choice == "Value" else ACCENT_AMBER
            fig_top.update_traces(
                marker_color=bar_color,
                textposition="auto",
            )
            x_title = "Value (Rs.)" if metric_choice == "Value" else "Units in stock"
            fig_top.update_layout(
                xaxis_title=x_title,
                yaxis_title="",
                height=280,
                margin=dict(l=10, r=10, t=10, b=20),
            )
            apply_chart_theme(fig_top)
            st.plotly_chart(
                fig_top,
                width="stretch",
                config={"displayModeBar": False},
                key="chart_top_spares_by_metric",
            )


if __name__ == "__main__":
    render()

