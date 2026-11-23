"""
Inventory view for Recon Dashboard.
Storage department spare parts overview, search, and insights.
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
    format_currency_inr,
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
    """Render the Inventory view."""
    inject_css()

    page_header(
        "Inventory",
        "Storage and spare parts catalog management",
    )

    tab_dash, tab_search, tab_insights = st.tabs(
        ["Dashboard", "Search & list", "Insights"]
    )

    selected_period = state.get("selected_period")
    inv_df = state.get("inventory_df")
    if inv_df is None or inv_df.empty:
        try:
            from core.database import get_engine
            from core.data_access import get_inventory
            eng = get_engine()
            inv_df = get_inventory(eng, selected_period)
        except Exception:
            pass

    has_data = inv_df is not None and not inv_df.empty


    with tab_dash:
        if not has_data:
            empty_state("📦", "No inventory data loaded yet.")
            col_l, col_btn, col_r = st.columns([2, 1, 2])
            with col_btn:
                if st.button("Upload data", key="btn_inv_upload", type="primary", width="stretch"):
                    st.switch_page("views/upload.py")
        else:
            df = inv_df.copy()

            # Metric calculations
            total_items = len(df)
            categories = df["category"].nunique() if "category" in df.columns else 0

            crit_count = 0
            if "criticality_flag" in df.columns:
                crit_count = (df["criticality_flag"].astype(str).str.lower() == "high").sum()

            total_val = 0.0
            if "quantity_in_stock" in df.columns and "unit_cost" in df.columns:
                df["total_value"] = (
                    pd.to_numeric(df["quantity_in_stock"], errors="coerce").fillna(0)
                    * pd.to_numeric(df["unit_cost"], errors="coerce").fillna(0)
                )
                total_val = df["total_value"].sum()

            # KPI row
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                kpi_card("Total spares", f"{total_items:,}", "Active catalog SKUs", ACCENT_GREEN)
            with c2:
                kpi_card("Categories", str(categories), "Equipment classes", ACCENT_CYAN)
            with c3:
                kpi_card("High criticality", f"{crit_count:,}", "Critical spares", ACCENT_CORAL)
            with c4:
                kpi_card("Inventory value", format_currency_inr(total_val), "Total valuation", ACCENT_BLUE, icon="₹")

            st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)

            # Charts row
            col_cat, col_crit = st.columns(2)

            with col_cat:
                if "category" in df.columns:
                    cat_counts = df["category"].value_counts().reset_index()
                    cat_counts.columns = ["category", "count"]
                    fig_cat = px.bar(cat_counts, x="category", y="count", text="count")
                    fig_cat.update_traces(
                        marker_color=ACCENT_GREEN,
                        textposition="outside",
                        textfont=dict(color=COLOR_TEXT, size=11),
                    )
                    fig_cat.update_layout(
                        yaxis_title="Spares count",
                        xaxis_title="",
                        height=260,
                    )
                    chart_card("Spares by category", fig_cat)

            with col_crit:
                if "criticality_flag" in df.columns:
                    crit_counts = df["criticality_flag"].value_counts().reset_index()
                    crit_counts.columns = ["criticality", "count"]
                    fig_crit = px.bar(crit_counts, x="criticality", y="count", text="count")
                    fig_crit.update_traces(
                        marker_color=ACCENT_AMBER,
                        textposition="outside",
                        textfont=dict(color=COLOR_TEXT, size=11),
                    )
                    fig_crit.update_layout(
                        yaxis_title="Spares count",
                        xaxis_title="",
                        height=260,
                    )
                    chart_card("Criticality distribution", fig_crit)

            # Catalog preview
            st.markdown(
                "<p style='font-size:0.95rem; font-weight:600; color:#E8F1F2; margin-top:10px; margin-bottom:8px;'>Catalog records</p>",
                unsafe_allow_html=True,
            )
            # Display clean view without hidden columns
            display_cols = [c for c in df.columns if not c.startswith("_") and c != "total_value"]
            st.dataframe(df[display_cols].head(50), width="stretch")

    with tab_search:
        empty_state("🔍", "Search and item drilldown coming soon.")

    with tab_insights:
        empty_state("💡", "Inventory consumption insights coming soon.")


if __name__ == "__main__":
    render()

