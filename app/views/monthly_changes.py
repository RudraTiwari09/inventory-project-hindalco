"""
Monthly Changes view for Recon Dashboard.
Used as:
1. Staging Review screen before publish (mode='review')
2. Permanent historical analysis screen in main app (mode='published')
Displays KPI rows, waterfall/gap charts, tabbed audit tables, Excel export,
and publication controls.
"""

import io
from datetime import datetime, timezone
from decimal import Decimal
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import text

from app import state
from app.ui.components import (
    apply_chart_theme,
    chart_card,
    empty_state,
    format_currency_inr,
    inject_css,
    kpi_card,
    page_header,
    status_chip,
)
from app.ui.theme import (
    ACCENT_AMBER,
    ACCENT_BLUE,
    ACCENT_CORAL,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_PURPLE,
    COLOR_CARD,
    COLOR_CARD_BORDER,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
)
from core.database import get_engine
from core.data_access import (
    get_available_periods,
    get_latest_period,
    get_inventory,
    get_ptw,
)
from core.inventory_diff import compute_inventory_diff
from core.matching import match_ptw_lines_to_inventory, compute_store_vs_ptw_gap
from core.pipeline import commit_and_publish, discard_staged_upload
from core.auth import is_admin


def render_monthly_changes(mode: str = "published"):
    """
    Render Monthly Changes view.
    mode: 'review' (during upload workflow) or 'published' (in main navigation).
    """
    inject_css()
    engine = get_engine()
    user = state.get("user") or {}
    admin = is_admin(user)

    if mode == "review":
        # Hide sidebar on review view per Section 3
        st.markdown(
            """
            <style>
            body:has([class*="st-key-review_changes_wrapper"]) [data-testid="stSidebar"],
            body:has([class*="st-key-review_changes_wrapper"]) [data-testid="collapsedControl"] {
                display: none !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        # Header for review workflow
        inv_val = state.get("inventory_validation") or state.get("inv_validation")
        ptw_val = state.get("ptw_validation") or state.get("ptw_validation")
        inv_as_of = state.get("inventory_as_of") or state.get("inv_as_of")
        period = inv_as_of.strftime("%b %Y") if inv_as_of else "Current Period"

        with st.container(key="review_changes_wrapper"):
            page_header(f"Review Changes · {period}", "Verify differences before committing to active records")
    else:
        # Published mode - reads selected period from session state
        period = state.get("selected_period")
        if not period:
            period = get_latest_period(engine) or "Oct 2026"

        col_h, col_gen = st.columns([4, 1])
        with col_h:
            page_header(f"Monthly Changes · {period}", "Reconciliation and variance report")
        with col_gen:
            st.write("")  # vertical alignment
            if st.button("Generate report", key="mc_generate_report_shortcut", type="primary", use_container_width=True):
                state.set("reports_period", period)
                try:
                    st.switch_page("views/reports.py")
                except Exception:
                    # In test runner or non-standard page route fallback
                    state.set("selected_page", "Reports")
                    st.rerun()

    # Fetch snapshot uploads for this period
    with engine.connect() as conn:
        inv_upload = conn.execute(
            text(
                """
                SELECT id, as_of_date, version, is_baseline
                FROM uploads
                WHERE dataset = 'inventory' AND period = :p AND status IN ('active', 'staged')
                ORDER BY version DESC, id DESC LIMIT 1
                """
            ),
            {"p": period},
        ).fetchone()

        ptw_upload = conn.execute(
            text(
                """
                SELECT id, as_of_date
                FROM uploads
                WHERE dataset = 'ptw' AND period = :p AND status IN ('active', 'staged')
                ORDER BY version DESC, id DESC LIMIT 1
                """
            ),
            {"p": period},
        ).fetchone()

    if not inv_upload:
        empty_state(
            icon="inventory",
            message=f"No inventory snapshot available for {period}.",
            button_text="Go to Upload",
            button_key="mc_empty_upload",
            on_click=lambda: state.set("flow_stage", "upload"),
        )
        return

    inv_id, as_of_date_str, inv_ver, is_base = inv_upload
    ptw_id = ptw_upload[0] if ptw_upload else None

    # Find baseline snapshot upload id
    with engine.connect() as conn:
        base_row = conn.execute(
            text(
                """
                SELECT id, period, as_of_date
                FROM uploads
                WHERE dataset = 'inventory' AND status = 'active' AND as_of_date < :aod
                ORDER BY as_of_date DESC LIMIT 1
                """
            ),
            {"aod": as_of_date_str},
        ).fetchone()

    base_id = base_row[0] if base_row else None
    base_period = base_row[1] if base_row else "Initial"
    base_as_of = base_row[2] if base_row else "1970-01-01"

    # Compute inventory diff
    diff_data = compute_inventory_diff(engine, inv_id, base_id, as_of_date_str)

    # Compute PTW match & gap
    if ptw_id:
        match_ptw_lines_to_inventory(engine, ptw_id, inv_id)
        gap_data = compute_store_vs_ptw_gap(engine, inv_id, ptw_id, base_as_of, as_of_date_str)
    else:
        gap_data = {"gap_table": [], "total_store_val": 0.0, "total_ptw_val": 0.0, "total_gap_val": 0.0, "confirmed_gap_val": 0.0, "probable_gap_val": 0.0, "unmatched_ptw_count": 0}

    # Status chips banner
    st.markdown(
        f"""
        <div style="display: flex; gap: 12px; align-items: center; margin-bottom: 20px;">
            <span style="font-size: 13px; color: {COLOR_TEXT_MUTED}; font-weight: 600;">{period} vs {base_period}</span>
            <span style="color: {COLOR_CARD_BORDER};">&bull;</span>
            <span style="font-size: 12px; color: {COLOR_TEXT};">Window: {base_as_of} to {as_of_date_str}</span>
            <span style="color: {COLOR_CARD_BORDER};">&bull;</span>
            <span style="background: rgba(46,213,115,0.18); color: {ACCENT_GREEN}; border: 1px solid {ACCENT_GREEN}; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 600;">
                {"Reconciled" if diff_data["is_reconciled"] else f"Not Reconciled (Rs {diff_data['reconciliation_gap']:,.2f})"}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # KPI ROW 1: Inventory financial totals
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        kpi_card("Opening value", format_currency_inr(diff_data["opening_val"]), accent_color=ACCENT_BLUE)
    with col2:
        kpi_card("Added (stock)", format_currency_inr(diff_data["added_val"]), accent_color=ACCENT_GREEN)
    with col3:
        kpi_card("Reduced (stock)", format_currency_inr(diff_data["reduced_val"]), accent_color=ACCENT_CORAL)
    with col4:
        kpi_card("Revaluation", format_currency_inr(diff_data["revaluation_val"]), accent_color=ACCENT_AMBER)
    with col5:
        kpi_card("Closing value", format_currency_inr(diff_data["closing_val"]), accent_color=ACCENT_CYAN)

    st.markdown('<div style="margin-top: 14px;"></div>', unsafe_allow_html=True)

    # KPI ROW 2: PTW & Gap totals
    kpi_p1, kpi_p2, kpi_p3, kpi_p4 = st.columns(4)
    with kpi_p1:
        net_chg = diff_data["net_change_val"]
        pct_chg = (float(net_chg) / float(diff_data["opening_val"]) * 100.0) if diff_data["opening_val"] > 0 else 0.0
        kpi_card("Net change", format_currency_inr(net_chg), delta=f"{pct_chg:+.1f}% vs opening", accent_color=ACCENT_PURPLE)
    with kpi_p2:
        kpi_card("PTW recorded use", format_currency_inr(gap_data["total_ptw_val"]), accent_color=ACCENT_CYAN)
    with kpi_p3:
        kpi_card("Store vs PTW gap", format_currency_inr(gap_data["total_gap_val"]), accent_color=ACCENT_AMBER if gap_data["total_gap_val"] != 0 else ACCENT_GREEN)
    with kpi_p4:
        kpi_card("Unmatched permits", f"{gap_data['unmatched_ptw_count']}", accent_color=ACCENT_CORAL if gap_data["unmatched_ptw_count"] > 0 else ACCENT_GREEN)

    st.markdown('<div style="margin-top: 24px;"></div>', unsafe_allow_html=True)

    # CHARTS ROW
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        # Waterfall Chart
        fig_wf = go.Figure(go.Waterfall(
            orientation="v",
            measure=["absolute", "relative", "relative", "relative", "total"],
            x=["Opening", "Added", "Reduced", "Revaluation", "Closing"],
            y=[
                float(diff_data["opening_val"]),
                float(diff_data["added_val"]),
                -float(diff_data["reduced_val"]),
                float(diff_data["revaluation_val"]),
                float(diff_data["closing_val"]),
            ],
            connector={"line": {"color": "rgba(255,255,255,0.2)"}},
            decreasing={"marker": {"color": ACCENT_CORAL}},
            increasing={"marker": {"color": ACCENT_GREEN}},
            totals={"marker": {"color": ACCENT_CYAN}},
        ))
        apply_chart_theme(fig_wf)
        fig_wf.update_layout(height=280, margin=dict(l=20, r=20, t=30, b=20))
        chart_card("Inventory reconciliation waterfall", fig_wf)

    with chart_col2:
        # Clustered Bar: Store Reduction vs PTW Usage for Top 5 Gap items
        gap_items = sorted(gap_data["gap_table"], key=lambda x: abs(x["difference_value"]), reverse=True)[:5]
        if gap_items:
            x_labels = [g["item_key"] for g in gap_items]
            store_bars = [g["store_reduced_qty"] for g in gap_items]
            ptw_bars = [g["ptw_qty"] for g in gap_items]

            fig_gap = go.Figure(data=[
                go.Bar(name="Store Reduced", x=x_labels, y=store_bars, marker_color=ACCENT_CORAL),
                go.Bar(name="PTW Used", x=x_labels, y=ptw_bars, marker_color=ACCENT_GREEN),
            ])
            fig_gap.update_layout(barmode="group", height=280, margin=dict(l=20, r=20, t=30, b=20))
            apply_chart_theme(fig_gap)
            chart_card("Top gap items: Store vs PTW quantity", fig_gap)
        else:
            fig_dummy = go.Figure()
            apply_chart_theme(fig_dummy)
            chart_card("Top gap items", fig_dummy)

    st.markdown('<div style="margin-top: 24px;"></div>', unsafe_allow_html=True)

    # TABS SECTION WITH INTERACTIVE DATA TABLES
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Added items",
        "Reduced items",
        "Store vs PTW gap",
        "Price & Transfers",
        "Needs review",
        "Export report",
    ])

    diff_df = pd.DataFrame(diff_data["diff_rows"])

    with tab1:
        if not diff_df.empty:
            added_df = diff_df[diff_df["change_type"].isin(["New item", "Qty increase"])]
            if not added_df.empty:
                st.dataframe(
                    added_df[["item_key", "change_type", "qty_old", "qty_new", "qty_delta", "qty_effect", "flags"]],
                    use_container_width=True,
                    height=240,
                )
            else:
                st.caption("No item additions in this window.")
        else:
            st.caption("No inventory diff records.")

    with tab2:
        if not diff_df.empty:
            reduced_df = diff_df[diff_df["change_type"].isin(["Removed item", "Qty decrease"])]
            if not reduced_df.empty:
                st.dataframe(
                    reduced_df[["item_key", "change_type", "qty_old", "qty_new", "qty_delta", "qty_effect", "flags"]],
                    use_container_width=True,
                    height=240,
                )
            else:
                st.caption("No item reductions in this window.")
        else:
            st.caption("No inventory diff records.")

    with tab3:
        if gap_data["gap_table"]:
            g_df = pd.DataFrame(gap_data["gap_table"])
            st.dataframe(
                g_df[["item_key", "store_reduced_qty", "ptw_qty", "difference_qty", "difference_value", "status", "review_status"]],
                use_container_width=True,
                height=260,
            )
        else:
            st.caption("No store vs PTW gap records.")

    with tab4:
        if not diff_df.empty:
            pt_df = diff_df[diff_df["change_type"].isin(["Price change only", "Transfer"])]
            if not pt_df.empty:
                st.dataframe(
                    pt_df[["item_key", "change_type", "price_old", "price_new", "price_effect", "flags"]],
                    use_container_width=True,
                    height=240,
                )
            else:
                st.caption("No price changes or transfers.")
        else:
            st.caption("No diff records.")

    with tab5:
        if not diff_df.empty:
            review_df = diff_df[diff_df["flags"].notna()]
            if not review_df.empty:
                st.dataframe(
                    review_df[["item_key", "change_type", "qty_delta", "qty_effect", "flags"]],
                    use_container_width=True,
                    height=240,
                )
            else:
                st.caption("No items flagged for special review.")
        else:
            st.caption("No flagged items.")

    with tab6:
        # Excel Export
        st.caption("Export comprehensive monthly reconciliation report:")
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            # Summary sheet
            sum_df = pd.DataFrame([
                {"Metric": "Period", "Value": period},
                {"Metric": "Opening Value (Rs)", "Value": float(diff_data["opening_val"])},
                {"Metric": "Added (Rs)", "Value": float(diff_data["added_val"])},
                {"Metric": "Reduced (Rs)", "Value": float(diff_data["reduced_val"])},
                {"Metric": "Price Revaluation (Rs)", "Value": float(diff_data["revaluation_val"])},
                {"Metric": "Closing Value (Rs)", "Value": float(diff_data["closing_val"])},
                {"Metric": "Reconciled", "Value": str(diff_data["is_reconciled"])},
            ])
            sum_df.to_excel(writer, sheet_name="Summary", index=False)
            if not diff_df.empty:
                diff_df.to_excel(writer, sheet_name="Inventory_Diff", index=False)
            if gap_data["gap_table"]:
                pd.DataFrame(gap_data["gap_table"]).to_excel(writer, sheet_name="Store_vs_PTW_Gap", index=False)

        st.download_button(
            label="Download Excel report",
            data=output.getvalue(),
            file_name=f"Recon_Report_{period.replace(' ', '_')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )

    # REVIEW MODE PUBLICATION CONTROLS
    if mode == "review":
        st.markdown('<div style="margin-top: 36px; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 24px;"></div>', unsafe_allow_html=True)
        col_c1, col_c2, col_c3 = st.columns([1, 1, 1])

        with col_c2:
            ack = st.checkbox("I reviewed the differences and warnings", key="chk_review_ack")
            btn_pub1, btn_pub2 = st.columns(2)
            with btn_pub1:
                if st.button("Confirm & publish", type="primary", disabled=not ack, use_container_width=True):
                    ok, msg = commit_and_publish(engine, inv_id, user=user.get("username", "admin"))
                    if ptw_id:
                        commit_and_publish(engine, ptw_id, user=user.get("username", "admin"))
                    if ok:
                        state.set("flow_stage", "main")
                        state.set("selected_period", period)
                        st.rerun()
                    else:
                        st.error(msg)
            with btn_pub2:
                if st.button("Discard staged", type="secondary", use_container_width=True):
                    discard_staged_upload(engine, inv_id, user=user.get("username", "admin"))
                    if ptw_id:
                        discard_staged_upload(engine, ptw_id, user=user.get("username", "admin"))
                    state.set("flow_stage", "upload")
                    st.rerun()


if __name__ == "__main__":
    render_monthly_changes(mode="published")

