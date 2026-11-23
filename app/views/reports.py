"""
Reports View for Recon Dashboard.
Allows users to generate, inspect, and download monthly audit-ready reconciliation packages
in Excel (.xlsx), PDF (.pdf), PowerPoint (.pptx), and unified ZIP archives.
Features idempotent change-detection, partial-failure resilience, execution history,
and live executive preview.
"""

from datetime import datetime
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
import zipfile
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy import text

from app import state
from app.ui.components import (
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
    COLOR_CARD,
    COLOR_CARD_BORDER,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
)
from core.auth import is_admin
from core.database import get_engine
from core.data_access import get_available_periods, get_latest_period
from core.reporting.excel import export_monthly_report_excel
from core.reporting.metrics import (
    build_report_data,
    compute_report_fingerprint,
    format_inr,
)
from core.reporting.pdf import export_monthly_report_pdf
from core.reporting.pptx import export_monthly_report_pptx
from core.savings import parse_period_to_date
from core.settings import get_setting_bool

logger = logging.getLogger(__name__)
KOLKATA_TZ = ZoneInfo("Asia/Kolkata")


def format_file_size(num_bytes: int) -> str:
    """Format bytes into readable KB / MB."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    else:
        return f"{num_bytes / (1024 * 1024):.1f} MB"


def render_reports():
    """Main render entrypoint for the Reports page."""
    inject_css()
    engine = get_engine()

    page_header(
        "Monthly Reports & Executive Packages",
        "Generate and download audit-ready Excel workbooks, executive PDF summaries, and presentations.",
    )

    available_periods = get_available_periods(engine)
    if not available_periods:
        empty_state(
            icon="summarize",
            message="No published snapshot data available. Publish a monthly snapshot to generate reports.",
            button_text="Go to Upload",
            button_key="rep_no_data_upload",
            on_click=lambda: state.set("flow_stage", "upload"),
        )
        return

    # Period Selection
    default_p = state.get("reports_period") or state.get("selected_period")
    if default_p not in available_periods:
        default_p = available_periods[0]
    p_idx = available_periods.index(default_p)

    col_sel, col_gen = st.columns([3, 1])
    with col_sel:
        selected_period = st.selectbox(
            "Select Reporting Period",
            options=available_periods,
            index=p_idx,
            key="reports_period_selector",
            help="Select the confirmed monthly period to generate or view reports for",
        )
        state.set("reports_period", selected_period)

    # Check existing runs for this period
    with engine.connect() as conn:
        latest_run = conn.execute(
            text(
                """
                SELECT id, period, version, generated_by, generated_at, fingerprint, formats, file_paths, status, duration_s
                FROM report_runs
                WHERE period = :p
                ORDER BY id DESC LIMIT 1
                """
            ),
            {"p": selected_period},
        ).mappings().fetchone()

    # Pre-compute current fingerprint to verify if data changed
    current_user = state.get("user") or "tiwari"
    if isinstance(current_user, dict):
        current_user = current_user.get("username", "tiwari")

    # Generate Report action
    generate_clicked = False
    with col_gen:
        st.write("")  # alignment spacing
        st.write("")
        if st.button("Generate report", key="reports_generate_btn", type="primary", use_container_width=True):
            generate_clicked = True

    output_dir = "data/reports"
    os.makedirs(output_dir, exist_ok=True)

    if generate_clicked:
        t0 = time.time()
        file_paths_map: Dict[str, str] = {}
        formats_list: List[str] = []
        errors_list: List[str] = []

        with st.status(f"Generating Monthly Report for {selected_period}...", expanded=True) as status_box:
            # Step 1: Collect canonical data
            status_box.update(label="1/5 Reconciling canonical monthly metrics...")
            try:
                report_data = build_report_data(engine, selected_period, user=current_user)
            except Exception as e:
                logger.exception("Error building report data: %s", e)
                st.error(f"Failed to reconcile data for {selected_period}: {e}")
                return

            current_fingerprint = report_data["cover"].get("fingerprint", "")
            ver = report_data["cover"].get("version", 1)

            # Step 2: Excel Export
            status_box.update(label="2/5 Building 15-sheet Excel workbook with formulas...")
            try:
                excel_p = export_monthly_report_excel(report_data, output_dir=output_dir)
                if excel_p and os.path.exists(excel_p):
                    file_paths_map["excel"] = excel_p
                    formats_list.append("xlsx")
            except Exception as e:
                logger.exception("Excel export error: %s", e)
                errors_list.append(f"Excel export: {e}")

            # Step 3: PDF Export
            status_box.update(label="3/5 Generating landscape PDF report with charts...")
            try:
                pdf_p = export_monthly_report_pdf(report_data, output_dir=output_dir)
                if pdf_p and os.path.exists(pdf_p):
                    file_paths_map["pdf"] = pdf_p
                    formats_list.append("pdf")
            except Exception as e:
                logger.exception("PDF export error: %s", e)
                errors_list.append(f"PDF export: {e}")

            # Step 4: Slides Export
            include_slides = get_setting_bool(engine, "include_slides", default=True)
            if include_slides:
                status_box.update(label="4/5 Generating executive widescreen PowerPoint presentation...")
                try:
                    pptx_p = export_monthly_report_pptx(report_data, output_dir=output_dir)
                    if pptx_p and os.path.exists(pptx_p):
                        file_paths_map["pptx"] = pptx_p
                        formats_list.append("pptx")
                except Exception as e:
                    logger.exception("PowerPoint export error: %s", e)
                    errors_list.append(f"PowerPoint export: {e}")
            else:
                status_box.update(label="4/5 Skipping PowerPoint (disabled in settings)...")

            # Step 5: Packaging ZIP
            status_box.update(label="5/5 Packaging files into ZIP archive...")
            p_code = selected_period.replace(" ", "_")
            zip_filename = f"CPP_Monthly_Report_{p_code}_v{ver}.zip"
            zip_p = os.path.abspath(os.path.join(output_dir, zip_filename))
            try:
                with zipfile.ZipFile(zip_p, "w", zipfile.ZIP_DEFLATED) as zf:
                    for fpath in file_paths_map.values():
                        if os.path.exists(fpath):
                            zf.write(fpath, arcname=os.path.basename(fpath))
                if os.path.exists(zip_p):
                    file_paths_map["zip"] = zip_p
                    formats_list.append("zip")
            except Exception as e:
                logger.exception("ZIP package error: %s", e)
                errors_list.append(f"ZIP packaging: {e}")

            # Record in report_runs table
            duration_s = round(time.time() - t0, 2)
            now_str = datetime.now(KOLKATA_TZ).strftime("%Y-%m-%d %H:%M:%S")

            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE report_runs SET status = 'superseded' WHERE period = :p AND status = 'current'"
                    ),
                    {"p": selected_period},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO report_runs (period, version, generated_by, generated_at, fingerprint, formats, file_paths, status, duration_s)
                        VALUES (:period, :version, :generated_by, :generated_at, :fingerprint, :formats, :file_paths, :status, :duration_s)
                        """
                    ),
                    {
                        "period": selected_period,
                        "version": ver,
                        "generated_by": current_user,
                        "generated_at": now_str,
                        "fingerprint": current_fingerprint,
                        "formats": ",".join(formats_list),
                        "file_paths": json.dumps(file_paths_map),
                        "status": "current",
                        "duration_s": duration_s,
                    },
                )

            if errors_list:
                status_box.update(
                    label=f"Report generated with {len(errors_list)} warning(s)",
                    state="error",
                    expanded=True,
                )
                for err in errors_list:
                    st.warning(f"Partial failure: {err}")
            else:
                status_box.update(
                    label=f"Monthly report package generated successfully ({duration_s}s)!",
                    state="complete",
                    expanded=False,
                )

        st.rerun()

    # Re-fetch latest run
    with engine.connect() as conn:
        latest_run = conn.execute(
            text(
                """
                SELECT id, period, version, generated_by, generated_at, fingerprint, formats, file_paths, status, duration_s
                FROM report_runs
                WHERE period = :p
                ORDER BY id DESC LIMIT 1
                """
            ),
            {"p": selected_period},
        ).mappings().fetchone()

    st.markdown("---")

    # If no report run exists yet, prompt user to generate
    if not latest_run:
        empty_state(
            icon="description",
            message=f"No reports generated yet for {selected_period}.",
            button_text="Generate Now",
            button_key="rep_first_gen_btn",
            on_click=lambda: None,
        )
        return

    # Parse file paths map
    paths_map = {}
    try:
        paths_map = json.loads(latest_run["file_paths"])
    except Exception:
        pass

    # Check fresh fingerprint vs latest run fingerprint
    try:
        fresh_data = build_report_data(engine, selected_period, user=current_user)
        fresh_fingerprint = fresh_data["cover"].get("fingerprint", "")
    except Exception:
        fresh_data = None
        fresh_fingerprint = latest_run["fingerprint"]

    is_up_to_date = fresh_fingerprint == latest_run["fingerprint"]

    # Status Chip & Metadata Row
    col_status, col_meta = st.columns([1, 2])
    with col_status:
        if is_up_to_date:
            status_chip("Up to date with confirmed data", "success")
        else:
            status_chip("Data updated since generation — click Generate to refresh", "warning")

    with col_meta:
        dur = f"{latest_run['duration_s']:.1f}s" if latest_run["duration_s"] else "n/a"
        st.markdown(
            f"""
            <div style="font-size: 0.82rem; color: {COLOR_TEXT_MUTED}; text-align: right;">
                Generated: <b>{latest_run['generated_at']}</b> • By: <b>{latest_run['generated_by']}</b> • 
                Duration: <b>{dur}</b> • Fingerprint: <code>{latest_run['fingerprint']}</code>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.write("")

    # ─────────────────────────────────────────────────────────────────────────
    # DOWNLOAD SECTION (4 Columns)
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("### Download Reports")
    d_col1, d_col2, d_col3, d_col4 = st.columns(4)

    # 1. Excel (.xlsx)
    excel_path = paths_map.get("excel")
    with d_col1:
        if excel_path and os.path.exists(excel_path):
            sz = format_file_size(os.path.getsize(excel_path))
            with open(excel_path, "rb") as f:
                excel_bytes = f.read()
            st.download_button(
                label=f"Excel Workbook ({sz})",
                data=excel_bytes,
                file_name=os.path.basename(excel_path),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_excel_btn",
                use_container_width=True,
            )
        else:
            st.button("Excel (.xlsx) Unavailable", disabled=True, use_container_width=True)

    # 2. PDF (.pdf)
    pdf_path = paths_map.get("pdf")
    with d_col2:
        if pdf_path and os.path.exists(pdf_path):
            sz = format_file_size(os.path.getsize(pdf_path))
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
            st.download_button(
                label=f"Executive PDF ({sz})",
                data=pdf_bytes,
                file_name=os.path.basename(pdf_path),
                mime="application/pdf",
                key="dl_pdf_btn",
                use_container_width=True,
            )
        else:
            st.button("PDF (.pdf) Unavailable", disabled=True, use_container_width=True)

    # 3. Slides (.pptx)
    pptx_path = paths_map.get("pptx")
    include_slides = get_setting_bool(engine, "include_slides", default=True)
    with d_col3:
        if include_slides and pptx_path and os.path.exists(pptx_path):
            sz = format_file_size(os.path.getsize(pptx_path))
            with open(pptx_path, "rb") as f:
                pptx_bytes = f.read()
            st.download_button(
                label=f"Slides ({sz})",
                data=pptx_bytes,
                file_name=os.path.basename(pptx_path),
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                key="dl_pptx_btn",
                use_container_width=True,
            )
        elif not include_slides:
            st.button("Slides (Disabled)", disabled=True, use_container_width=True)
        else:
            st.button("Slides (.pptx) Unavailable", disabled=True, use_container_width=True)

    # 4. ZIP Package (.zip)
    zip_path = paths_map.get("zip")
    with d_col4:
        if zip_path and os.path.exists(zip_path):
            sz = format_file_size(os.path.getsize(zip_path))
            with open(zip_path, "rb") as f:
                zip_bytes = f.read()
            st.download_button(
                label=f"Full Package ZIP ({sz})",
                data=zip_bytes,
                file_name=os.path.basename(zip_path),
                mime="application/zip",
                key="dl_zip_btn",
                use_container_width=True,
            )
        else:
            st.button("Package (.zip) Unavailable", disabled=True, use_container_width=True)

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────────
    # TABS: Executive Preview & Run History
    # ─────────────────────────────────────────────────────────────────────────
    tab_prev, tab_hist = st.tabs(["Executive Summary Preview", "Generation History"])

    with tab_prev:
        if fresh_data:
            es = fresh_data["executive_summary"]

            # 6 KPI Tiles
            k1, k2, k3, k4, k5, k6 = st.columns(6)
            with k1:
                kpi_card("Closing Valuation", format_inr(es["closing_value"]), "Stock listing")
            with k2:
                kpi_card(
                    "Net Inventory Change",
                    format_inr(es["net_change_rs"]),
                    f"{es['net_change_pct']:+.1f}% vs prior",
                    color=ACCENT_CYAN,
                )
            with k3:
                kpi_card("Store vs PTW Gap", format_inr(es["store_vs_ptw_gap"]), "Discrepancy", color=ACCENT_AMBER)
            with k4:
                kpi_card(
                    "FY Cost Avoidance",
                    format_inr(es["savings_verified"] + es["savings_actioned"]),
                    "Verified & Actioned",
                    color=ACCENT_GREEN,
                )
            with k5:
                kpi_card(
                    "Open Opportunities",
                    format_inr(es["open_opportunities_rs"]),
                    f"{es['open_opportunities_count']} items",
                    color=ACCENT_GREEN,
                )
            with k6:
                kpi_card("PTW Match Coverage", f"{es['match_coverage_pct']:.1f}%", f"{es['confirmed_lines']} lines")

            st.write("")

            # Narrative sentences
            with chart_card("Key Monthly Observations & Analysis"):
                for s in es.get("sentences", []):
                    st.markdown(f"• {s}")

            st.write("")

            # Points for discussion
            st.markdown("#### Top 5 Management Discussion Points")
            pts = fresh_data.get("discussion_points", [])[:5]
            if pts:
                pts_df = []
                for p in pts:
                    pts_df.append(
                        {
                            "Point / Issue Description": p.get("title", ""),
                            "Category": p.get("category", ""),
                            "Estimated Impact": format_inr(p.get("impact_rs", 0.0)),
                            "Recommended Action": p.get("recommendation", ""),
                            "Owner": p.get("owner", "") or "[ To Assign ]",
                            "Status": p.get("status", "") or "[ To Review ]",
                        }
                    )
                st.dataframe(pts_df, use_container_width=True, hide_index=True)
            else:
                st.info("No management discussion points identified for this period.")

    with tab_hist:
        with engine.connect() as conn:
            hist_rows = conn.execute(
                text(
                    """
                    SELECT id, period, version, generated_by, generated_at, fingerprint, formats, file_paths, status, duration_s
                    FROM report_runs
                    WHERE period = :p
                    ORDER BY id DESC LIMIT 10
                    """
                ),
                {"p": selected_period},
            ).mappings().fetchall()

        if hist_rows:
            table_records = []
            for r in hist_rows:
                f_map = {}
                try:
                    f_map = json.loads(r["file_paths"])
                except Exception:
                    pass

                sizes_str = []
                for fmt, fpath in f_map.items():
                    if os.path.exists(fpath):
                        sizes_str.append(f"{fmt.upper()}: {format_file_size(os.path.getsize(fpath))}")
                
                table_records.append(
                    {
                        "Run ID": r["id"],
                        "Timestamp": r["generated_at"],
                        "Triggered By": r["generated_by"],
                        "Fingerprint": r["fingerprint"],
                        "Formats & Sizes": " | ".join(sizes_str) if sizes_str else r["formats"],
                        "Duration": f"{r['duration_s']:.1f}s" if r["duration_s"] else "—",
                        "Status": r["status"].title(),
                    }
                )
            st.dataframe(table_records, use_container_width=True, hide_index=True)
        else:
            st.info("No prior report generation history found for this period.")


# Allow standalone execution
if __name__ == "__main__":
    safe_render(render_reports)

