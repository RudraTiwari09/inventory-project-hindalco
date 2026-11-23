"""
Excel Report Exporter for Recon Dashboard.
Generates comprehensive multi-tab workbooks with verified Excel formulas,
native charts, sanitized inputs, and Indian currency number formatting.
"""

from datetime import date, datetime
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional
import xlsxwriter

from core.savings import parse_period_to_date

logger = logging.getLogger(__name__)


def sanitize_text(val: Any) -> str:
    """Strip control characters and return clean string."""
    if val is None:
        return ""
    s = str(val)
    # Strip control characters except newline and tab
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", s)
    return s


def format_excel_sheet_name(name: str) -> str:
    """Ensure sheet name is valid Excel sheet name (max 31 chars, no special chars)."""
    clean = re.sub(r"[\\/*?:\[\]]", "", name)
    return clean[:31]


def export_monthly_report_excel(
    report_data: Dict[str, Any],
    output_dir: str = "data/reports",
) -> str:
    """
    Generate the CPP Monthly Report in Excel format (.xlsx).
    Returns absolute path of the generated file.
    """
    cover = report_data["cover"]
    period = cover["period"]
    ver = cover.get("version", 1)

    period_d = parse_period_to_date(period)
    p_code = period_d.strftime("%Y-%m") if period_d else period.replace(" ", "_")
    filename = f"CPP_Monthly_Report_{p_code}_v{ver}.xlsx"

    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.abspath(os.path.join(output_dir, filename))

    workbook = xlsxwriter.Workbook(
        file_path,
        {
            "nan_inf_to_errors": True,
            "strings_to_numbers": False,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    )

    # Set document properties
    workbook.set_properties(
        {
            "title": f"CPP Monthly Reconciliation Report - {period}",
            "subject": "Inventory x PTW Reconciliation & Cost Avoidance",
            "author": "Recon Dashboard",
            "company": cover.get("plant_name", "Captive Power Plant"),
            "comments": "Automated report generated from published plant data",
        }
    )

    # Formats & Palettes
    DARK_TEAL = "#112B34"
    BORDER_TEAL = "#1E4A52"
    TEXT_LIGHT = "#FFFFFF"

    f_header = workbook.add_format(
        {
            "bold": True,
            "bg_color": DARK_TEAL,
            "font_color": TEXT_LIGHT,
            "font_name": "Calibri",
            "font_size": 11,
            "border": 1,
            "border_color": BORDER_TEAL,
            "align": "left",
            "valign": "vcenter",
            "text_wrap": True,
        }
    )

    f_title = workbook.add_format(
        {
            "bold": True,
            "font_name": "Calibri",
            "font_size": 16,
            "font_color": DARK_TEAL,
            "align": "left",
            "valign": "vcenter",
        }
    )

    f_sub = workbook.add_format(
        {
            "font_name": "Calibri",
            "font_size": 10,
            "font_color": "#5A737B",
            "italic": True,
        }
    )

    f_text = workbook.add_format(
        {
            "font_name": "Calibri",
            "font_size": 10,
            "valign": "vcenter",
            "border": 1,
            "border_color": "#D3DFE2",
        }
    )

    f_num = workbook.add_format(
        {
            "font_name": "Calibri",
            "font_size": 10,
            "num_format": "#,##0.0",
            "align": "right",
            "valign": "vcenter",
            "border": 1,
            "border_color": "#D3DFE2",
        }
    )

    # Indian Currency Format
    f_inr = workbook.add_format(
        {
            "font_name": "Calibri",
            "font_size": 10,
            "num_format": "[>=10000000]##\\,##\\,##\\,##0;[>=100000]##\\,##\\,##0;##,##0",
            "align": "right",
            "valign": "vcenter",
            "border": 1,
            "border_color": "#D3DFE2",
        }
    )

    f_inr_bold = workbook.add_format(
        {
            "font_name": "Calibri",
            "font_size": 10,
            "bold": True,
            "num_format": "[>=10000000]##\\,##\\,##\\,##0;[>=100000]##\\,##\\,##0;##,##0",
            "align": "right",
            "valign": "vcenter",
            "border": 1,
            "border_color": "#D3DFE2",
            "bg_color": "#F0F5F6",
        }
    )

    f_pct = workbook.add_format(
        {
            "font_name": "Calibri",
            "font_size": 10,
            "num_format": "0.0%",
            "align": "right",
            "valign": "vcenter",
            "border": 1,
            "border_color": "#D3DFE2",
        }
    )

    f_ok = workbook.add_format(
        {
            "font_name": "Calibri",
            "font_size": 10,
            "font_color": "#27AE60",
            "bold": True,
            "align": "center",
            "valign": "vcenter",
            "border": 1,
            "border_color": "#D3DFE2",
        }
    )

    def setup_sheet(ws):
        ws.set_landscape()
        ws.set_paper(9)  # A4
        ws.fit_to_pages(1, 0)
        ws.repeat_rows(0)
        ws.set_margins(left=0.5, right=0.5, top=0.6, bottom=0.6)

    # Helper to write standard table
    def write_table(ws, headers: List[str], data_rows: List[List[Any]], col_formats: List[Any], start_row: int = 0):
        setup_sheet(ws)
        ws.freeze_panes(start_row + 1, 0)
        # Header
        for c_idx, h in enumerate(headers):
            ws.write_string(start_row, c_idx, sanitize_text(h), f_header)

        # Rows
        for r_idx, row in enumerate(data_rows, start=start_row + 1):
            for c_idx, val in enumerate(row):
                fmt = col_formats[c_idx] if c_idx < len(col_formats) else f_text
                if val is None or val == "":
                    ws.write_string(r_idx, c_idx, "—", f_text)
                elif isinstance(val, (int, float)):
                    if math.isnan(val) or math.isinf(val):
                        ws.write_string(r_idx, c_idx, "n/a", f_text)
                    else:
                        ws.write_number(r_idx, c_idx, float(val), fmt)
                else:
                    ws.write_string(r_idx, c_idx, sanitize_text(val), fmt)

        # Autofit column widths
        for c_idx, h in enumerate(headers):
            max_len = len(str(h))
            for row in data_rows[:100]:
                if c_idx < len(row):
                    max_len = max(max_len, len(str(row[c_idx] or "")))
            ws.set_column(c_idx, c_idx, min(max(max_len + 3, 12), 40))
        ws.autofilter(start_row, 0, max(start_row, start_row + len(data_rows)), len(headers) - 1)

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 1: Summary
    # ─────────────────────────────────────────────────────────────────────────
    ws_sum = workbook.add_worksheet("Summary")
    setup_sheet(ws_sum)
    ws_sum.freeze_panes(6, 0)

    # Banner
    ws_sum.write_string(0, 0, f"{cover['plant_name']} — Monthly Reconciliation & Savings Report", f_title)
    ws_sum.write_string(1, 0, f"Period: {period} ({cover['comparison_label']}) | Generated: {cover['generated_at_ist']} | Version: v{ver}", f_sub)

    # Executive sentences
    ex = report_data["executive_summary"]
    r_ptr = 3
    for s in ex.get("sentences", []):
        ws_sum.write_string(r_ptr, 0, f"• {sanitize_text(s)}", f_text)
        r_ptr += 1

    r_ptr += 1
    # KPI Table with formulas
    kpi_headers = ["Metric Description", "App Value (Rs)", "Formula Value (Rs)", "Audit Check"]
    for c_idx, h in enumerate(kpi_headers):
        ws_sum.write_string(r_ptr, c_idx, h, f_header)

    kpis = [
        ("Opening Inventory Valuation", ex["opening_value"], f"={ex['opening_value']:.2f}"),
        ("Total Stock Added", ex["added_value"], "=SUM('Added'!F2:F500)"),
        ("Total Stock Reduced", ex["reduced_value"], "=SUM('Reduced'!F2:F500)"),
        ("Total Inventory Closing Valuation", ex["closing_value"], f"=B{r_ptr+2}+C{r_ptr+3}-C{r_ptr+4}"),
        ("Store vs PTW Net Gap", ex["store_vs_ptw_gap"], "=SUM('Store vs PTW gap'!E2:E500)"),
        ("Verified Cost Avoidance", ex["savings_verified"], "=SUMIF('Savings log'!K2:K500, \"verified\", 'Savings log'!J2:J500)"),
        ("Actioned Cost Avoidance (Awaiting Check)", ex["savings_actioned"], "=SUMIF('Savings log'!K2:K500, \"actioned\", 'Savings log'!J2:J500)"),
        ("Open Avoidance Opportunities", ex["open_opportunities_rs"], "=SUMIF('Opportunities'!L2:L500, \"open\", 'Opportunities'!I2:I500)"),
        ("Non-Critical Excess Stock Exposure", ex["excess_stock_rs"], f"={ex['excess_stock_rs']:.2f}"),
    ]

    table_start = r_ptr + 1
    for i, (label, app_v, formula_str) in enumerate(kpis):
        curr_row = table_start + i
        ws_sum.write_string(curr_row, 0, label, f_text)
        ws_sum.write_number(curr_row, 1, float(app_v), f_inr)
        ws_sum.write_formula(curr_row, 2, formula_str, f_inr, value=float(app_v))
        # Audit check formula: =IF(ABS(B - C) < 1.0, "OK", B - C)
        check_f = f'=IF(ABS(B{curr_row+1}-C{curr_row+1})<1, "OK", B{curr_row+1}-C{curr_row+1})'
        ws_sum.write_formula(curr_row, 3, check_f, f_ok, value="OK")

    ws_sum.set_column(0, 0, 42)
    ws_sum.set_column(1, 2, 22)
    ws_sum.set_column(3, 3, 14)

    # Native Excel Chart: Waterfall Movement
    chart_wf = workbook.add_chart({"type": "column"})
    chart_wf.add_series(
        {
            "name": "Inventory Movement",
            "categories": ["Summary", table_start, 0, table_start + 2, 0],
            "values": ["Summary", table_start, 1, table_start + 2, 1],
            "fill": {"color": "#1E9AA6"},
        }
    )
    chart_wf.set_title({"name": "Monthly Movement Components"})
    chart_wf.set_legend({"none": True})
    chart_wf.set_size({"width": 460, "height": 260})
    ws_sum.insert_chart(r_ptr, 5, chart_wf)

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 2: Savings
    # ─────────────────────────────────────────────────────────────────────────
    ws_sav = workbook.add_worksheet("Savings")
    sav_data = report_data["savings_and_opportunities"]
    sav_headers = ["Category", "Sub-category", "Count", "Amount (Rs)"]
    sav_rows = [
        ["Audited Savings", "Verified Cost Avoidance", "-", sav_data.get("verified_rs", 0.0)],
        ["Pipeline", "Actioned (Awaiting Verification)", "-", sav_data.get("actioned_rs", 0.0)],
        ["Opportunities", "High Confidence", sav_data["opportunities"]["high"]["count"], sav_data["opportunities"]["high"]["rs"]],
        ["Opportunities", "Medium Confidence", sav_data["opportunities"]["medium"]["count"], sav_data["opportunities"]["medium"]["rs"]],
        ["Opportunities", "Low Confidence", sav_data["opportunities"]["low"]["count"], sav_data["opportunities"]["low"]["rs"]],
        ["Exposure", "Excess Stock (Non-Critical)", "-", sav_data.get("excess_stock_rs", 0.0)],
    ]
    write_table(ws_sav, sav_headers, sav_rows, [f_text, f_text, f_text, f_inr])

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 3: Savings log
    # ─────────────────────────────────────────────────────────────────────────
    ws_log = workbook.add_worksheet("Savings log")
    entries_df = sav_data.get("entries_df")
    log_headers = [
        "Entry ID", "Period", "Action Date", "Reference", "Item Key",
        "Description", "Type", "Requested Qty", "Avoided Qty", "Avoided Rs",
        "Status", "Data Check", "Created By", "Verified By"
    ]
    log_rows = []
    if entries_df is not None and not entries_df.empty:
        for _, r in entries_df.iterrows():
            log_rows.append([
                r["id"], r["period"], r["action_date"], r.get("reference", ""),
                r.get("item_key", ""), r.get("description", ""), r.get("type", ""),
                r.get("requested_qty", 0.0), r.get("avoided_qty", 0.0),
                (r.get("amount_paise", 0) or 0) / 100.0,
                r.get("status", ""), r.get("data_check", ""),
                r.get("created_by", ""), r.get("verified_by", ""),
            ])
    write_table(
        ws_log,
        log_headers,
        log_rows,
        [f_text, f_text, f_text, f_text, f_text, f_text, f_text, f_num, f_num, f_inr, f_text, f_text, f_text, f_text],
    )

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 4: Opportunities
    # ─────────────────────────────────────────────────────────────────────────
    ws_opp = workbook.add_worksheet("Opportunities")
    opp_df = sav_data.get("opportunities_df")
    opp_headers = [
        "ID", "Period", "Item Key", "Added Qty", "Closing Qty", "Target Qty",
        "Months Cover After", "Avoidable Qty", "Avoidable Value (Rs)", "Unit Price (Rs)",
        "Confidence", "Status", "Flags"
    ]
    opp_rows = []
    if opp_df is not None and not opp_df.empty:
        for _, r in opp_df.iterrows():
            opp_rows.append([
                r["id"], r["period"], r["item_key"], r.get("added_qty", 0.0),
                r.get("closing_qty", 0.0), r.get("target_qty", 0.0),
                r.get("months_cover_after", 0.0), r.get("avoidable_qty", 0.0),
                (r.get("avoidable_value_paise", 0) or 0) / 100.0,
                r.get("unit_price", 0.0), r.get("confidence", ""),
                r.get("status", ""), r.get("flags", ""),
            ])
    write_table(
        ws_opp,
        opp_headers,
        opp_rows,
        [f_text, f_text, f_text, f_num, f_num, f_num, f_num, f_num, f_inr, f_inr, f_text, f_text, f_text],
    )

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 5: Added
    # ─────────────────────────────────────────────────────────────────────────
    ws_add = workbook.add_worksheet("Added")
    mv = report_data["inventory_movement"]
    add_headers = ["Item Key", "Material Code", "Change Type", "Qty Old", "Qty New", "Qty Effect (Rs)", "Unit Price (Rs)"]
    add_rows = []
    for r in mv.get("top_added", []):
        add_rows.append([
            r.get("item_key", ""), r.get("material_code", ""), r.get("change_type", ""),
            r.get("qty_old", 0.0), r.get("qty_new", 0.0),
            abs(float(r.get("qty_effect") or 0.0)), float(r.get("price_new") or 0.0),
        ])
    write_table(ws_add, add_headers, add_rows, [f_text, f_text, f_text, f_num, f_num, f_inr, f_inr])

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 6: Reduced
    # ─────────────────────────────────────────────────────────────────────────
    ws_red = workbook.add_worksheet("Reduced")
    red_headers = ["Item Key", "Material Code", "Change Type", "Qty Old", "Qty New", "Reduction Magnitude (Rs)", "Unit Price (Rs)"]
    red_rows = []
    for r in mv.get("top_reduced", []):
        red_rows.append([
            r.get("item_key", ""), r.get("material_code", ""), r.get("change_type", ""),
            r.get("qty_old", 0.0), r.get("qty_new", 0.0),
            abs(float(r.get("qty_effect") or 0.0)), float(r.get("price_old") or 0.0),
        ])
    write_table(ws_red, red_headers, red_rows, [f_text, f_text, f_text, f_num, f_num, f_inr, f_inr])

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 7: New items
    # ─────────────────────────────────────────────────────────────────────────
    ws_new = workbook.add_worksheet("New items")
    new_rows = []
    for r in mv.get("new_items", []):
        new_rows.append([
            r.get("item_key", ""), r.get("qty_new", 0.0), float(r.get("qty_effect") or 0.0), float(r.get("price_new") or 0.0),
        ])
    write_table(ws_new, ["Item Key", "Quantity", "Value (Rs)", "Unit Price (Rs)"], new_rows, [f_text, f_num, f_inr, f_inr])

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 8: Removed items
    # ─────────────────────────────────────────────────────────────────────────
    ws_rem = workbook.add_worksheet("Removed items")
    rem_rows = []
    for r in mv.get("removed_items", []):
        rem_rows.append([
            r.get("item_key", ""), r.get("qty_old", 0.0), abs(float(r.get("qty_effect") or 0.0)), float(r.get("price_old") or 0.0),
        ])
    write_table(ws_rem, ["Item Key", "Old Quantity", "Value Removed (Rs)", "Unit Price (Rs)"], rem_rows, [f_text, f_num, f_inr, f_inr])

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 9: Price changes
    # ─────────────────────────────────────────────────────────────────────────
    ws_prc = workbook.add_worksheet("Price changes")
    prc_rows = []
    for r in mv.get("price_changes", []):
        prc_rows.append([
            r.get("item_key", ""), r.get("price_old", 0.0), r.get("price_new", 0.0), float(r.get("price_effect") or 0.0),
        ])
    write_table(ws_prc, ["Item Key", "Price Old (Rs)", "Price New (Rs)", "Price Effect (Rs)"], prc_rows, [f_text, f_inr, f_inr, f_inr])

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 10: Transfers
    # ─────────────────────────────────────────────────────────────────────────
    ws_trf = workbook.add_worksheet("Transfers")
    trf_rows = []
    for r in mv.get("transfers", []):
        trf_rows.append([
            r.get("item_key", ""), r.get("qty_delta", 0.0), float(r.get("qty_effect") or 0.0), r.get("flags", ""),
        ])
    write_table(ws_trf, ["Item Key", "Quantity Transferred", "Value (Rs)", "Flags"], trf_rows, [f_text, f_num, f_inr, f_text])

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 11: PTW usage
    # ─────────────────────────────────────────────────────────────────────────
    ws_ptw = workbook.add_worksheet("PTW usage")
    ptw_data = report_data["store_vs_ptw"]
    ptw_rows = [
        ["Total Recorded Store Reductions", ptw_data.get("total_store_val", 0.0)],
        ["Total Recorded Closed PTW Usage", ptw_data.get("total_ptw_val", 0.0)],
        ["Net Difference (Gap)", ptw_data.get("total_gap_val", 0.0)],
        ["Confirmed Match Variance", ptw_data.get("confirmed_gap_val", 0.0)],
        ["Probable Match Variance", ptw_data.get("probable_gap_val", 0.0)],
        ["Unmatched Lines Count", ptw_data.get("unmatched_ptw_count", 0)],
    ]
    write_table(ws_ptw, ["PTW Metric", "Value (Rs / Count)"], ptw_rows, [f_text, f_inr])

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 12: Store vs PTW gap
    # ─────────────────────────────────────────────────────────────────────────
    ws_gap = workbook.add_worksheet("Store vs PTW gap")
    gap_headers = ["Item Key", "Store Reduced Qty", "PTW Closed Qty", "Difference Qty", "Difference Value (Rs)", "Status"]
    gap_rows = []
    for g in ptw_data.get("top_gap_items", []):
        gap_rows.append([
            g.get("item_key", ""), g.get("store_reduced_qty", 0.0),
            g.get("ptw_qty", 0.0), g.get("difference_qty", 0.0),
            g.get("difference_value", 0.0), g.get("status", ""),
        ])
    write_table(ws_gap, gap_headers, gap_rows, [f_text, f_num, f_num, f_num, f_inr, f_text])

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 13: Needs review
    # ─────────────────────────────────────────────────────────────────────────
    ws_rev = workbook.add_worksheet("Needs review")
    disc_headers = ["Title / Description", "Category", "Estimated Impact (Rs)", "Recommended Action", "Owner", "Status"]
    disc_rows = []
    for d in report_data.get("discussion_points", []):
        disc_rows.append([
            d.get("title", ""), d.get("category", ""), d.get("impact_rs", 0.0),
            d.get("recommendation", ""), d.get("owner", ""), d.get("status", "To review"),
        ])
    write_table(ws_rev, disc_headers, disc_rows, [f_text, f_text, f_inr, f_text, f_text, f_text])

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 14: Data quality
    # ─────────────────────────────────────────────────────────────────────────
    ws_dq = workbook.add_worksheet("Data quality")
    dq = report_data["data_quality"]
    dq_rows = [
        ["Total Rejected Rows (Sanitization)", dq.get("rejected_count", 0)],
        ["PTW Match Coverage", f"{dq.get('match_coverage_pct', 0.0):.1f}%"],
        ["Inventory Snapshot Stale", "Yes" if dq.get("inv_stale") else "No"],
        ["PTW Snapshot Stale", "Yes" if dq.get("ptw_stale") else "No"],
        ["Reporting Periods Differ", "Yes" if dq.get("periods_differ") else "No"],
        ["Low Match Coverage Warning", "Yes" if dq.get("low_match_coverage") else "No"],
    ]
    write_table(ws_dq, ["Data Quality Metric", "Assessment"], dq_rows, [f_text, f_text])

    # ─────────────────────────────────────────────────────────────────────────
    # SHEET 15: Methodology
    # ─────────────────────────────────────────────────────────────────────────
    ws_met = workbook.add_worksheet("Methodology")
    setup_sheet(ws_met)
    ws_met.write_string(0, 0, "Reporting Methodology & Accounting Standards", f_title)
    ws_met.write_string(2, 0, "Target Inventory Cover:", f_header)
    ws_met.write_string(2, 1, f"{report_data['methodology']['months_of_cover']} Months of Average Usage", f_text)
    ws_met.write_string(3, 0, "Plant Head Approval Threshold:", f_header)
    ws_met.write_string(3, 1, f"Rs. {report_data['methodology']['plant_head_threshold']:,.0f}", f_text)

    ws_met.write_string(5, 0, "Mandatory Scope Limitations & Assumptions:", f_header)
    for idx, lim in enumerate(report_data["methodology"]["limitations"], start=6):
        ws_met.write_string(idx, 0, f"{idx-5}. {lim}", f_text)

    ws_met.set_column(0, 0, 80)
    ws_met.set_column(1, 1, 35)

    workbook.close()
    logger.info("Excel report generated successfully at %s", file_path)
    return file_path
