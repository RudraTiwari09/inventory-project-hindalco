"""
Upload Screen for the Recon Dashboard.
Two equal partitions (Inventory: Storage department | Permit to work: Operations),
stacked choice toggles, drag-and-drop upload, automatic mapping,
validation checks, independent partition error boundaries, and gated Proceed button.
"""

from datetime import date, datetime, timezone
from typing import Dict, Any, Optional
import pandas as pd
import streamlit as st

from app import state
from app.ui.components import (
    inject_css,
    page_header,
    format_currency_inr,
)
from app.ui.icons import icon
from app.ui.theme import (
    COLOR_BG,
    COLOR_CARD,
    COLOR_CARD_BORDER,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
    ACCENT_GREEN,
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_CORAL,
)
from core.database import (
    get_engine,
    load_column_mapping,
)
from core.data_access import (
    get_latest_snapshot_info,
    is_snapshot_stale,
)
from core.validation import (
    parse_raw_file,
    suggest_column_mappings,
    compute_file_hash,
    validate_and_normalize,
    INVENTORY_REQUIRED_FIELDS,
    PTW_REQUIRED_FIELDS,
)


def _parse_to_date(val: Any) -> Optional[date]:
    """Safely convert date/datetime/string to a datetime.date object."""
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        try:
            return datetime.strptime(val[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    return None


def render_partition(dataset: str, title: str, dept_label: str, engine):
    """
    Render one upload partition (Inventory or PTW).
    Default mode is None (empty state until user chooses an option).
    Choice buttons are stacked vertically with equal height.
    """
    mode_key = f"{dataset}_mode"
    parsed_key = f"{dataset}_parsed"
    val_key = f"{dataset}_validation"
    mapping_key = f"{dataset}_mapping"
    as_of_key = f"{dataset}_as_of"

    old_info = get_latest_snapshot_info(engine, dataset)
    has_old = old_info is not None

    # Column header in clean sentence case
    st.markdown(
        f"""
        <div style="margin-bottom: 16px;">
            <div style="font-size: 22px; font-weight: 700; color: {COLOR_TEXT}; line-height: 1.2;">
                {title}
            </div>
            <div style="font-size: 13px; color: {COLOR_TEXT_MUTED}; margin-top: 4px;">
                {dept_label}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    curr_mode = state.get(mode_key)  # None, 'new', or 'old'
    is_new = (curr_mode == "new")
    is_old = (curr_mode == "old")

    # Stacked choice buttons (equal height, full width)
    # Top button: Upload new data
    btn_label_new = "✓  Upload new data" if is_new else "Upload new data"
    with st.container(key=f"choice_btn_{dataset}_new_{'active' if is_new else 'idle'}"):
        if st.button(
            btn_label_new,
            key=f"btn_mode_new_{dataset}",
            type="primary" if is_new else "secondary",
            use_container_width=True,
        ):
            state.set(mode_key, "new")
            st.rerun()

    # Bottom button: Continue with old data
    btn_label_old = "✓  Continue with old data" if is_old else "Continue with old data"
    with st.container(key=f"choice_btn_{dataset}_old_{'active' if is_old else 'idle'}"):
        if st.button(
            btn_label_old,
            key=f"btn_mode_old_{dataset}",
            type="primary" if is_old else "secondary",
            disabled=not has_old,
            help="No previous data available" if not has_old else "Reuse existing active snapshot",
            use_container_width=True,
        ):
            state.set(mode_key, "old")
            st.rerun()

    st.markdown('<div style="margin-top: 18px;"></div>', unsafe_allow_html=True)

    # 1. EMPTY STATE (when curr_mode is None)
    if curr_mode is None:
        st.markdown(
            f"""
            <div style="background: rgba(255,255,255,0.02); border: 1px dashed {COLOR_CARD_BORDER}; border-radius: 12px; padding: 44px 20px; text-align: center;">
                <div style="font-size: 14px; color: {COLOR_TEXT_MUTED};">
                    Choose an option above to upload new files or continue with previous data.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    # 2. CONTINUE WITH OLD DATA
    if curr_mode == "old":
        if old_info:
            is_stale = is_snapshot_stale(engine, old_info["as_of_date"])
            stale_badge = (
                f'<span style="background: rgba(246,196,83,0.18); color: {ACCENT_AMBER}; border: 1px solid {ACCENT_AMBER}; padding: 2px 8px; border-radius: 12px; font-size: 11px; margin-left: 8px; font-weight: 600;">Stale</span>'
                if is_stale
                else ""
            )

            metric_label = "Total stock value" if dataset == "inventory" else "Total permits"
            metric_val = (
                format_currency_inr(old_info.get("total_value", 0))
                if dataset == "inventory"
                else f"{old_info.get('row_count', 0):,} permits"
            )

            st.markdown(
                f"""
                <div style="background: {COLOR_CARD}; border: 1px solid {COLOR_CARD_BORDER}; border-radius: 12px; padding: 20px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                        <span style="font-size: 16px; font-weight: 600; color: {COLOR_TEXT};">
                            Period: {old_info['period']} {stale_badge}
                        </span>
                        <span style="font-size: 12px; color: {COLOR_TEXT_MUTED};">
                            As of: {old_info['as_of_date']}
                        </span>
                    </div>
                    <div style="display: flex; gap: 24px; margin-bottom: 14px;">
                        <div>
                            <div style="font-size: 11px; text-transform: uppercase; color: {COLOR_TEXT_MUTED}; font-weight: 600;">Rows</div>
                            <div style="font-size: 16px; font-weight: 600; color: {COLOR_TEXT};">{old_info['row_count']:,}</div>
                        </div>
                        <div>
                            <div style="font-size: 11px; text-transform: uppercase; color: {COLOR_TEXT_MUTED}; font-weight: 600;">{metric_label}</div>
                            <div style="font-size: 16px; font-weight: 600; color: {ACCENT_GREEN};">{metric_val}</div>
                        </div>
                    </div>
                    <div style="font-size: 12px; color: {COLOR_TEXT_MUTED}; border-top: 1px solid rgba(255,255,255,0.06); padding-top: 10px;">
                        Uploaded by <strong>{old_info['uploaded_by']}</strong> on {old_info['uploaded_at'][:10]}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.warning("No previous snapshot found.")
        return

    # 3. UPLOAD NEW DATA
    uploaded_file = st.file_uploader(
        f"Choose {title} file",
        type=["csv", "xlsx", "xls"],
        key=f"uploader_{dataset}",
        help="Upload CSV or Excel file",
    )

    if uploaded_file is not None:
        file_bytes = uploaded_file.getvalue()
        file_hash = compute_file_hash(file_bytes)
        state.set(f"{dataset}_file_bytes", file_bytes)
        state.set(f"{dataset}_file_name", uploaded_file.name)

        # Parse file
        df_raw, sheets, parse_err = parse_raw_file(uploaded_file)
        if parse_err:
            st.error(parse_err)
            state.set(val_key, {"valid": False, "errors": [parse_err], "warnings": []})
            return

        # Multi-sheet picker if Excel
        if len(sheets) > 1:
            selected_sheet = st.selectbox(
                "Select sheet",
                options=sheets,
                key=f"sheet_{dataset}",
            )
            df_raw, _, _ = parse_raw_file(uploaded_file, sheet_name=selected_sheet)

        state.set(parsed_key, df_raw)

        # Show file summary chips
        st.markdown(
            f"""
            <div style="display: flex; gap: 8px; align-items: center; margin: 10px 0;">
                <span style="font-size: 12px; color: {COLOR_TEXT}; background: rgba(255,255,255,0.06); padding: 3px 10px; border-radius: 12px;">
                    {uploaded_file.name}
                </span>
                <span style="font-size: 12px; color: {ACCENT_CYAN}; background: rgba(28,200,238,0.1); padding: 3px 10px; border-radius: 12px;">
                    {len(df_raw):,} rows &times; {len(df_raw.columns)} cols
                </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # As-of date picker
        default_date = state.get(as_of_key) or date.today()
        as_of = st.date_input(
            "Data as of (required)",
            value=default_date,
            max_value=date.today(),
            key=f"date_picker_{dataset}",
        )
        state.set(as_of_key, as_of)

        # Column mappings
        saved_m = load_column_mapping(engine, dataset)
        suggested_m = suggest_column_mappings(df_raw.columns.tolist(), dataset, saved_m)
        state.set(mapping_key, suggested_m)

        # Run validation
        val_res = validate_and_normalize(
            df_raw=df_raw,
            dataset=dataset,
            mapping=suggested_m,
            as_of_date=as_of,
            engine=engine,
            file_hash=file_hash,
        )
        state.set(val_key, val_res)

        # Validation status chip
        if val_res["valid"]:
            if val_res.get("warnings"):
                st.markdown(
                    f'<span style="background: rgba(246,196,83,0.18); color: {ACCENT_AMBER}; border: 1px solid {ACCENT_AMBER}; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600;">Warnings ({len(val_res["warnings"])})</span>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<span style="background: rgba(46,213,115,0.18); color: {ACCENT_GREEN}; border: 1px solid {ACCENT_GREEN}; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600;">Ready</span>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                f'<span style="background: rgba(242,122,132,0.18); color: {ACCENT_CORAL}; border: 1px solid {ACCENT_CORAL}; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600;">Error ({len(val_res["errors"])})</span>',
                unsafe_allow_html=True,
            )

        # Issues & warnings expander
        if val_res.get("errors") or val_res.get("warnings") or val_res.get("rejected_rows"):
            with st.expander("View issues & warnings"):
                for err in val_res.get("errors", []):
                    st.error(err)
                for w in val_res.get("warnings", [])[:10]:
                    st.warning(w)
                rejs = val_res.get("rejected_rows", [])
                if rejs:
                    st.caption(f"{len(rejs)} row(s) excluded or rejected (Total rows, blank rows, or invalid codes).")
                    rej_df = pd.DataFrame(rejs)
                    st.dataframe(rej_df[["row_number", "reason"]], use_container_width=True, height=140)

        # Column Mapping review expander
        with st.expander("Column mapping (Review)"):
            st.caption("Verify uploaded columns matched to standard fields:")
            req_cols = INVENTORY_REQUIRED_FIELDS if dataset == "inventory" else PTW_REQUIRED_FIELDS
            for req_f in req_cols:
                mapped_to = suggested_m.get(req_f, "—")
                st.markdown(f"- **{req_f}** &rarr; `{mapped_to}`")
    else:
        state.set(val_key, None)


def _commit_and_publish_uploads(engine) -> Tuple[bool, str]:
    """
    Stage and publish uploaded files, update session state with the cleaned DataFrames,
    and set the active period before routing to the main dashboard.
    """
    from core.pipeline import stage_inventory_upload, stage_ptw_upload, commit_and_publish
    from core.data_access import clear_snapshot_cache, get_latest_snapshot_info
    from core.validation import compute_file_hash

    inv_mode = state.get("inventory_mode")
    ptw_mode = state.get("ptw_mode")

    inv_old = get_latest_snapshot_info(engine, "inventory")
    ptw_old = get_latest_snapshot_info(engine, "ptw")

    inv_date = _parse_to_date(state.get("inventory_as_of")) if inv_mode == "new" else (
        _parse_to_date(inv_old["as_of_date"]) if inv_old else None
    )
    ptw_date = _parse_to_date(state.get("ptw_as_of")) if ptw_mode == "new" else (
        _parse_to_date(ptw_old["as_of_date"]) if ptw_old else None
    )

    active_period = (
        inv_date.strftime("%b %Y") if inv_date else (
            ptw_date.strftime("%b %Y") if ptw_date else (
                inv_old["period"] if inv_old else "Oct 2026"
            )
        )
    )

    current_user = state.get("user") or {}
    user_name = current_user.get("username", "admin")

    # 1. Publish Inventory if new
    if inv_mode == "new":
        inv_val = state.get("inventory_validation") or {}
        inv_df = inv_val.get("cleaned_df")
        if inv_df is not None and not inv_df.empty:
            inv_bytes = state.get("inventory_file_bytes")
            if not inv_bytes:
                inv_bytes = inv_df.to_csv(index=False).encode("utf-8")
            inv_fname = state.get("inventory_file_name") or f"inventory_{active_period.replace(' ', '_')}.csv"
            inv_hash = compute_file_hash(inv_bytes)
            as_of_d = inv_date or date.today()

            inv_uid = stage_inventory_upload(
                engine=engine,
                period=active_period,
                as_of_date=as_of_d,
                filename=inv_fname,
                file_bytes=inv_bytes,
                file_hash=inv_hash,
                cleaned_df=inv_df,
                rejected_rows=inv_val.get("rejected_rows", []),
                user=user_name,
            )
            commit_and_publish(engine, inv_uid, user=user_name, replace_existing=True)
            state.set("inventory_df", inv_df)
            state.set("inv_row_count", len(inv_df))

    # 2. Publish PTW if new
    if ptw_mode == "new":
        ptw_val = state.get("ptw_validation") or {}
        ptw_df = ptw_val.get("cleaned_df")
        if ptw_df is not None and not ptw_df.empty:
            ptw_bytes = state.get("ptw_file_bytes")
            if not ptw_bytes:
                ptw_bytes = ptw_df.to_csv(index=False).encode("utf-8")
            ptw_fname = state.get("ptw_file_name") or f"ptw_{active_period.replace(' ', '_')}.csv"
            ptw_hash = compute_file_hash(ptw_bytes)
            as_of_d = ptw_date or date.today()

            ptw_uid = stage_ptw_upload(
                engine=engine,
                period=active_period,
                as_of_date=as_of_d,
                filename=ptw_fname,
                file_bytes=ptw_bytes,
                file_hash=ptw_hash,
                cleaned_df=ptw_df,
                rejected_rows=ptw_val.get("rejected_rows", []),
                user=user_name,
            )
            commit_and_publish(engine, ptw_uid, user=user_name, replace_existing=True)
            state.set("ptw_df", ptw_df)
            state.set("ptw_row_count", len(ptw_df))

    # 3. Clear cache and update active session state
    clear_snapshot_cache()
    state.set("selected_period", active_period)
    state.set("data_loaded", True)
    state.set("flow_stage", "main")
    return True, active_period


def render_upload():
    """Render the dual-partition upload interface."""
    inject_css()
    engine = get_engine()

    # Hide sidebar on upload view per Section 3
    st.markdown(
        """
        <style>
        body:has([class*="st-key-ghost_back_wrapper"]) [data-testid="stSidebar"],
        body:has([class*="st-key-ghost_back_wrapper"]) [data-testid="collapsedControl"] {
            display: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # 1. Ghost "Back" button on its own row ABOVE the banner, left-aligned
    back_col, _ = st.columns([3, 9])
    with back_col:
        with st.container(key="ghost_back_wrapper"):
            if st.button("← Back to home", key="btn_back_to_landing"):
                state.reset_upload_state()
                state.set("flow_stage", "landing")
                st.rerun()

    # 2. Full-width gradient banner
    page_header("Update data", "Choose a source for each side")

    # 3. Two equal columns side by side, separated by thin vertical divider
    col_left, col_divider, col_right = st.columns([10, 1, 10])

    # Independent partition rendering: if one crashes, the other still renders
    with col_left:
        try:
            render_partition("inventory", "Inventory", "Storage department", engine)
        except Exception as exc:
            st.error(f"Error loading Inventory section: {exc}")

    with col_divider:
        st.markdown(
            f"""
            <div style="border-left: 1px solid {COLOR_CARD_BORDER}; height: 100%; min-height: 480px; margin: 0 auto; width: 1px;"></div>
            """,
            unsafe_allow_html=True,
        )

    with col_right:
        try:
            render_partition("ptw", "Permit to work", "Operations", engine)
        except Exception as exc:
            st.error(f"Error loading PTW section: {exc}")

    st.markdown('<div style="margin-top: 36px;"></div>', unsafe_allow_html=True)

    # Gating & Period Alignment Check
    inv_mode = state.get("inventory_mode")
    ptw_mode = state.get("ptw_mode")

    inv_val = state.get("inventory_validation")
    ptw_val = state.get("ptw_validation")

    inv_old = get_latest_snapshot_info(engine, "inventory")
    ptw_old = get_latest_snapshot_info(engine, "ptw")

    inv_ready = False
    if inv_mode == "old" and inv_old:
        inv_ready = True
    elif inv_mode == "new" and inv_val and inv_val.get("valid"):
        inv_ready = True

    ptw_ready = False
    if ptw_mode == "old" and ptw_old:
        ptw_ready = True
    elif ptw_mode == "new" and ptw_val and ptw_val.get("valid"):
        ptw_ready = True

    both_ready = inv_ready and ptw_ready

    # Period alignment check
    inv_date = _parse_to_date(state.get("inventory_as_of")) if inv_mode == "new" else (
        _parse_to_date(inv_old["as_of_date"]) if inv_old else None
    )
    ptw_date = _parse_to_date(state.get("ptw_as_of")) if ptw_mode == "new" else (
        _parse_to_date(ptw_old["as_of_date"]) if ptw_old else None
    )

    if inv_ready and ptw_ready and inv_date and ptw_date:
        if (inv_date.year, inv_date.month) != (ptw_date.year, ptw_date.month):
            st.markdown(
                f"""
                <div style="background: rgba(246,196,83,0.12); border: 1px solid {ACCENT_AMBER}; border-radius: 10px; padding: 12px 18px; margin-bottom: 20px; text-align: center;">
                    <span style="color: {ACCENT_AMBER}; font-weight: 600;">Periods differ:</span>
                    <span style="color: {COLOR_TEXT}; margin-left: 6px;">Inventory is {inv_date.strftime('%b %Y')}, PTW is {ptw_date.strftime('%b %Y')}. Only overlapping data will be reconciled.</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # Proceed Button (Centered below the two columns)
    btn_col1, btn_col2, btn_col3 = st.columns([1, 1.4, 1])
    with btn_col2:
        if both_ready:
            if st.button("Proceed to dashboard", key="btn_proceed", type="primary", use_container_width=True):
                try:
                    ok, act_p = _commit_and_publish_uploads(engine)
                    if ok:
                        st.rerun()
                except Exception as exc:
                    st.error(f"Error publishing data: {exc}")
        else:
            st.button(
                "Proceed",
                key="btn_proceed_disabled",
                disabled=True,
                help="Choose an option on both sides and resolve any blocking errors",
                use_container_width=True,
            )


if __name__ == "__main__":
    render_upload()
