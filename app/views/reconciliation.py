"""
Reconciliation View for Recon Dashboard.
Cross-system matching review queue and Store-vs-PTW gap analysis.
"""

import streamlit as st
import pandas as pd
from sqlalchemy import text

from app import state
from app.ui.components import (
    empty_state,
    inject_css,
    page_header,
    format_currency_inr,
    status_chip,
    kpi_card,
)
from app.ui.theme import (
    ACCENT_GREEN,
    ACCENT_AMBER,
    ACCENT_CORAL,
    ACCENT_CYAN,
    ACCENT_BLUE,
    COLOR_CARD,
    COLOR_CARD_BORDER,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
)
from core.database import get_engine
from core.data_access import get_latest_period
from core.matching import confirm_match, compute_store_vs_ptw_gap
from core.auth import is_admin


def render() -> None:
    """Render the Reconciliation view."""
    inject_css()
    engine = get_engine()
    user = state.get("user") or {}
    admin = is_admin(user)

    period = state.get("selected_period") or get_latest_period(engine) or "Oct 2026"

    page_header(
        f"Reconciliation &bull; {period}",
        "PTW matching review queue and store-versus-permit variance",
    )

    tab_matching, tab_gap = st.tabs(["Matching review queue", "Store vs PTW variance"])

    # Fetch active uploads
    with engine.connect() as conn:
        inv_row = conn.execute(
            text("SELECT id, as_of_date FROM uploads WHERE dataset = 'inventory' AND period = :p AND status = 'active' ORDER BY version DESC LIMIT 1"),
            {"p": period},
        ).fetchone()

        ptw_row = conn.execute(
            text("SELECT id FROM uploads WHERE dataset = 'ptw' AND period = :p AND status = 'active' ORDER BY version DESC LIMIT 1"),
            {"p": period},
        ).fetchone()

        base_row = conn.execute(
            text("SELECT as_of_date FROM uploads WHERE dataset = 'inventory' AND status = 'active' AND as_of_date < :aod ORDER BY as_of_date DESC LIMIT 1"),
            {"aod": inv_row[1] if inv_row else "1970-01-01"},
        ).fetchone()

    inv_id = inv_row[0] if inv_row else None
    ptw_id = ptw_row[0] if ptw_row else None
    base_as_of = base_row[0] if base_row else "1970-01-01"
    as_of = inv_row[1] if inv_row else "2099-12-31"

    with tab_matching:
        if ptw_id:
            with engine.connect() as conn:
                lines_df = pd.read_sql_query(
                    """
                    SELECT id, permit_key, raw_description, cleaned_description,
                           matched_item_key, match_method, match_confidence, status
                    FROM ptw_lines
                    WHERE upload_id = :uid
                    ORDER BY match_confidence ASC
                    """,
                    con=engine,
                    params={"uid": ptw_id},
                )

            if not lines_df.empty:
                col_m1, col_m2, col_m3 = st.columns(3)
                auto_cnt = len(lines_df[lines_df["status"] == "Auto"])
                prob_cnt = len(lines_df[lines_df["status"] == "Probable"])
                un_cnt = len(lines_df[lines_df["status"] == "Unmatched"])

                with col_m1:
                    kpi_card("Auto matched", f"{auto_cnt}", accent_color=ACCENT_GREEN)
                with col_m2:
                    kpi_card("Probable match", f"{prob_cnt}", accent_color=ACCENT_AMBER)
                with col_m3:
                    kpi_card("Unmatched", f"{un_cnt}", accent_color=ACCENT_CORAL)

                st.markdown('<div style="margin-top: 18px;"></div>', unsafe_allow_html=True)
                st.dataframe(
                    lines_df[["permit_key", "raw_description", "matched_item_key", "match_confidence", "match_method", "status"]],
                    use_container_width=True,
                    height=280,
                )

                # Manual Confirmation Tool (Admin only)
                if admin:
                    st.markdown('<div style="margin-top: 14px;"></div>', unsafe_allow_html=True)
                    with st.expander("Confirm / Remap Item (Admin)"):
                        st.caption("Confirm mapping for a PTW description to save in confirmed matches:")
                        c_col1, c_col2, c_col3 = st.columns([2, 2, 1])
                        with c_col1:
                            ptw_desc_input = st.text_input("PTW text / description")
                        with c_col2:
                            item_key_input = st.text_input("Target item key (e.g. BRG-6204)")
                        with c_col3:
                            st.markdown('<div style="margin-top: 28px;"></div>', unsafe_allow_html=True)
                            if st.button("Confirm match", type="primary", use_container_width=True):
                                if ptw_desc_input and item_key_input:
                                    confirm_match(engine, ptw_desc_input, item_key_input, user.get("username", "admin"))
                                    st.success(f"Match confirmed: '{ptw_desc_input}' &rarr; '{item_key_input}'")
                                    st.rerun()
                                else:
                                    st.error("Both fields required.")
            else:
                empty_state("📋", "No PTW lines recorded for this period.")
        else:
            empty_state("📋", "No PTW data loaded for this period.")

    with tab_gap:
        if inv_id and ptw_id:
            gap_res = compute_store_vs_ptw_gap(engine, inv_id, ptw_id, base_as_of, as_of)
            tbl = gap_res.get("gap_table", [])
            if tbl:
                gap_df = pd.DataFrame(tbl)
                st.dataframe(
                    gap_df[["item_key", "store_reduced_qty", "ptw_qty", "difference_qty", "difference_value", "status", "review_status"]],
                    use_container_width=True,
                    height=340,
                )
            else:
                empty_state("⚖️", "No variance records found in this window.")
        else:
            empty_state("⚖️", "Both Inventory and PTW data are required for variance analysis.")


if __name__ == "__main__":
    render()
