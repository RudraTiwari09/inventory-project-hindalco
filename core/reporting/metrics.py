"""
Single Source of Truth Report Metrics Builder.
Extracts, reconciles, and aggregates all monthly movement, PTW gaps,
savings, data quality, and discussion points into a unified data structure.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import pandas as pd
from sqlalchemy import text

from core.data_access import is_snapshot_stale
from core.inventory_diff import compute_inventory_diff
from core.matching import compute_store_vs_ptw_gap, match_ptw_lines_to_inventory
from core.savings import get_fy_label, savings_summary
from core.settings import (
    get_setting,
    get_setting_float,
    get_setting_int,
)

logger = logging.getLogger(__name__)
KOLKATA_TZ = ZoneInfo("Asia/Kolkata")


def format_inr(val: Any) -> str:
    """Format monetary amount in Indian numbering format (Rs. L / Cr)."""
    if val is None or val == "" or str(val) == "—" or str(val) == "-":
        return "—"
    try:
        num = float(val)
        if abs(num) >= 10_000_000:
            return f"Rs. {num / 10_000_000:.2f} Cr"
        elif abs(num) >= 100_000:
            return f"Rs. {num / 100_000:.2f} L"
        elif abs(num) >= 1_000:
            return f"Rs. {num / 1_000:.1f} K"
        else:
            return f"Rs. {num:,.0f}"
    except (ValueError, TypeError):
        return "—"


def compute_report_fingerprint(
    inv_id: Optional[int],
    inv_ver: Optional[int],
    ptw_id: Optional[int],
    ptw_ver: Optional[int],
    base_id: Optional[int],
    latest_match_ts: Optional[str],
    latest_savings_ts: Optional[str],
    settings_str: str,
) -> str:
    """Compute 16-character SHA-256 fingerprint for cache and change detection."""
    raw = (
        f"{inv_id}_{inv_ver}_{ptw_id}_{ptw_ver}_{base_id}_"
        f"{latest_match_ts}_{latest_savings_ts}_{settings_str}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_report_data(
    engine,
    period: str,
    user: Optional[str] = "system",
) -> Dict[str, Any]:
    """
    Build the canonical, single-source-of-truth dataset for the monthly report.
    Used identically by on-screen views, Excel, PDF, and PowerPoint exporters.
    """
    now_ist = datetime.now(KOLKATA_TZ)

    # 1. Fetch settings
    plant_name = get_setting(engine, "plant_name", default="Captive Power Plant")
    site_name = get_setting(engine, "site_name", default="")
    footer_text = get_setting(engine, "footer_text", default="Internal use only")
    months_of_cover = get_setting_float(engine, "months_of_cover", default=6.0)
    plant_head_threshold = get_setting_float(engine, "plant_head_threshold", default=200000.0)
    fy_start_m = get_setting_int(engine, "fy_start_month", default=4)

    settings_str = f"{plant_name}_{months_of_cover}_{plant_head_threshold}_{fy_start_m}"

    with engine.connect() as conn:
        # 2. Fetch inventory upload for this period
        inv_row = conn.execute(
            text(
                """
                SELECT id, as_of_date, version, file_name, file_hash, is_baseline
                FROM uploads
                WHERE dataset = 'inventory' AND period = :p AND status = 'active'
                ORDER BY version DESC, id DESC LIMIT 1
                """
            ),
            {"p": period},
        ).fetchone()

        # 3. Fetch PTW upload for this period
        ptw_row = conn.execute(
            text(
                """
                SELECT id, as_of_date, version, file_name, file_hash
                FROM uploads
                WHERE dataset = 'ptw' AND period = :p AND status = 'active'
                ORDER BY version DESC, id DESC LIMIT 1
                """
            ),
            {"p": period},
        ).fetchone()

        # Latest match confirmation timestamp
        latest_match_ts = conn.execute(
            text("SELECT MAX(confirmed_at) FROM user_confirmed_matches")
        ).scalar() or ""

        # Latest savings entry change timestamp
        latest_savings_ts = conn.execute(
            text("SELECT MAX(COALESCE(verified_at, voided_at, created_at)) FROM savings_entries")
        ).scalar() or ""

    inv_id = inv_row[0] if inv_row else None
    inv_as_of = inv_row[1] if inv_row else "1970-01-01"
    inv_ver = inv_row[2] if inv_row else 1
    inv_fname = inv_row[3] if inv_row else "None"
    inv_hash8 = (inv_row[4][:8]) if inv_row and inv_row[4] else "none"
    is_baseline = bool(inv_row[5]) if inv_row else False

    ptw_id = ptw_row[0] if ptw_row else None
    ptw_as_of = ptw_row[1] if ptw_row else None
    ptw_ver = ptw_row[2] if ptw_row else 1
    ptw_fname = ptw_row[3] if ptw_row else "None"
    ptw_hash8 = (ptw_row[4][:8]) if ptw_row and ptw_row[4] else "none"

    # Find baseline upload
    base_id = None
    base_period = None
    base_as_of = "1970-01-01"
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
            {"aod": inv_as_of},
        ).fetchone()

        if base_row:
            base_id, base_period, base_as_of = base_row

    if not base_id:
        is_baseline = True
        comparison_label = f"{period} (Baseline period)"
        comparison_window = f"Up to {inv_as_of}"
    else:
        comparison_label = f"{period} vs {base_period}"
        comparison_window = f"({base_as_of} to {inv_as_of}]"

    fingerprint = compute_report_fingerprint(
        inv_id,
        inv_ver,
        ptw_id,
        ptw_ver,
        base_id,
        latest_match_ts,
        latest_savings_ts,
        settings_str,
    )

    # 4. Inventory Diff & Movement
    if inv_id:
        diff_data = compute_inventory_diff(engine, inv_id, base_id, inv_as_of)
    else:
        diff_data = {
            "opening_val": 0.0,
            "added_val": 0.0,
            "reduced_val": 0.0,
            "revaluation_val": 0.0,
            "closing_val": 0.0,
            "diff_records": [],
            "reconciled": False,
            "discrepancy_rs": 0.0,
        }

    # 5. PTW Matching & Gap
    if inv_id and ptw_id:
        match_ptw_lines_to_inventory(engine, ptw_id, inv_id)
        gap_data = compute_store_vs_ptw_gap(engine, inv_id, ptw_id, base_as_of, inv_as_of)
    else:
        gap_data = {
            "gap_table": [],
            "total_store_val": 0.0,
            "total_ptw_val": 0.0,
            "total_gap_val": 0.0,
            "confirmed_gap_val": 0.0,
            "probable_gap_val": 0.0,
            "unmatched_ptw_count": 0,
        }

    # 6. Savings & Opportunities Summary
    savings = savings_summary(engine, scope="period", period=period)

    # 7. Check Data Quality & Edge Cases
    periods_differ = False
    if ptw_as_of and inv_as_of:
        inv_d = datetime.strptime(inv_as_of[:10], "%Y-%m-%d").date()
        ptw_d = datetime.strptime(ptw_as_of[:10], "%Y-%m-%d").date()
        if inv_d.strftime("%Y-%m") != ptw_d.strftime("%Y-%m"):
            periods_differ = True

    inv_stale = is_snapshot_stale(engine, inv_as_of)
    ptw_stale = is_snapshot_stale(engine, ptw_as_of) if ptw_as_of else False

    # PTW Match Coverage
    ptw_lines_total = 0
    confirmed_lines = 0
    probable_lines = 0
    unmatched_lines = 0
    with engine.connect() as conn:
        if ptw_id:
            ptw_lines_total = conn.execute(
                text("SELECT COUNT(*) FROM ptw_lines WHERE upload_id = :puid"),
                {"puid": ptw_id},
            ).scalar() or 0
            confirmed_lines = conn.execute(
                text("SELECT COUNT(*) FROM ptw_lines WHERE upload_id = :puid AND status = 'Confirmed'"),
                {"puid": ptw_id},
            ).scalar() or 0
            probable_lines = conn.execute(
                text("SELECT COUNT(*) FROM ptw_lines WHERE upload_id = :puid AND status = 'Probable'"),
                {"puid": ptw_id},
            ).scalar() or 0
            unmatched_lines = conn.execute(
                text("SELECT COUNT(*) FROM ptw_lines WHERE upload_id = :puid AND status = 'Unmatched'"),
                {"puid": ptw_id},
            ).scalar() or 0

    match_coverage_pct = (
        ((confirmed_lines + probable_lines) / ptw_lines_total * 100.0)
        if ptw_lines_total > 0
        else 0.0
    )
    low_match_coverage = match_coverage_pct < 50.0 and ptw_lines_total > 0

    # Rejected rows count
    rejected_count = 0
    with engine.connect() as conn:
        uids = [u for u in (inv_id, ptw_id) if u]
        if len(uids) == 1:
            rejected_count = conn.execute(
                text("SELECT COUNT(*) FROM rejected_rows WHERE upload_id = :u1"),
                {"u1": uids[0]},
            ).scalar() or 0
        elif len(uids) > 1:
            rejected_count = conn.execute(
                text("SELECT COUNT(*) FROM rejected_rows WHERE upload_id IN (:u1, :u2)"),
                {"u1": uids[0], "u2": uids[1]},
            ).scalar() or 0

    # 8. Detailed Movement Breakdown
    diff_records = diff_data.get("diff_records") or diff_data.get("diff_rows") or []
    top_added = [
        r for r in diff_records
        if r.get("change_type") in ("Qty increase", "New item")
        and "Transfer" not in (r.get("flags") or "")
    ]
    top_added.sort(key=lambda x: float(x.get("qty_effect") or 0.0), reverse=True)

    top_reduced = [
        r for r in diff_records
        if r.get("change_type") in ("Qty decrease", "Removed item")
        and "Transfer" not in (r.get("flags") or "")
    ]
    top_reduced.sort(key=lambda x: abs(float(x.get("qty_effect") or 0.0)), reverse=True)

    new_items = [r for r in diff_records if r.get("change_type") == "New item"]
    removed_items = [r for r in diff_records if r.get("change_type") == "Removed item"]
    transfers = [r for r in diff_records if "Transfer" in (r.get("flags") or "")]
    price_changes = [r for r in diff_records if r.get("change_type") == "Price change" or abs(float(r.get("price_effect") or 0.0)) > 0]

    # Top Gaps
    gap_table = gap_data.get("gap_table", [])
    gap_table_sorted = sorted(gap_table, key=lambda x: abs(float(x.get("difference_value") or 0.0)), reverse=True)
    top_gap_items = [g for g in gap_table_sorted if abs(float(g.get("difference_value") or 0.0)) > 0.0][:25]

    # 9. Points for Discussion (Top 5 ranked by Rs impact x confidence)
    discussion_points = []

    # Source A: Large gaps
    for g in top_gap_items[:3]:
        diff_val = abs(float(g.get("difference_value") or 0.0))
        if diff_val >= 10000:
            status_desc = g.get("status", "Store vs PTW gap")
            discussion_points.append(
                {
                    "title": f"Gap in {g.get('item_key')}: {status_desc} of {format_inr(diff_val)}",
                    "impact_rs": diff_val,
                    "category": "Store vs PTW Gap",
                    "recommendation": "Reconcile store issue vouchers against maintenance permit log sheets.",
                    "owner": "",
                    "status": "To review",
                }
            )

    # Source B: Additions above Rs 2L
    for a in top_added:
        val = float(a.get("qty_effect") or 0.0)
        if val >= plant_head_threshold:
            discussion_points.append(
                {
                    "title": f"High-value stock addition: {a.get('item_key')} added ({format_inr(val)})",
                    "impact_rs": val,
                    "category": "Major Addition",
                    "recommendation": "Verify plant head sanction and consumption schedule.",
                    "owner": "",
                    "status": "To review",
                }
            )

    # Source C: Contradicted savings entries
    entries_df = savings.get("entries_df")
    if entries_df is not None and not entries_df.empty:
        contra = entries_df[entries_df["data_check"] == "contradicted"]
        for _, c_row in contra.iterrows():
            amt = float(c_row["amount_paise"] or 0) / 100.0
            discussion_points.append(
                {
                    "title": f"Savings entry #{c_row['id']} contradicted: stock subsequently rose for {c_row.get('item_key')}",
                    "impact_rs": amt,
                    "category": "Contradicted Saving",
                    "recommendation": "Confirm whether avoided order was subsequently placed in ERP.",
                    "owner": "",
                    "status": "To review",
                }
            )

    # Source D: High confidence opportunities
    opp_df = savings.get("opportunities_df")
    if opp_df is not None and not opp_df.empty:
        open_high = opp_df[(opp_df["status"] == "open") & (opp_df["confidence"] == "high")]
        for _, o_row in open_high.iterrows():
            val = float(o_row["avoidable_value_paise"] or 0) / 100.0
            if val >= 50000:
                discussion_points.append(
                    {
                        "title": f"High-confidence opportunity: {o_row.get('item_key')} avoidable value {format_inr(val)}",
                        "impact_rs": val,
                        "category": "Opportunity",
                        "recommendation": "Review replenishment schedule to cancel or defer purchase.",
                        "owner": "",
                        "status": "To review",
                    }
                )

    # Source E: Data warnings
    if periods_differ:
        discussion_points.append(
            {
                "title": f"Reporting periods differ: Inventory ({inv_as_of}) vs PTW ({ptw_as_of})",
                "impact_rs": 0.0,
                "category": "Data Quality",
                "recommendation": "Request synchronized cutoff dates from Operations and Storage.",
                "owner": "",
                "status": "To review",
            }
        )

    if low_match_coverage:
        discussion_points.append(
            {
                "title": f"Low match coverage ({match_coverage_pct:.1f}%): over 50% of PTW lines unmatched",
                "impact_rs": float(gap_data.get("total_ptw_val") or 0.0),
                "category": "Match Coverage",
                "recommendation": "Review and confirm probable item mappings in Reconciliation view.",
                "owner": "",
                "status": "To review",
            }
        )

    # Sort discussion points by impact_rs desc and keep top 5
    discussion_points.sort(key=lambda x: x["impact_rs"], reverse=True)
    top_5_discussion = discussion_points[:5]

    # 10. Auto-Generated Executive Summary Sentences (3 to 5 data-driven templates)
    closing_val = float(diff_data["closing_val"])
    opening_val = float(diff_data["opening_val"])
    net_chg = closing_val - opening_val
    pct_chg = ((net_chg / opening_val) * 100.0) if opening_val > 0 else 0.0

    exec_sentences = []
    # Sentence 1: Inventory movement
    if is_baseline:
        exec_sentences.append(
            f"Total inventory was established at {format_inr(closing_val)} across active catalog items as the baseline reporting period."
        )
    else:
        exec_sentences.append(
            f"Total inventory closed at {format_inr(closing_val)}, representing a net change of {format_inr(net_chg)} ({pct_chg:+.1f}%) compared to {base_period}."
        )

    # Sentence 2: Store vs PTW gap
    total_gap = gap_data.get("total_gap_val", 0.0)
    store_val = gap_data.get("total_store_val", 0.0)
    ptw_val = gap_data.get("total_ptw_val", 0.0)
    if ptw_id:
        if abs(total_gap) > 1000:
            exec_sentences.append(
                f"The net gap between store reductions ({format_inr(store_val)}) and recorded PTW consumption ({format_inr(ptw_val)}) stands at {format_inr(total_gap)}."
            )
        else:
            exec_sentences.append(
                "Store reductions and recorded PTW maintenance usage were aligned within defined tolerance limits."
            )

    # Sentence 3: Opportunities
    opp_tot_rs = savings["opportunities"]["total_rs"]
    opp_tot_cnt = savings["opportunities"]["total_count"]
    if opp_tot_cnt > 0:
        exec_sentences.append(
            f"Identified {opp_tot_cnt} cost avoidance opportunities totaling {format_inr(opp_tot_rs)} across additions exceeding target inventory cover."
        )
    elif not is_baseline:
        exec_sentences.append(
            "All additions during the period complied with established inventory cover targets."
        )

    # Sentence 4: Savings actioned / verified
    ver_rs = savings.get("verified_rs", 0.0)
    act_rs = savings.get("actioned_rs", 0.0)
    if (ver_rs + act_rs) > 0:
        exec_sentences.append(
            f"Cost avoidance tracking records {format_inr(ver_rs)} in verified savings and {format_inr(act_rs)} actioned awaiting verification."
        )
    else:
        exec_sentences.append(
            "No cost avoidance entries were actioned or verified during this monthly reporting cycle."
        )

    # Sentence 5: Excess stock exposure
    excess_rs = savings.get("excess_stock_rs", 0.0)
    if excess_rs > 0:
        exec_sentences.append(
            f"Working capital exposure in non-critical excess stock is currently estimated at {format_inr(excess_rs)} above the {months_of_cover:.0f}-month cover target."
        )

    # Limit to 5 sentences
    exec_sentences = exec_sentences[:5]

    # Target FY
    target_d = datetime.strptime(inv_as_of[:10], "%Y-%m-%d").date() if inv_as_of else date.today()
    fy_label = get_fy_label(target_d, fy_start_month=fy_start_m)

    # 11. Compile Canonical Data Model
    return {
        # Cover metadata
        "cover": {
            "plant_name": plant_name,
            "site_name": site_name,
            "period": period,
            "fy_label": fy_label,
            "comparison_label": comparison_label,
            "comparison_window": comparison_window,
            "generated_at_ist": now_ist.strftime("%d-%b-%Y %I:%M %p IST"),
            "version": inv_ver,
            "generated_by": user,
            "inv_filename": inv_fname,
            "inv_hash8": inv_hash8,
            "ptw_filename": ptw_fname,
            "ptw_hash8": ptw_hash8,
            "fingerprint": fingerprint,
            "is_baseline": is_baseline,
            "baseline_period": base_period,
        },
        # Executive Summary
        "executive_summary": {
            "closing_value": closing_val,
            "opening_value": opening_val,
            "net_change_rs": net_chg,
            "net_change_pct": pct_chg,
            "added_value": diff_data["added_val"],
            "reduced_value": diff_data["reduced_val"],
            "revaluation_value": diff_data["revaluation_val"],
            "reconciled": diff_data.get("reconciled", True),
            "store_vs_ptw_gap": total_gap,
            "store_value": store_val,
            "ptw_value": ptw_val,
            "savings_verified": ver_rs,
            "savings_actioned": act_rs,
            "open_opportunities_rs": opp_tot_rs,
            "open_opportunities_count": opp_tot_cnt,
            "excess_stock_rs": excess_rs,
            "match_coverage_pct": match_coverage_pct,
            "confirmed_lines": confirmed_lines,
            "probable_lines": probable_lines,
            "unmatched_lines": unmatched_lines,
            "ptw_lines_total": ptw_lines_total,
            "sentences": exec_sentences,
        },
        # Points for discussion
        "discussion_points": top_5_discussion,
        # Section detail tables
        "inventory_movement": {
            "waterfall": {
                "opening": opening_val,
                "added": diff_data["added_val"],
                "reduced": diff_data["reduced_val"],
                "revaluation": diff_data["revaluation_val"],
                "closing": closing_val,
            },
            "top_added": top_added[:50],
            "top_reduced": top_reduced[:50],
            "new_items": new_items[:50],
            "removed_items": removed_items[:50],
            "transfers": transfers[:50],
            "price_changes": price_changes[:50],
        },
        "store_vs_ptw": {
            "top_gap_items": top_gap_items,
            "total_store_val": store_val,
            "total_ptw_val": ptw_val,
            "total_gap_val": total_gap,
            "confirmed_gap_val": gap_data.get("confirmed_gap_val", 0.0),
            "probable_gap_val": gap_data.get("probable_gap_val", 0.0),
            "unmatched_ptw_count": gap_data.get("unmatched_ptw_count", 0),
        },
        "savings_and_opportunities": savings,
        "needs_review": {
            "unvalued_additions": [r for r in top_added if float(r.get("price_new") or 0.0) <= 0],
            "high_value_additions": [r for r in top_added if float(r.get("qty_effect") or 0.0) >= plant_head_threshold],
            "contradicted_entries": [
                r.to_dict() for _, r in entries_df[entries_df["data_check"] == "contradicted"].iterrows()
            ] if entries_df is not None and not entries_df.empty else [],
            "open_gaps": [g for g in top_gap_items if g.get("review_status") == "Open"],
        },
        "data_quality": {
            "rejected_count": rejected_count,
            "match_coverage_pct": match_coverage_pct,
            "periods_differ": periods_differ,
            "inv_stale": inv_stale,
            "ptw_stale": ptw_stale,
            "low_match_coverage": low_match_coverage,
        },
        "methodology": {
            "months_of_cover": months_of_cover,
            "plant_head_threshold": plant_head_threshold,
            "limitations": [
                "Monthly snapshots record net change over the comparison window; intermediate intra-month receipts and issues are not observed.",
                "PTW records only reflect permitted maintenance work; non-permit operational consumption and direct store issues are not captured.",
                "Probable string matches are statistical indications; only user-confirmed and part-number regex matches represent verified consumption.",
                "Cost avoidance reflects avoided or cancelled procurement; it does not constitute a cash reduction or profit without Finance confirmation.",
            ],
            "footer_text": footer_text,
        },
    }
