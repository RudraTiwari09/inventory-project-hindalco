"""
Upload History and Audit Trail View (Admin only).
Displays versioned upload registry, raw file download, void controls,
and full system audit logs.
"""

import os
import streamlit as st
import pandas as pd
from sqlalchemy import text

from app import state
from app.ui.components import (
    inject_css,
    page_header,
    status_chip,
    empty_state,
)
from app.ui.theme import (
    COLOR_CARD,
    COLOR_CARD_BORDER,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
    ACCENT_GREEN,
    ACCENT_AMBER,
    ACCENT_CORAL,
    ACCENT_CYAN,
)
from core.database import get_engine
from core.audit import get_audit_trail
from core.pipeline import void_latest_upload
from core.auth import is_admin, require_admin


def render_upload_history():
    """Render upload history and audit log for administrator."""
    inject_css()
    engine = get_engine()
    user = state.get("user") or {}

    # Server-side guard
    if not is_admin(user):
        st.error("Admin privileges required to access Upload History and Audit Logs.")
        st.stop()

    page_header("Upload History & Audit", "Versioned snapshot registry and security event logs")

    tab_uploads, tab_audit = st.tabs(["Upload registry", "Security audit log"])

    with tab_uploads:
        with engine.connect() as conn:
            uploads_df = pd.read_sql_query(
                """
                SELECT id, dataset, period, as_of_date, version, status,
                       file_name, SUBSTR(file_hash, 1, 8) as hash8,
                       row_count, rejected_count, uploaded_by, uploaded_at, stored_path
                FROM uploads
                ORDER BY id DESC
                """,
                con=engine,
            )

        if not uploads_df.empty:
            st.dataframe(
                uploads_df[[
                    "id", "dataset", "period", "as_of_date", "version",
                    "status", "file_name", "hash8", "row_count", "rejected_count",
                    "uploaded_by", "uploaded_at"
                ]],
                use_container_width=True,
                height=280,
            )

            st.markdown('<div style="margin-top: 18px;"></div>', unsafe_allow_html=True)
            col_v1, col_v2 = st.columns([2, 1])

            with col_v1:
                # Void latest upload action
                active_uploads = uploads_df[uploads_df["status"] == "active"]
                if not active_uploads.empty:
                    upload_options = {
                        f"#{r['id']} {r['dataset']} ({r['period']} v{r['version']})": r["id"]
                        for _, r in active_uploads.iterrows()
                    }
                    selected_lbl = st.selectbox("Select active upload to void", options=list(upload_options.keys()))
                    selected_id = upload_options[selected_lbl]

                    with st.expander("Void confirmation (Danger zone)"):
                        st.warning("Voiding an active upload will restore the previous superseded version as active.")
                        confirm_txt = st.text_input("Type 'VOID' to confirm", key="void_confirm_input")
                        if st.button("Void selected upload", type="secondary", disabled=(confirm_txt != "VOID")):
                            ok, msg = void_latest_upload(engine, selected_id, user=user.get("username", "admin"))
                            if ok:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)
            with col_v2:
                # Raw file download
                if not uploads_df.empty:
                    st.caption("Download raw archived file:")
                    file_options = {
                        f"#{r['id']} {r['file_name']}": r["stored_path"]
                        for _, r in uploads_df.iterrows()
                        if pd.notna(r["stored_path"]) and os.path.exists(str(r["stored_path"]))
                    }
                    if file_options:
                        dl_lbl = st.selectbox("Select file", options=list(file_options.keys()))
                        dl_path = file_options[dl_lbl]
                        with open(dl_path, "rb") as f:
                            st.download_button(
                                "Download file",
                                data=f.read(),
                                file_name=os.path.basename(dl_path),
                                use_container_width=True,
                            )
        else:
            st.caption("No uploads recorded in database yet.")

    with tab_audit:
        trail = get_audit_trail(engine, limit=100)
        if trail:
            audit_df = pd.DataFrame(trail)
            st.dataframe(
                audit_df[["id", "event_type", "username", "ip_address", "timestamp", "details"]],
                use_container_width=True,
                height=380,
            )
        else:
            st.caption("Audit trail empty.")


if __name__ == "__main__":
    render_upload_history()

