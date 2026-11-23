"""
Savings Tracker View for Recon Dashboard.
Presents cost avoidance summary, open opportunities, savings log,
and admin governance controls.
"""

from datetime import date, datetime
import html
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
    safe_render,
    status_chip,
)
from app.ui.theme import (
    ACCENT_AMBER,
    ACCENT_BLUE,
    ACCENT_CORAL,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_PURPLE,
    COLOR_BG,
    COLOR_CARD,
    COLOR_CARD_BORDER,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
)
from core.audit import log_audit_event
from core.auth import is_admin
from core.data_access import get_available_periods, get_latest_period
from core.database import get_engine
from core.savings import (
    compute_opportunities_and_excess,
    create_savings_entry,
    dismiss_opportunity,
    edit_savings_entry,
    get_fy_label,
    savings_summary,
    verify_savings_entry,
    void_savings_entry,
)
from core.settings import (
    get_setting,
    get_setting_float,
    get_setting_int,
    set_setting,
)


# ─────────────────────────────────────────────────────────────────────────────
# Dialog Forms (Admin Only)
# ─────────────────────────────────────────────────────────────────────────────


@st.dialog("Log new savings entry")
def dialog_add_savings_entry(engine, default_opp: dict = None):
    """Dialog for manual savings entry or converting an opportunity."""
    user = state.get("user") or {}
    username = user.get("username", "admin")

    st.markdown("<p style='color:#8FA9AE; font-size:0.85rem; margin-bottom:12px;'>Record an avoided order, reduced procurement, or redeployed inventory.</p>", unsafe_allow_html=True)

    today = date.today()
    c1, c2 = st.columns(2)
    with c1:
        action_date = st.date_input("Action date", value=today, max_value=today)
    with c2:
        entry_types = [
            ("order_cancelled", "Order cancelled"),
            ("order_reduced", "Order reduced"),
            ("existing_stock_used", "Existing stock used"),
            ("returned_to_vendor", "Returned to vendor"),
            ("other", "Other cost avoidance"),
        ]
        entry_type = st.selectbox(
            "Avoidance type",
            options=[t[0] for t in entry_types],
            format_func=lambda x: dict(entry_types).get(x, x),
        )

    col_item1, col_item2 = st.columns(2)
    with col_item1:
        def_item = default_opp.get("item_key", "") if default_opp else ""
        item_key = st.text_input("Item key (optional if non-catalog)", value=def_item)
    with col_item2:
        def_ref = ""
        reference = st.text_input("Reference (PR / PO / Work order #)", value=def_ref)

    def_desc = default_opp.get("description", "") if default_opp else ""
    description = st.text_input("Description", value=def_desc)

    col_q1, col_q2, col_p = st.columns(3)
    def_req = float(default_opp.get("added_qty", 1.0)) if default_opp else 1.0
    def_avoid = float(default_opp.get("avoidable_qty", 1.0)) if default_opp else 1.0
    def_price = float(default_opp.get("unit_price", 0.0)) if default_opp else 0.0

    with col_q1:
        requested_qty = st.number_input("Requested qty", min_value=0.01, value=max(0.01, def_req), step=1.0)
    with col_q2:
        avoided_qty = st.number_input("Avoided qty", min_value=0.01, max_value=float(requested_qty), value=min(float(requested_qty), max(0.01, def_avoid)), step=1.0)
    with col_p:
        unit_price = st.number_input("Unit price (Rs)", min_value=0.0, value=def_price, step=10.0)

    # Live calculated savings
    calc_savings_rs = avoided_qty * unit_price
    st.markdown(
        f"""
        <div style="background: rgba(46,213,115,0.08); border: 1px solid rgba(46,213,115,0.25); border-radius: 8px; padding: 10px 14px; margin: 10px 0;">
            <span style="color:#8FA9AE; font-size:0.8rem; text-transform:uppercase; letter-spacing:0.05em;">Calculated Cost Avoidance:</span>
            <span style="color:{ACCENT_GREEN}; font-size:1.15rem; font-weight:700; margin-left:8px;">{format_currency_inr(calc_savings_rs)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    price_override_reason = None
    if unit_price > 0 and def_price > 0 and abs(unit_price - def_price) / def_price > 0.20:
        price_override_reason = st.text_input("Price override justification (differs > 20% from catalog)")

    col_d, col_eq = st.columns(2)
    with col_d:
        department = st.text_input("Department", value=default_opp.get("department", "") if default_opp else "")
    with col_eq:
        equipment_tag = st.text_input("Equipment tag", value=default_opp.get("equipment_tag", "") if default_opp else "")

    requested_by = st.text_input("Requested by (Engineer / Department contact)")
    note = st.text_area("Audit notes / justification", height=70)

    opp_id = default_opp.get("id") if default_opp else None

    col_sub, col_can = st.columns([1, 1])
    with col_sub:
        if st.button("Confirm and log saving", type="primary", use_container_width=True):
            ok, msg, new_id = create_savings_entry(
                engine=engine,
                action_date=action_date,
                entry_type=entry_type,
                requested_qty=requested_qty,
                avoided_qty=avoided_qty,
                unit_price=unit_price,
                created_by=username,
                item_key=item_key.strip() if item_key else None,
                description=description.strip() if description else None,
                department=department.strip() if department else None,
                equipment_tag=equipment_tag.strip() if equipment_tag else None,
                reference=reference.strip() if reference else None,
                requested_by=requested_by.strip() if requested_by else None,
                note=note.strip() if note else None,
                opportunity_id=opp_id,
                price_override_reason=price_override_reason,
            )
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)


@st.dialog("Verify savings entry")
def dialog_verify_entry(engine, entry_id: int):
    """Dialog for verifying an actioned entry."""
    user = state.get("user") or {}
    username = user.get("username", "admin")

    st.markdown(f"<p style='color:#E8F1F2; font-weight:600;'>Verify savings entry #{entry_id}</p>", unsafe_allow_html=True)
    note = st.text_area("Verification note (mandatory)", placeholder="Confirm PO was cancelled in ERP / store verified...")

    if st.button("Confirm verification", type="primary", use_container_width=True):
        ok, msg = verify_savings_entry(engine, entry_id, user=username, note=note)
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)


@st.dialog("Void savings entry")
def dialog_void_entry(engine, entry_id: int):
    """Dialog for voiding an entry."""
    user = state.get("user") or {}
    username = user.get("username", "admin")

    st.markdown(f"<p style='color:{ACCENT_CORAL}; font-weight:600;'>Void savings entry #{entry_id}</p>", unsafe_allow_html=True)
    st.caption("Voided entries remain visible in the audit trail but are excluded from all reporting figures.")
    reason = st.text_area("Reason for voiding (mandatory)", placeholder="Order subsequently approved by plant head...")

    if st.button("Confirm void", type="primary", use_container_width=True):
        ok, msg = void_savings_entry(engine, entry_id, user=username, reason=reason)
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)


@st.dialog("Dismiss opportunity")
def dialog_dismiss_opp(engine, opp_id: int):
    """Dialog for dismissing an open opportunity."""
    user = state.get("user") or {}
    username = user.get("username", "admin")

    st.markdown(f"<p style='color:#E8F1F2; font-weight:600;'>Dismiss opportunity #{opp_id}</p>", unsafe_allow_html=True)
    reason = st.text_area("Reason for dismissal (mandatory)", placeholder="Emergency spare for overhaul; stock increase is intentional...")

    if st.button("Confirm dismissal", type="primary", use_container_width=True):
        ok, msg = dismiss_opportunity(engine, opp_id, user=username, reason=reason)
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Main View Rendering
# ─────────────────────────────────────────────────────────────────────────────


def render_savings():
    """Render Savings Tracker View."""
    inject_css()
    engine = get_engine()

    page_header("Savings tracker", "Cost avoidance, procurement opportunities & excess stock tracking")

    # Global Period & Data Availability
    avail_periods = get_available_periods(engine)
    if not avail_periods:
        empty_state(
            "inventory",
            "No published inventory data available yet. Upload datasets to begin tracking savings and opportunities.",
        )
        return

    sel_period = state.get("selected_period")
    if not sel_period or sel_period not in avail_periods:
        sel_period = avail_periods[0]
        state.set("selected_period", sel_period)

    # Check for baseline period
    is_baseline = len(avail_periods) == 1
    if is_baseline:
        st.markdown(
            f"""
            <div style="background: rgba(31,95,168,0.12); border: 1px solid {ACCENT_BLUE}; border-radius: 10px; padding: 12px 18px; margin-bottom: 18px;">
                <span style="color: {ACCENT_BLUE}; font-weight: 600;">Baseline period ({sel_period}):</span>
                <span style="color: {COLOR_TEXT}; margin-left: 6px;">Diff-based opportunities require at least two periods of stock data. Opportunities will be generated with subsequent uploads.</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Load unified metrics
    summary_fy = savings_summary(engine, scope="FY", period=sel_period)
    summary_p = savings_summary(engine, scope="period", period=sel_period)

    user_info = state.get("user") or {}
    admin_mode = is_admin(user_info)

    # Tabs
    tab_summary, tab_opps, tab_log = st.tabs(["Summary", "Opportunities", "Savings log"])

    # ── TAB 1: SUMMARY ────────────────────────────────────────────────────────
    with tab_summary:
        # Row 1: KPI Cards
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            kpi_card(
                "Verified this FY",
                format_currency_inr(summary_fy["verified_rs"]),
                delta=f"{summary_fy['fy_label']}",
                accent_color=ACCENT_GREEN,
            )
        with c2:
            kpi_card(
                "Actioned (period)",
                format_currency_inr(summary_p["actioned_rs"]),
                delta=f"In {sel_period}",
                accent_color=ACCENT_AMBER,
            )
        with c3:
            opp_counts = summary_p["opportunities"]
            kpi_card(
                "Opportunities",
                format_currency_inr(opp_counts["total_rs"]),
                delta=f"H:{opp_counts['high']['count']} M:{opp_counts['medium']['count']} L:{opp_counts['low']['count']}",
                accent_color=ACCENT_CYAN,
            )
        with c4:
            exc_delta = summary_p["excess_stock_delta_rs"]
            delta_str = f"{format_currency_inr(abs(exc_delta))} {'more' if exc_delta >= 0 else 'less'}" if exc_delta != 0 else "Unchanged"
            kpi_card(
                "Excess stock",
                format_currency_inr(summary_p["excess_stock_rs"]),
                delta=delta_str,
                accent_color=ACCENT_CORAL,
            )
        with c5:
            kpi_card(
                "Orders reviewed",
                f"{summary_fy['orders_reviewed_count']}",
                delta=f"{summary_fy['orders_above_threshold_count']} ≥ Rs 2L",
                accent_color=ACCENT_PURPLE,
            )

        st.markdown("<div style='height: 18px;'></div>", unsafe_allow_html=True)

        # Row 2: Monthly Stacked Bar + Savings by Type
        col_bar, col_type = st.columns([1.5, 1])
        with col_bar:
            m_trend = summary_fy.get("monthly_trend", [])
            if m_trend:
                df_trend = pd.DataFrame(m_trend)
                fig_monthly = go.Figure()
                fig_monthly.add_trace(
                    go.Bar(
                        x=df_trend["period"],
                        y=df_trend["verified_rs"],
                        name="Verified",
                        marker_color=ACCENT_GREEN,
                    )
                )
                fig_monthly.add_trace(
                    go.Bar(
                        x=df_trend["period"],
                        y=df_trend["actioned_rs"],
                        name="Actioned",
                        marker_color=ACCENT_AMBER,
                    )
                )
                fig_monthly.add_trace(
                    go.Scatter(
                        x=df_trend["period"],
                        y=df_trend["cumulative_fy_rs"],
                        name="Cumulative FY",
                        mode="lines+markers",
                        line=dict(color=ACCENT_CYAN, width=2),
                        marker=dict(size=6),
                    )
                )
                fig_monthly.update_layout(
                    barmode="stack",
                    height=280,
                    margin=dict(l=10, r=10, t=10, b=20),
                    yaxis_title="Amount (Rs)",
                    xaxis_title="",
                )
                chart_card(f"Monthly savings trend ({summary_fy['fy_label']})", fig_monthly, key="chart_savings_monthly")
            else:
                fig_empty = go.Figure()
                fig_empty.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=20))
                chart_card(f"Monthly savings trend ({summary_fy['fy_label']})", fig_empty, key="chart_savings_monthly_empty")

        with col_type:
            types_dict = summary_fy.get("savings_by_type", {})
            if types_dict and sum(types_dict.values()) > 0:
                t_df = pd.DataFrame([{"type": k.replace("_", " ").title(), "amount": v} for k, v in types_dict.items() if v > 0])
                fig_type = px.pie(
                    t_df,
                    names="type",
                    values="amount",
                    hole=0.55,
                    color_discrete_sequence=[ACCENT_GREEN, ACCENT_CYAN, ACCENT_PURPLE, ACCENT_AMBER, ACCENT_BLUE],
                )
                fig_type.update_layout(
                    height=280,
                    margin=dict(l=10, r=10, t=10, b=20),
                    legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
                )
                chart_card("Savings by avoidance type", fig_type, key="chart_savings_by_type")
            else:
                fig_empty = go.Figure()
                fig_empty.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=20))
                chart_card("Savings by avoidance type", fig_empty, key="chart_savings_by_type_empty")

        st.markdown("<div style='height: 18px;'></div>", unsafe_allow_html=True)

        # Row 3: Department / Equipment breakdown & Top Opportunities
        col_dept, col_opp = st.columns([1, 1.3])
        with col_dept:
            breakdown_choice = st.segmented_control(
                "Breakdown",
                options=["Department", "Equipment"],
                default="Department",
                label_visibility="collapsed",
                key="savings_breakdown_toggle",
            )
            data_dict = summary_fy.get("savings_by_department" if breakdown_choice == "Department" else "savings_by_equipment", {})
            if data_dict and sum(data_dict.values()) > 0:
                d_df = pd.DataFrame([{"group": k, "amount": v} for k, v in data_dict.items() if v > 0]).sort_values("amount", ascending=True)
                fig_group = px.bar(d_df, x="amount", y="group", orientation="h")
                fig_group.update_traces(marker_color=ACCENT_BLUE)
                fig_group.update_layout(
                    height=260,
                    margin=dict(l=10, r=10, t=10, b=20),
                    xaxis_title="Amount (Rs)",
                    yaxis_title="",
                )
                chart_card(f"Savings by {breakdown_choice.lower()}", fig_group, key="chart_savings_by_group")
            else:
                fig_empty = go.Figure()
                fig_empty.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=20))
                chart_card(f"Savings by {breakdown_choice.lower()}", fig_empty, key="chart_savings_by_group_empty")

        with col_opp:
            opp_df = summary_p.get("opportunities_df")
            if opp_df is not None and not opp_df.empty:
                open_o = opp_df[opp_df["status"] == "open"].copy()
                if not open_o.empty:
                    open_o["rs"] = open_o["avoidable_value_paise"] / 100.0
                    top_opps = open_o.sort_values("rs", ascending=False).head(8)
                    fig_opp = px.bar(top_opps, x="rs", y="item_key", orientation="h", text="rs")
                    fig_opp.update_traces(
                        marker_color=ACCENT_CYAN,
                        texttemplate="Rs %{text:,.0f}",
                        textposition="outside",
                    )
                    fig_opp.update_layout(
                        height=260,
                        margin=dict(l=10, r=10, t=10, b=20),
                        xaxis_title="Avoidable Value (Rs)",
                        yaxis_title="",
                    )
                    chart_card("Top open opportunities", fig_opp, key="chart_top_opportunities")
                else:
                    fig_empty = go.Figure()
                    fig_empty.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=20))
                    chart_card("Top open opportunities", fig_empty, key="chart_top_opportunities_empty")
            else:
                fig_empty = go.Figure()
                fig_empty.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=20))
                chart_card("Top open opportunities", fig_empty, key="chart_top_opportunities_empty")

    # ── TAB 2: OPPORTUNITIES ─────────────────────────────────────────────────
    with tab_opps:
        opp_df = summary_p.get("opportunities_df")
        if opp_df is None or opp_df.empty:
            empty_state("savings", f"No opportunities detected for {sel_period}. All additions were within target cover or lack history.")
        else:
            col_f1, col_f2, col_f3 = st.columns([1, 1, 2])
            with col_f1:
                conf_filter = st.selectbox("Confidence", ["All", "High", "Medium", "Low"], key="opp_conf_filter")
            with col_f2:
                stat_filter = st.selectbox("Status", ["Open", "Converted", "Dismissed", "All"], key="opp_stat_filter")
            with col_f3:
                opp_search = st.text_input("Search item or tag", key="opp_search_box")

            filtered_opp = opp_df.copy()
            if conf_filter != "All":
                filtered_opp = filtered_opp[filtered_opp["confidence"].str.lower() == conf_filter.lower()]
            if stat_filter != "All":
                filtered_opp = filtered_opp[filtered_opp["status"].str.lower() == stat_filter.lower()]
            if opp_search:
                s_lower = opp_search.lower()
                filtered_opp = filtered_opp[filtered_opp["item_key"].str.lower().str.contains(s_lower, na=False)]

            st.caption(f"Showing {len(filtered_opp)} opportunities for {sel_period}")

            # Display table
            disp_opp = filtered_opp.copy()
            disp_opp["avoidable_rs"] = disp_opp["avoidable_value_paise"] / 100.0

            cols_show = [
                "id",
                "item_key",
                "added_qty",
                "closing_qty",
                "target_qty",
                "months_cover_after",
                "avoidable_qty",
                "unit_price",
                "avoidable_rs",
                "confidence",
                "flags",
                "status",
            ]
            st.dataframe(
                disp_opp[cols_show],
                column_config={
                    "id": "ID",
                    "item_key": "Item Key",
                    "added_qty": st.column_config.NumberColumn("Added Qty", format="%.1f"),
                    "closing_qty": st.column_config.NumberColumn("Stock After", format="%.1f"),
                    "target_qty": st.column_config.NumberColumn("Target Qty", format="%.1f"),
                    "months_cover_after": st.column_config.NumberColumn("Cover After", format="%.1f mos"),
                    "avoidable_qty": st.column_config.NumberColumn("Avoidable Qty", format="%.1f"),
                    "unit_price": st.column_config.NumberColumn("Unit Price", format="Rs %.2f"),
                    "avoidable_rs": st.column_config.NumberColumn("Avoidable Value", format="Rs %d"),
                    "confidence": "Confidence",
                    "flags": "Flags",
                    "status": "Status",
                },
                width="stretch",
                hide_index=True,
            )

            # Action bar for open opportunities
            if admin_mode:
                open_items = filtered_opp[filtered_opp["status"] == "open"]
                if not open_items.empty:
                    st.markdown("<p style='font-size:0.9rem; font-weight:600; color:#E8F1F2; margin-top:14px;'>Action open opportunity</p>", unsafe_allow_html=True)
                    col_act_sel, col_btn_log, col_btn_dis = st.columns([2, 1, 1])
                    with col_act_sel:
                        selected_opp_id = st.selectbox(
                            "Select opportunity to action",
                            options=open_items["id"].tolist(),
                            format_func=lambda x: f"#{x} - {open_items[open_items['id'] == x]['item_key'].iloc[0]} (Rs {open_items[open_items['id'] == x]['avoidable_value_paise'].iloc[0]/100:,.0f})",
                            label_visibility="collapsed",
                        )
                    with col_btn_log:
                        if st.button("Log saving", type="primary", use_container_width=True):
                            opp_row = open_items[open_items["id"] == selected_opp_id].iloc[0].to_dict()
                            dialog_add_savings_entry(engine, default_opp=opp_row)
                    with col_btn_dis:
                        if st.button("Dismiss", use_container_width=True):
                            dialog_dismiss_opp(engine, selected_opp_id)

    # ── TAB 3: SAVINGS LOG ───────────────────────────────────────────────────
    with tab_log:
        col_act1, col_act2, col_act3 = st.columns([1, 1, 1])
        with col_act1:
            if admin_mode:
                if st.button("+ Add manual entry", key="btn_add_savings_entry", type="primary", use_container_width=True):
                    dialog_add_savings_entry(engine)
        with col_act3:
            entries_all = summary_p.get("entries_df")
            if entries_all is not None and not entries_all.empty:
                csv_bytes = entries_all.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "Download CSV",
                    data=csv_bytes,
                    file_name=f"savings_log_{sel_period.replace(' ', '_')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

        # Filters
        c_f1, c_f2, c_f3 = st.columns([1, 1, 2])
        with c_f1:
            filter_status = st.selectbox("Status", ["All", "Actioned", "Verified", "Voided"], key="log_stat_filter")
        with c_f2:
            filter_check = st.selectbox("Data check", ["All", "Pending", "Supported", "Contradicted", "Not applicable"], key="log_chk_filter")
        with c_f3:
            search_query = st.text_input("Search reference, item or description", key="log_search_query")

        entries_df = summary_p.get("entries_df")
        if entries_df is None or entries_df.empty:
            empty_state("savings", "No savings entries logged yet. Click '+ Add manual entry' to record an avoided order.")
        else:
            log_filtered = entries_df.copy()
            if filter_status != "All":
                log_filtered = log_filtered[log_filtered["status"].str.lower() == filter_status.lower()]
            if filter_check != "All":
                val = filter_check.lower().replace(" ", "_")
                log_filtered = log_filtered[log_filtered["data_check"].str.lower() == val]
            if search_query:
                sq = search_query.lower()
                log_filtered = log_filtered[
                    log_filtered["reference"].str.lower().str.contains(sq, na=False)
                    | log_filtered["item_key"].str.lower().str.contains(sq, na=False)
                    | log_filtered["description"].str.lower().str.contains(sq, na=False)
                ]

            st.caption(f"Displaying {len(log_filtered)} logged savings entries")

            log_display = log_filtered.copy()
            log_display["amount_rs"] = log_display["amount_paise"] / 100.0

            cols_table = [
                "id",
                "period",
                "action_date",
                "reference",
                "item_key",
                "description",
                "type",
                "avoided_qty",
                "unit_price",
                "amount_rs",
                "status",
                "data_check",
                "created_by",
            ]
            st.dataframe(
                log_display[cols_table],
                column_config={
                    "id": "ID",
                    "period": "Period",
                    "action_date": "Action Date",
                    "reference": "Reference",
                    "item_key": "Item Key",
                    "description": "Description",
                    "type": "Type",
                    "avoided_qty": st.column_config.NumberColumn("Avoided Qty", format="%.1f"),
                    "unit_price": st.column_config.NumberColumn("Unit Price", format="Rs %.2f"),
                    "amount_rs": st.column_config.NumberColumn("Avoided Value", format="Rs %d"),
                    "status": "Status",
                    "data_check": "Data Check",
                    "created_by": "Created By",
                },
                width="stretch",
                hide_index=True,
            )

            # Row Action Controls (Admin Only)
            if admin_mode and not log_filtered.empty:
                st.markdown("<p style='font-size:0.9rem; font-weight:600; color:#E8F1F2; margin-top:14px;'>Manage entry</p>", unsafe_allow_html=True)
                col_sel, col_v, col_e, col_void = st.columns([1.5, 1, 1, 1])
                with col_sel:
                    entry_choices = log_filtered["id"].tolist()
                    sel_entry_id = st.selectbox(
                        "Select entry",
                        options=entry_choices,
                        format_func=lambda x: f"#{x} - {log_filtered[log_filtered['id'] == x]['description'].iloc[0] or log_filtered[log_filtered['id'] == x]['reference'].iloc[0]} (Rs {log_filtered[log_filtered['id'] == x]['amount_paise'].iloc[0]/100:,.0f})",
                        label_visibility="collapsed",
                    )
                with col_v:
                    entry_stat = log_filtered[log_filtered["id"] == sel_entry_id]["status"].iloc[0]
                    can_verify = entry_stat == "actioned"
                    if st.button("Verify entry", disabled=not can_verify, use_container_width=True):
                        dialog_verify_entry(engine, sel_entry_id)
                with col_e:
                    can_edit = entry_stat == "actioned"
                    if st.button("Edit entry", disabled=not can_edit, use_container_width=True):
                        e_row = log_filtered[log_filtered["id"] == sel_entry_id].iloc[0].to_dict()
                        dialog_add_savings_entry(engine, default_opp=e_row)
                with col_void:
                    can_void = entry_stat != "voided"
                    if st.button("Void entry", disabled=not can_void, use_container_width=True):
                        dialog_void_entry(engine, sel_entry_id)

    # ── HOW SAVINGS ARE COUNTED PANEL ────────────────────────────────────────
    st.markdown("<div style='height: 24px;'></div>", unsafe_allow_html=True)
    with st.expander("How savings are counted (Methodology)"):
        st.markdown(
            """
            - **Opportunity**: Found automatically when additions exceed target cover. Potential procurement to avoid; **not money saved**.
            - **Actioned**: An administrator recorded an avoided order or quantity reduction. Avoided quantity and price are frozen at logging.
            - **Verified**: Confirmed by an authorized reviewer following automated data consistency checks; counted toward audited savings.
            - **Excess stock**: Inventory held above target cover (`months_of_cover × average_consumption`). Shown as exposure and **never added to savings**.
            - **Consumption rate**: Maximum of matched closed PTW usage and store reductions, averaged across published periods with at least 3 months history.
            """,
            unsafe_allow_html=True,
        )

    # ── ADMIN SETTINGS EXPANDER ──────────────────────────────────────────────
    if admin_mode:
        with st.expander("Savings and report settings (Admin)"):
            c_s1, c_s2, c_s3 = st.columns(3)
            with c_s1:
                cur_cover = get_setting_float(engine, "months_of_cover", default=6.0)
                new_cover = st.number_input("Months of cover target", min_value=1.0, max_value=24.0, value=cur_cover, step=0.5)

                cur_min_hist = get_setting_int(engine, "min_history_months", default=3)
                new_min_hist = st.number_input("Min history periods", min_value=1, max_value=12, value=cur_min_hist, step=1)

                cur_win = get_setting_int(engine, "history_window_months", default=12)
                new_win = st.number_input("History window periods", min_value=3, max_value=36, value=cur_win, step=1)

            with c_s2:
                cur_fy = get_setting_int(engine, "fy_start_month", default=4)
                new_fy = st.number_input("FY start month (1=Jan, 4=Apr)", min_value=1, max_value=12, value=cur_fy, step=1)

                cur_thresh = get_setting_float(engine, "plant_head_threshold", default=200000.0)
                new_thresh = st.number_input("Plant head approval threshold (Rs)", min_value=10000.0, value=cur_thresh, step=50000.0)

                cur_mc = get_setting(engine, "maker_checker", default="false").lower() == "true"
                new_mc = st.toggle("Enforce maker-checker verification", value=cur_mc)

            with c_s3:
                cur_plant = get_setting(engine, "plant_name", default="Captive Power Plant")
                new_plant = st.text_input("Plant facility name", value=cur_plant)

                cur_site = get_setting(engine, "site_name", default="")
                new_site = st.text_input("Site / Location name", value=cur_site)

                cur_footer = get_setting(engine, "footer_text", default="Internal use only")
                new_footer = st.text_input("Report footer disclaimer", value=cur_footer)

            if st.button("Save settings", type="primary", key="btn_save_savings_settings"):
                set_setting(engine, "months_of_cover", str(new_cover), updated_by=user_info.get("username", "admin"))
                set_setting(engine, "min_history_months", str(new_min_hist), updated_by=user_info.get("username", "admin"))
                set_setting(engine, "history_window_months", str(new_win), updated_by=user_info.get("username", "admin"))
                set_setting(engine, "fy_start_month", str(new_fy), updated_by=user_info.get("username", "admin"))
                set_setting(engine, "plant_head_threshold", str(new_thresh), updated_by=user_info.get("username", "admin"))
                set_setting(engine, "maker_checker", "true" if new_mc else "false", updated_by=user_info.get("username", "admin"))
                set_setting(engine, "plant_name", new_plant, updated_by=user_info.get("username", "admin"))
                set_setting(engine, "site_name", new_site, updated_by=user_info.get("username", "admin"))
                set_setting(engine, "footer_text", new_footer, updated_by=user_info.get("username", "admin"))

                # Recompute opportunities with new settings
                compute_opportunities_and_excess(engine, sel_period)
                st.success("Settings saved and opportunities recomputed.")
                st.rerun()


if __name__ == "__main__":
    safe_render(render_savings, screen_name="Savings")

