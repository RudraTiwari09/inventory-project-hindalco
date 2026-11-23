"""
PowerPoint Slides Exporter for Recon Dashboard.
Generates a 7-slide 16:9 widescreen presentation using python-pptx.
Implements the app's sleek dark theme (#0B1E26 background, #112B34 cards, #1E4A52 borders).
"""

from datetime import datetime
import io
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

# Set matplotlib config directory to tempdir
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "recon_mpl"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from core.reporting.metrics import format_inr
from core.savings import parse_period_to_date

logger = logging.getLogger(__name__)

# Palette constants
COLOR_BG = RGBColor(0x0B, 0x1E, 0x26)         # #0B1E26
COLOR_CARD = RGBColor(0x11, 0x2B, 0x34)       # #112B34
COLOR_BORDER = RGBColor(0x1E, 0x4A, 0x52)     # #1E4A52
COLOR_TEXT = RGBColor(0xE8, 0xF1, 0xF2)       # #E8F1F2
COLOR_MUTED = RGBColor(0x8F, 0xA9, 0xAE)      # #8FA9AE
COLOR_GREEN = RGBColor(0x2E, 0xD5, 0x73)      # #2ED573
COLOR_CYAN = RGBColor(0x1E, 0x9A, 0xA6)       # #1E9AA6
COLOR_AMBER = RGBColor(0xF6, 0xC4, 0x53)      # #F6C453
COLOR_CORAL = RGBColor(0xF2, 0x7A, 0x84)      # #F27A84


def add_slide_base(prs: Presentation, title_text: str, subtitle_text: Optional[str] = None):
    """Create slide with dark background, top header banner, and bottom footer."""
    blank_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank_layout)

    # Full background
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
    bg.fill.solid()
    bg.fill.fore_color.rgb = COLOR_BG
    bg.line.fill.background()

    # Header title
    if title_text:
        title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.9))
        tf = title_box.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = title_text
        p.font.name = "Calibri"
        p.font.size = Pt(22)
        p.font.bold = True
        p.font.color.rgb = COLOR_TEXT

        if subtitle_text:
            p2 = tf.add_paragraph()
            p2.text = subtitle_text
            p2.font.name = "Calibri"
            p2.font.size = Pt(11)
            p2.font.color.rgb = COLOR_MUTED

    # Running Footer
    footer_box = slide.shapes.add_textbox(Inches(0.8), Inches(7.05), Inches(11.7), Inches(0.35))
    ftf = footer_box.text_frame
    ftf.word_wrap = True
    ftf.margin_left = ftf.margin_top = ftf.margin_right = ftf.margin_bottom = 0
    fp = ftf.paragraphs[0]
    fp.text = "Recon Dashboard • Captive Power Plant (CPP) • Confirmed & Published Data Only"
    fp.font.name = "Calibri"
    fp.font.size = Pt(9)
    fp.font.color.rgb = COLOR_MUTED

    return slide


def add_card(slide, left: float, top: float, width: float, height: float):
    """Add a rounded styled container card."""
    card = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(left),
        Inches(top),
        Inches(width),
        Inches(height),
    )
    card.fill.solid()
    card.fill.fore_color.rgb = COLOR_CARD
    card.line.color.rgb = COLOR_BORDER
    card.line.width = Pt(1)
    return card


def render_dark_inventory_chart(report_data: Dict[str, Any]) -> io.BytesIO:
    """Generate dark-themed inventory movement bar chart."""
    wf = report_data.get("inventory_movement", {}).get("waterfall", {})
    categories = ["Opening", "Added", "Reduced", "Reval", "Closing"]
    op = float(wf.get("opening", 0.0)) / 100_000.0
    ad = float(wf.get("added", 0.0)) / 100_000.0
    rd = -abs(float(wf.get("reduced", 0.0))) / 100_000.0
    rv = float(wf.get("revaluation", 0.0)) / 100_000.0
    cl = float(wf.get("closing", 0.0)) / 100_000.0

    vals = [op, ad, rd, rv, cl]
    bar_colors = ["#1E9AA6", "#2ED573", "#F27A84", "#D3A4EA", "#2ED573"]

    fig, ax = plt.subplots(figsize=(5.6, 3.4), dpi=200, facecolor="#112B34")
    ax.set_facecolor("#112B34")
    bars = ax.bar(categories, vals, color=bar_colors, width=0.5)
    ax.axhline(0, color="#8FA9AE", linewidth=0.7)
    ax.set_ylabel("Value (Rs. Lakhs)", fontsize=8, color="#8FA9AE")
    ax.tick_params(colors="#E8F1F2", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#1E4A52")

    for bar in bars:
        h = bar.get_height()
        va = "bottom" if h >= 0 else "top"
        y_pos = h + (1.2 if h >= 0 else -3.5)
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            y_pos,
            f"{abs(h):.1f} L",
            ha="center",
            va=va,
            fontsize=7,
            fontweight="bold",
            color="#E8F1F2",
        )

    fig.subplots_adjust(bottom=0.18, top=0.92, left=0.18, right=0.95)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=200, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf


def render_dark_store_ptw_chart(report_data: Dict[str, Any]) -> io.BytesIO:
    """Generate dark-themed Store vs PTW chart."""
    st_val = float(report_data["executive_summary"].get("store_value", 0.0)) / 100_000.0
    pt_val = float(report_data["executive_summary"].get("ptw_value", 0.0)) / 100_000.0
    gap_val = float(report_data["executive_summary"].get("store_vs_ptw_gap", 0.0)) / 100_000.0

    cats = ["Store Issues", "PTW Logged", "Unmatched Gap"]
    vals = [st_val, pt_val, gap_val]
    colors_list = ["#1E9AA6", "#2ED573", "#F27A84" if gap_val > 0 else "#2ED573"]

    fig, ax = plt.subplots(figsize=(5.6, 3.4), dpi=200, facecolor="#112B34")
    ax.set_facecolor("#112B34")
    bars = ax.bar(cats, vals, color=colors_list, width=0.48)
    ax.axhline(0, color="#8FA9AE", linewidth=0.7)
    ax.set_ylabel("Value (Rs. Lakhs)", fontsize=8, color="#8FA9AE")
    ax.tick_params(colors="#E8F1F2", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#1E4A52")

    for bar in bars:
        h = bar.get_height()
        va = "bottom" if h >= 0 else "top"
        y_pos = h + (1.0 if h >= 0 else -3.0)
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            y_pos,
            f"{abs(h):.1f} L",
            ha="center",
            va=va,
            fontsize=7,
            fontweight="bold",
            color="#E8F1F2",
        )

    fig.subplots_adjust(bottom=0.18, top=0.92, left=0.18, right=0.95)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=200, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf


def render_dark_savings_chart(report_data: Dict[str, Any]) -> io.BytesIO:
    """Generate dark-themed savings breakdown chart."""
    by_type = report_data.get("savings_and_opportunities", {}).get("by_type", {})
    labels = {
        "overstock_avoidance": "Overstock Avoid",
        "price_variance": "Price Variance",
        "alternative_part": "Alternate Part",
        "reconditioned_used": "Reconditioned",
        "consumption_reduction": "Consumption Red.",
    }
    names = []
    vals = []
    for k, label in labels.items():
        v = float(by_type.get(k, 0.0)) / 100_000.0
        if v > 0 or len(names) < 3:
            names.append(label)
            vals.append(v)

    fig, ax = plt.subplots(figsize=(5.6, 3.4), dpi=200, facecolor="#112B34")
    ax.set_facecolor("#112B34")
    bars = ax.barh(names, vals, color="#2ED573", height=0.45)
    ax.set_xlabel("Avoidance Value (Rs. Lakhs)", fontsize=8, color="#8FA9AE")
    ax.tick_params(colors="#E8F1F2", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#1E4A52")

    for bar in bars:
        w = bar.get_width()
        ax.text(
            w + 0.1,
            bar.get_y() + bar.get_height() / 2.0,
            f"{w:.1f} L" if w > 0 else "0.0",
            ha="left",
            va="center",
            fontsize=7,
            fontweight="bold",
            color="#E8F1F2",
        )

    fig.subplots_adjust(bottom=0.2, top=0.92, left=0.26, right=0.92)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=200, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf


def export_monthly_report_pptx(
    report_data: Dict[str, Any],
    output_dir: str = "data/reports",
) -> str:
    """
    Generate 7-slide executive widescreen presentation in PPTX format.
    Returns the absolute path to the generated file.
    """
    cover = report_data["cover"]
    period = cover["period"]
    ver = cover.get("version", 1)

    period_d = parse_period_to_date(period)
    p_code = period_d.strftime("%Y-%m") if period_d else period.replace(" ", "_")
    filename = f"CPP_Monthly_Report_{p_code}_v{ver}.pptx"

    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.abspath(os.path.join(output_dir, filename))

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    es = report_data["executive_summary"]

    # ─────────────────────────────────────────────────────────────────────────
    # SLIDE 1: Title Slide
    # ─────────────────────────────────────────────────────────────────────────
    s1 = add_slide_base(prs, "")
    # Main hero card
    add_card(s1, 1.2, 1.2, 10.933, 5.1)

    hero_box = s1.shapes.add_textbox(Inches(1.8), Inches(1.8), Inches(9.7), Inches(3.8))
    htf = hero_box.text_frame
    htf.word_wrap = True

    p = htf.paragraphs[0]
    p.text = "MONTHLY RECONCILIATION & COST AVOIDANCE"
    p.font.name = "Calibri"
    p.font.size = Pt(28)
    p.font.bold = True
    p.font.color.rgb = COLOR_GREEN

    p2 = htf.add_paragraph()
    p2.text = f"{cover['plant_name']} • {cover.get('site_name', '')}"
    p2.font.name = "Calibri"
    p2.font.size = Pt(18)
    p2.font.color.rgb = COLOR_TEXT

    p3 = htf.add_paragraph()
    p3.text = f"Reporting Period: {period}  |  Financial Year: {cover['fy_label']}"
    p3.font.name = "Calibri"
    p3.font.size = Pt(14)
    p3.font.color.rgb = COLOR_CYAN

    p4 = htf.add_paragraph()
    gen_time = cover.get("generated_at_ist") or cover.get("generated_at", "")
    gen_user = cover.get("generated_by") or cover.get("user", "system")
    p4.text = (
        f"\nGenerated: {gen_time}  •  User: {gen_user}\n"
        f"Snapshot Version: v{ver}  •  Report Fingerprint: {cover.get('fingerprint', '—')}\n"
        "Status: Published & Confirmed Data Only (Offline Autonomous)"
    )
    p4.font.name = "Calibri"
    p4.font.size = Pt(11)
    p4.font.color.rgb = COLOR_MUTED

    # ─────────────────────────────────────────────────────────────────────────
    # SLIDE 2: Month at a glance
    # ─────────────────────────────────────────────────────────────────────────
    s2 = add_slide_base(prs, "Month at a Glance", "Executive Summary & Key Performance Indicators")

    # 6 KPI cards (2 rows of 3)
    kpis = [
        ("CLOSING VALUATION", format_inr(es["closing_value"]), "Full stock listing valuation"),
        ("NET INVENTORY CHANGE", f"{format_inr(es['net_change_rs'])} ({es['net_change_pct']:+.1f}%)", "Net change vs opening snapshot"),
        ("STORE VS PTW GAP", format_inr(es["store_vs_ptw_gap"]), f"Store: {format_inr(es['store_value'])} | PTW: {format_inr(es['ptw_value'])}"),
        ("FY COST AVOIDANCE", format_inr(es["savings_verified"] + es["savings_actioned"]), f"Verified: {format_inr(es['savings_verified'])}"),
        ("OPEN OPPORTUNITIES", format_inr(es["open_opportunities_rs"]), f"{es['open_opportunities_count']} items exceeding target cover"),
        ("PTW MATCH COVERAGE", f"{es['match_coverage_pct']:.1f}%", f"{es['confirmed_lines']} confirmed / {es['ptw_lines_total']} total lines"),
    ]

    card_w = 3.65
    card_h = 1.35
    for idx, (lbl, val, sub) in enumerate(kpis):
        r = idx // 3
        c = idx % 3
        cx = 0.8 + c * (card_w + 0.38)
        cy = 1.45 + r * (card_h + 0.25)
        add_card(s2, cx, cy, card_w, card_h)

        tb = s2.shapes.add_textbox(Inches(cx + 0.15), Inches(cy + 0.12), Inches(card_w - 0.3), Inches(card_h - 0.24))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p = tf.paragraphs[0]
        p.text = lbl
        p.font.name = "Calibri"
        p.font.size = Pt(9)
        p.font.bold = True
        p.font.color.rgb = COLOR_MUTED

        p2 = tf.add_paragraph()
        p2.text = val
        p2.font.name = "Calibri"
        p2.font.size = Pt(18)
        p2.font.bold = True
        p2.font.color.rgb = COLOR_TEXT

        p3 = tf.add_paragraph()
        p3.text = sub
        p3.font.name = "Calibri"
        p3.font.size = Pt(8.5)
        p3.font.color.rgb = COLOR_CYAN

    # Highlights card at the bottom
    add_card(s2, 0.8, 4.65, 11.73, 2.2)
    hb = s2.shapes.add_textbox(Inches(1.0), Inches(4.75), Inches(11.3), Inches(2.0))
    htf = hb.text_frame
    htf.word_wrap = True
    htf.margin_left = htf.margin_top = htf.margin_right = htf.margin_bottom = 0

    p = htf.paragraphs[0]
    p.text = "Key Monthly Observations & Analysis"
    p.font.name = "Calibri"
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = COLOR_GREEN

    for sent in es.get("sentences", [])[:3]:
        p_sent = htf.add_paragraph()
        p_sent.text = f"• {sent}"
        p_sent.font.name = "Calibri"
        p_sent.font.size = Pt(11)
        p_sent.font.color.rgb = COLOR_TEXT

    # ─────────────────────────────────────────────────────────────────────────
    # SLIDE 3: Inventory Movement
    # ─────────────────────────────────────────────────────────────────────────
    s3 = add_slide_base(prs, "Inventory Movement & Reconciliation", "Snapshot reconciliation across additions, issues, and adjustments")

    # Left card: Chart
    add_card(s3, 0.8, 1.45, 5.7, 5.4)
    chart_buf_inv = render_dark_inventory_chart(report_data)
    s3.shapes.add_picture(chart_buf_inv, Inches(0.9), Inches(1.9), width=Inches(5.5))

    # Right card: Additions & Reductions summary
    add_card(s3, 6.8, 1.45, 5.73, 5.4)
    tb_mov = s3.shapes.add_textbox(Inches(7.0), Inches(1.6), Inches(5.3), Inches(5.1))
    mtf = tb_mov.text_frame
    mtf.word_wrap = True
    mtf.margin_left = mtf.margin_top = mtf.margin_right = mtf.margin_bottom = 0

    p = mtf.paragraphs[0]
    p.text = "Top Stock Inflows (Additions)"
    p.font.name = "Calibri"
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = COLOR_GREEN

    for it in report_data.get("inventory_movement", {}).get("top_added", [])[:3]:
        p_it = mtf.add_paragraph()
        p_it.text = f"• {it.get('material_code')}: {it.get('description', '')[:32]} — {format_inr(it.get('qty_effect', 0))}"
        p_it.font.name = "Calibri"
        p_it.font.size = Pt(10)
        p_it.font.color.rgb = COLOR_TEXT

    p_red = mtf.add_paragraph()
    p_red.text = "\nTop Stock Outflows (Reductions)"
    p_red.font.name = "Calibri"
    p_red.font.size = Pt(13)
    p_red.font.bold = True
    p_red.font.color.rgb = COLOR_CORAL

    for it in report_data.get("inventory_movement", {}).get("top_reduced", [])[:3]:
        p_it = mtf.add_paragraph()
        p_it.text = f"• {it.get('material_code')}: {it.get('description', '')[:32]} — {format_inr(it.get('qty_effect', 0))}"
        p_it.font.name = "Calibri"
        p_it.font.size = Pt(10)
        p_it.font.color.rgb = COLOR_TEXT

    p_note = mtf.add_paragraph()
    p_note.text = (
        f"\nInventory Reconciled: {'Yes' if es.get('reconciled') else 'Discrepancy'}\n"
        f"Closing Valuation: {format_inr(es['closing_value'])}"
    )
    p_note.font.name = "Calibri"
    p_note.font.size = Pt(10)
    p_note.font.color.rgb = COLOR_MUTED

    # ─────────────────────────────────────────────────────────────────────────
    # SLIDE 4: Store vs PTW
    # ─────────────────────────────────────────────────────────────────────────
    s4 = add_slide_base(prs, "Store Issues vs PTW Maintenance Consumption", "Variance analysis between physical store issuances and maintenance permits")

    add_card(s4, 0.8, 1.45, 5.7, 5.4)
    chart_buf_ptw = render_dark_store_ptw_chart(report_data)
    s4.shapes.add_picture(chart_buf_ptw, Inches(0.9), Inches(1.9), width=Inches(5.5))

    add_card(s4, 6.8, 1.45, 5.73, 5.4)
    tb_gap = s4.shapes.add_textbox(Inches(7.0), Inches(1.6), Inches(5.3), Inches(5.1))
    gtf = tb_gap.text_frame
    gtf.word_wrap = True
    gtf.margin_left = gtf.margin_top = gtf.margin_right = gtf.margin_bottom = 0

    p = gtf.paragraphs[0]
    p.text = "Top Variance Drivers (Store vs PTW)"
    p.font.name = "Calibri"
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = COLOR_CYAN

    for g in report_data.get("store_vs_ptw", {}).get("top_gap_items", [])[:3]:
        p_g = gtf.add_paragraph()
        p_g.text = f"• {g.get('item_code')}: {g.get('description', '')[:30]}\n   Store: {format_inr(g.get('store_issue_rs'))} | PTW: {format_inr(g.get('ptw_reported_rs'))} | Gap: {format_inr(g.get('gap_rs'))}"
        p_g.font.name = "Calibri"
        p_g.font.size = Pt(10)
        p_g.font.color.rgb = COLOR_TEXT

    p_match = gtf.add_paragraph()
    p_match.text = (
        f"\nReconciliation Summary:\n"
        f"• Total PTW Lines: {es['ptw_lines_total']}\n"
        f"• Confirmed Linked Lines: {es['confirmed_lines']} ({es['match_coverage_pct']:.1f}% coverage)\n"
        f"• Net Unexplained Gap: {format_inr(es['store_vs_ptw_gap'])}"
    )
    p_match.font.name = "Calibri"
    p_match.font.size = Pt(10.5)
    p_match.font.color.rgb = COLOR_MUTED

    # ─────────────────────────────────────────────────────────────────────────
    # SLIDE 5: Savings & Opportunities
    # ─────────────────────────────────────────────────────────────────────────
    s5 = add_slide_base(prs, "Cost Avoidance & Inventory Optimization", "Procurement avoidance, reconditioned spares, and excess stock mitigation")

    add_card(s5, 0.8, 1.45, 5.7, 5.4)
    chart_buf_sav = render_dark_savings_chart(report_data)
    s5.shapes.add_picture(chart_buf_sav, Inches(0.9), Inches(1.9), width=Inches(5.5))

    add_card(s5, 6.8, 1.45, 5.73, 5.4)
    tb_opp = s5.shapes.add_textbox(Inches(7.0), Inches(1.6), Inches(5.3), Inches(5.1))
    otf = tb_opp.text_frame
    otf.word_wrap = True
    otf.margin_left = otf.margin_top = otf.margin_right = otf.margin_bottom = 0

    p = otf.paragraphs[0]
    p.text = "Top Open Cost Avoidance Opportunities"
    p.font.name = "Calibri"
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = COLOR_GREEN

    for opp in report_data.get("savings_and_opportunities", {}).get("open_opportunities", [])[:3]:
        p_op = otf.add_paragraph()
        p_op.text = (
            f"• {opp.get('material_code')}: {opp.get('opportunity_type', '').replace('_', ' ').title()}\n"
            f"   Action: {opp.get('recommended_action', '')[:35]} — Potential: {format_inr(opp.get('potential_savings_rs'))}"
        )
        p_op.font.name = "Calibri"
        p_op.font.size = Pt(10)
        p_op.font.color.rgb = COLOR_TEXT

    p_pol = otf.add_paragraph()
    p_pol.text = (
        f"\nMethodology & Accounting Policy:\n"
        f"• Savings reflect confirmed cancellations & substitutions.\n"
        f"• Target Cover: {report_data['methodology']['months_of_cover']} Months average usage.\n"
        f"• Open Avoidance Pipeline: {format_inr(es['open_opportunities_rs'])}"
    )
    p_pol.font.name = "Calibri"
    p_pol.font.size = Pt(10)
    p_pol.font.color.rgb = COLOR_MUTED

    # ─────────────────────────────────────────────────────────────────────────
    # SLIDE 6: Points for Discussion
    # ─────────────────────────────────────────────────────────────────────────
    s6 = add_slide_base(prs, "Points for Management Discussion", "Key review items for the monthly plant reconciliation meeting")

    # Table with 5 items
    add_card(s6, 0.8, 1.45, 11.73, 5.4)

    rows_count = 6
    cols_count = 6
    table_shape = s6.shapes.add_table(rows_count, cols_count, Inches(1.0), Inches(1.65), Inches(11.33), Inches(4.8))
    table = table_shape.table

    # Column widths: Point(3.5), Category(1.5), Impact(1.3), Action(2.5), Owner(1.2), Status(1.3)
    col_widths = [Inches(3.3), Inches(1.4), Inches(1.3), Inches(2.7), Inches(1.3), Inches(1.3)]
    for i, w in enumerate(col_widths):
        table.columns[i].width = w

    headers = ["Point / Issue Description", "Category", "Impact (Rs)", "Recommended Action", "Owner", "Status"]
    for i, h in enumerate(headers):
        cell = table.cell(0, i)
        cell.fill.solid()
        cell.fill.fore_color.rgb = COLOR_BORDER
        cell.text_frame.word_wrap = True
        cell.text_frame.margin_left = cell.text_frame.margin_right = Pt(4)
        p = cell.text_frame.paragraphs[0]
        p.text = h
        p.font.name = "Calibri"
        p.font.size = Pt(10)
        p.font.bold = True
        p.font.color.rgb = COLOR_TEXT

    for row_idx, d in enumerate(report_data.get("discussion_points", [])[:5], start=1):
        vals = [
            d.get("title", ""),
            d.get("category", ""),
            format_inr(d.get("impact_rs", 0.0)),
            d.get("recommendation", ""),
            d.get("owner", "") or "[ ________ ]",
            d.get("status", "") or "[ To Review ]",
        ]
        for c_idx, val in enumerate(vals):
            cell = table.cell(row_idx, c_idx)
            cell.fill.solid()
            cell.fill.fore_color.rgb = COLOR_CARD
            cell.text_frame.word_wrap = True
            cell.text_frame.margin_left = cell.text_frame.margin_right = Pt(4)
            p = cell.text_frame.paragraphs[0]
            p.text = val
            p.font.name = "Calibri"
            p.font.size = Pt(9.5)
            p.font.color.rgb = COLOR_TEXT

    # ─────────────────────────────────────────────────────────────────────────
    # SLIDE 7: Data Quality & Notes
    # ─────────────────────────────────────────────────────────────────────────
    s7 = add_slide_base(prs, "Data Quality, Governance & Scope Limitations", "Audit trail, integrity checks, and sign-off status")

    # Left card: Quality metrics
    add_card(s7, 0.8, 1.45, 5.7, 5.4)
    tb_dq = s7.shapes.add_textbox(Inches(1.0), Inches(1.6), Inches(5.3), Inches(5.1))
    dqtf = tb_dq.text_frame
    dqtf.word_wrap = True
    dqtf.margin_left = dqtf.margin_top = dqtf.margin_right = dqtf.margin_bottom = 0

    p = dqtf.paragraphs[0]
    p.text = "Audit Trail & Data Completeness"
    p.font.name = "Calibri"
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = COLOR_CYAN

    dq = report_data["data_quality"]
    dq_items = [
        f"• Fingerprint (SHA-256): {cover.get('fingerprint', '—')}",
        f"• Run Timestamp: {gen_time}",
        f"• Generated By: {gen_user}",
        f"• Sanitisation Rejections: {dq.get('rejected_count', 0)} rows quarantined",
        f"• PTW Match Coverage: {dq.get('match_coverage_pct', 0.0):.1f}%",
        f"• Snapshot Staleness Alert: {'Yes' if dq.get('inv_stale') else 'None (Current)'}",
        f"• Inventory Hash: {dq.get('inv_hash', '—') or '—'}",
        f"• PTW Hash: {dq.get('ptw_hash', '—') or '—'}",
    ]
    for item in dq_items:
        p_item = dqtf.add_paragraph()
        p_item.text = item
        p_item.font.name = "Calibri"
        p_item.font.size = Pt(10)
        p_item.font.color.rgb = COLOR_TEXT

    # Right card: Limitations & Sign-off
    add_card(s7, 6.8, 1.45, 5.73, 5.4)
    tb_lim = s7.shapes.add_textbox(Inches(7.0), Inches(1.6), Inches(5.3), Inches(5.1))
    ltf = tb_lim.text_frame
    ltf.word_wrap = True
    ltf.margin_left = ltf.margin_top = ltf.margin_right = ltf.margin_bottom = 0

    p = ltf.paragraphs[0]
    p.text = "Scope Limitations & Sign-Off"
    p.font.name = "Calibri"
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = COLOR_AMBER

    for idx, lim in enumerate(report_data.get("methodology", {}).get("limitations", [])[:3], start=1):
        p_lim = ltf.add_paragraph()
        p_lim.text = f"{idx}. {lim}"
        p_lim.font.name = "Calibri"
        p_lim.font.size = Pt(9.5)
        p_lim.font.color.rgb = COLOR_TEXT

    p_sig = ltf.add_paragraph()
    p_sig.text = (
        "\nFormal Review Sign-Off:\n"
        "Store In-Charge: _______________________ Date: _________\n"
        "O&M Head: _____________________________ Date: _________\n"
        "Plant Head (CPP): ______________________ Date: _________"
    )
    p_sig.font.name = "Calibri"
    p_sig.font.size = Pt(9.5)
    p_sig.font.color.rgb = COLOR_MUTED

    prs.save(file_path)
    logger.info("PowerPoint report generated successfully at %s", file_path)
    return file_path
