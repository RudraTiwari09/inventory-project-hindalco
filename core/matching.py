"""
Inventory x PTW Matching and Gap Analysis Engine.
Implements 3-tier matching (regex code, confirmed matches, RapidFuzz with equipment bonus),
window-based comparison, tolerance checks, and cumulative gap tracking per Section 11.
"""

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
from sqlalchemy import text

from core.settings import get_setting_float
from core.audit import log_audit_event

logger = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz
    def _fuzzy_ratio(a: str, b: str) -> float:
        return float(fuzz.token_sort_ratio(str(a).lower(), str(b).lower()))
except ImportError:
    def _fuzzy_ratio(a: str, b: str) -> float:
        sa = set(str(a).lower().split())
        sb = set(str(b).lower().split())
        if not sa or not sb:
            return 0.0
        return 100.0 * len(sa & sb) / max(len(sa), len(sb))


def match_ptw_lines_to_inventory(
    engine,
    ptw_upload_id: int,
    inv_upload_id: Optional[int],
) -> Dict[str, Any]:
    """
    Execute 3-tier matching on all ptw_lines for a given ptw_upload_id against
    inventory_items in inv_upload_id:
    Tier 1: Material code regex match (100% confidence)
    Tier 2: User confirmed matches (100% confidence)
    Tier 3: RapidFuzz on description (+10 bonus for equipment tag match)
    Updates ptw_lines table.
    """
    if not inv_upload_id:
        return {"matched": 0, "auto": 0, "probable": 0, "unmatched": 0}

    with engine.connect() as conn:
        # Load inventory items
        inv_df = pd.read_sql_query(
            """
            SELECT item_key, material_code, description, equipment_tag, unit_price
            FROM inventory_items
            WHERE upload_id = :uid
            """,
            con=engine,
            params={"uid": inv_upload_id},
        )

        # Load PTW lines with permit equipment tag
        ptw_df = pd.read_sql_query(
            """
            SELECT l.id, l.permit_key, l.raw_description, l.cleaned_description,
                   p.equipment_tag
            FROM ptw_lines l
            JOIN ptw_permits p ON l.permit_key = p.permit_key AND l.upload_id = p.upload_id
            WHERE l.upload_id = :uid
            """,
            con=engine,
            params={"uid": ptw_upload_id},
        )

        # Load user confirmed matches
        confirmed_rows = conn.execute(
            text("SELECT ptw_description, inventory_item_key FROM user_confirmed_matches")
        ).fetchall()
        confirmed_map = {r[0].lower().strip(): r[1] for r in confirmed_rows}

    if inv_df.empty or ptw_df.empty:
        return {"matched": 0, "auto": 0, "probable": 0, "unmatched": len(ptw_df)}

    # Build code lookup map and regex patterns
    known_codes = [c for c in inv_df["material_code"].unique() if len(str(c)) >= 3]
    code_to_key = {}
    for _, r in inv_df.iterrows():
        code_to_key[r["material_code"]] = r["item_key"]

    # Precompile regex for known codes (word boundary)
    escaped_codes = [re.escape(str(c)) for c in known_codes]
    code_pattern = re.compile(r"\b(" + "|".join(escaped_codes) + r")\b", re.IGNORECASE) if escaped_codes else None

    # Precompute clean inventory descriptions
    inv_records = []
    for _, r in inv_df.iterrows():
        inv_records.append({
            "item_key": r["item_key"],
            "mcode": r["material_code"],
            "desc_clean": str(r["description"]).lower().strip(),
            "eq_tag": str(r.get("equipment_tag", "")).lower().strip(),
        })

    updates = []
    auto_count = 0
    probable_count = 0
    unmatched_count = 0

    for _, prow in ptw_df.iterrows():
        line_id = prow["id"]
        raw_desc = str(prow["raw_description"])
        clean_desc = str(prow["cleaned_description"]).lower().strip()
        ptw_tag = str(prow.get("equipment_tag", "")).lower().strip()

        matched_key = None
        method = "unmatched"
        confidence = 0.0
        status = "Unmatched"

        # Tier 1: Material code regex match
        if code_pattern:
            m = code_pattern.search(raw_desc)
            if m:
                found_code = m.group(1).upper()
                # Find matching key
                for c, ikey in code_to_key.items():
                    if c.upper() == found_code:
                        matched_key = ikey
                        method = "code_regex"
                        confidence = 100.0
                        status = "Auto"
                        break

        # Tier 2: User confirmed matches
        if not matched_key and clean_desc in confirmed_map:
            matched_key = confirmed_map[clean_desc]
            method = "confirmed"
            confidence = 100.0
            status = "Auto"

        # Tier 3: RapidFuzz description match with equipment tag bonus
        if not matched_key:
            best_score = 0.0
            best_key = None

            for irec in inv_records:
                score = _fuzzy_ratio(clean_desc, irec["desc_clean"])
                # +10 bonus if equipment tag matches
                if ptw_tag and irec["eq_tag"] and ptw_tag == irec["eq_tag"]:
                    score = min(100.0, score + 10.0)

                if score > best_score:
                    best_score = score
                    best_key = irec["item_key"]

            if best_score >= 90.0:
                matched_key = best_key
                method = "fuzzy_bonus"
                confidence = best_score
                status = "Auto"
            elif best_score >= 70.0:
                matched_key = best_key
                method = "fuzzy_text"
                confidence = best_score
                status = "Probable"
            else:
                matched_key = None
                method = "unmatched"
                confidence = best_score
                status = "Unmatched"

        if status == "Auto":
            auto_count += 1
        elif status == "Probable":
            probable_count += 1
        else:
            unmatched_count += 1

        updates.append({
            "lid": line_id,
            "mkey": matched_key,
            "method": method,
            "conf": confidence,
            "stat": status,
        })

    # Persist updates to ptw_lines
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE ptw_lines
                SET matched_item_key = :mkey,
                    match_method = :method,
                    match_confidence = :conf,
                    status = :stat
                WHERE id = :lid
                """
            ),
            updates,
        )

    return {
        "matched": auto_count + probable_count,
        "auto": auto_count,
        "probable": probable_count,
        "unmatched": unmatched_count,
    }


def compute_store_vs_ptw_gap(
    engine,
    inv_upload_id: int,
    ptw_upload_id: Optional[int],
    start_date_exclusive: str,
    end_date_inclusive: str,
) -> Dict[str, Any]:
    """
    Compute Store Reduction vs PTW Usage gap per item for the comparison window
    (start_date_exclusive, end_date_inclusive].
    Returns gap rows, split totals (Confirmed/Probable/Unmatched), and summary KPIs.
    """
    gap_tol_pct = get_setting_float(engine, "gap_tolerance_percent", default=5.0)
    gap_tol_units = get_setting_float(engine, "gap_tolerance_units", default=1.0)

    # 1. Fetch store reductions from inventory_diff (excluding transfers)
    with engine.connect() as conn:
        diff_rows = conn.execute(
            text(
                """
                SELECT item_key, ABS(qty_delta) as store_reduced_qty, price_old
                FROM inventory_diff
                WHERE upload_id = :uid AND change_type IN ('Qty decrease', 'Removed item')
                """
            ),
            {"uid": inv_upload_id},
        ).fetchall()

        store_reductions = {r[0]: {"qty": float(r[1]), "price": float(r[2]) if r[2] else 0.0} for r in diff_rows}

        # 2. Fetch PTW usage in comparison window with Closed status
        ptw_usage: Dict[str, Dict[str, Any]] = {}
        unmatched_ptw_lines = []

        if ptw_upload_id:
            ptw_rows = conn.execute(
                text(
                    """
                    SELECT l.matched_item_key, l.quantity, l.status, l.raw_description, p.permit_key
                    FROM ptw_lines l
                    JOIN ptw_permits p ON l.permit_key = p.permit_key AND l.upload_id = p.upload_id
                    WHERE l.upload_id = :puid
                      AND p.status = 'Closed'
                      AND p.permit_start > :start_d
                      AND p.permit_start <= :end_d
                    """
                ),
                {
                    "puid": ptw_upload_id,
                    "start_d": start_date_exclusive,
                    "end_d": end_date_inclusive,
                },
            ).fetchall()

            for mkey, qty, stat, desc, pkey in ptw_rows:
                q = float(qty or 0.0)
                if not mkey or stat == "Unmatched":
                    unmatched_ptw_lines.append({"permit_key": pkey, "desc": desc, "qty": q})
                    continue

                if mkey not in ptw_usage:
                    ptw_usage[mkey] = {"total_qty": 0.0, "auto_qty": 0.0, "probable_qty": 0.0}
                ptw_usage[mkey]["total_qty"] += q
                if stat == "Auto":
                    ptw_usage[mkey]["auto_qty"] += q
                else:
                    ptw_usage[mkey]["probable_qty"] += q

        # 3. Load gap review comments
        reviews = conn.execute(
            text("SELECT item_key, status, comment FROM gap_review")
        ).fetchall()
        review_map = {r[0]: {"status": r[1], "comment": r[2]} for r in reviews}

    # 4. Merge items
    all_keys = sorted(set(store_reductions.keys()) | set(ptw_usage.keys()))
    gap_table = []
    total_store_val = 0.0
    total_ptw_val = 0.0
    total_gap_val = 0.0

    confirmed_gap_val = 0.0
    probable_gap_val = 0.0

    for k in all_keys:
        s_info = store_reductions.get(k, {"qty": 0.0, "price": 0.0})
        p_info = ptw_usage.get(k, {"total_qty": 0.0, "auto_qty": 0.0, "probable_qty": 0.0})

        s_qty = s_info["qty"]
        p_qty = p_info["total_qty"]
        price = s_info["price"]

        diff_qty = s_qty - p_qty
        diff_val = diff_qty * price

        total_store_val += s_qty * price
        total_ptw_val += p_qty * price
        total_gap_val += diff_val

        if p_info["auto_qty"] > 0:
            confirmed_gap_val += diff_val
        elif p_info["probable_qty"] > 0:
            probable_gap_val += diff_val

        # Status classification
        status = "Within tolerance"
        if s_qty == 0.0 and p_qty > 0.0:
            status = "PTW only"
        elif s_qty > 0.0 and p_qty == 0.0:
            status = "Inventory only"
        elif abs(diff_qty) > gap_tol_units and (s_qty > 0 and (abs(diff_qty) / s_qty) * 100.0 > gap_tol_pct):
            status = "Store reduced more than PTW" if diff_qty > 0 else "PTW used more than store reduced"

        rev = review_map.get(k, {"status": "Open", "comment": ""})

        gap_table.append({
            "item_key": k,
            "store_reduced_qty": s_qty,
            "ptw_qty": p_qty,
            "difference_qty": diff_qty,
            "difference_value": diff_val,
            "unit_price": price,
            "status": status,
            "review_status": rev["status"],
            "comment": rev["comment"],
        })

    return {
        "gap_table": gap_table,
        "total_store_val": total_store_val,
        "total_ptw_val": total_ptw_val,
        "total_gap_val": total_gap_val,
        "confirmed_gap_val": confirmed_gap_val,
        "probable_gap_val": probable_gap_val,
        "unmatched_ptw_count": len(unmatched_ptw_lines),
    }


def confirm_match(
    engine,
    ptw_description: str,
    inventory_item_key: str,
    user: str = "admin",
) -> None:
    """
    Save a user-confirmed match and log to audit trail.
    """
    now = datetime.now(timezone.utc).isoformat()
    clean_desc = ptw_description.lower().strip()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO user_confirmed_matches
                    (ptw_description, inventory_item_key, confirmed_by, confirmed_at, notes)
                VALUES
                    (:desc, :ikey, :u, :now, 'Manual user confirmation')
                ON CONFLICT(ptw_description) DO UPDATE SET
                    inventory_item_key = excluded.inventory_item_key,
                    confirmed_by = excluded.confirmed_by,
                    confirmed_at = excluded.confirmed_at
                """
            ),
            {"desc": clean_desc, "ikey": inventory_item_key, "u": user, "now": now},
        )

    log_audit_event(
        engine,
        event_type="match_confirmed",
        details=f"Confirmed '{clean_desc}' -> '{inventory_item_key}'",
        username=user,
    )

    try:
        from core.savings import run_data_check
        run_data_check(engine)
    except Exception:
        pass

