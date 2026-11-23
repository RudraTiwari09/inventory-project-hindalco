"""
Savings Engine for Recon Dashboard.
Implements cost avoidance opportunity detection, manual entry lifecycle,
data validation checks, and savings summary reporting.
"""

from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
from sqlalchemy import text

from core.audit import log_audit_event
from core.auth import is_admin, require_admin
from core.settings import (
    get_setting,
    get_setting_float,
    get_setting_int,
)

logger = logging.getLogger(__name__)


def get_fy_label(d: date, fy_start_month: int = 4) -> str:
    """
    Format date into Financial Year string, e.g. 'FY 2026-27'.
    Boundary: March 31 falls into the ending year, April 1 into the new year.
    """
    year = d.year if d.month >= fy_start_month else d.year - 1
    next_year_short = str(year + 1)[-2:]
    return f"FY {year}-{next_year_short}"


def get_fy_date_bounds(year_label: str, fy_start_month: int = 4) -> Tuple[date, date]:
    """
    Convert 'FY 2026-27' into (start_date, end_date).
    Example: FY 2026-27 with start_month=4 -> (2026-04-01, 2027-03-31).
    """
    parts = year_label.replace("FY", "").strip().split("-")
    base_year = int(parts[0])
    start_d = date(base_year, fy_start_month, 1)
    end_year = base_year + 1 if fy_start_month > 1 else base_year
    end_month = fy_start_month - 1 if fy_start_month > 1 else 12
    _, last_day = monthrange(end_year, end_month)
    end_d = date(end_year, end_month, last_day)
    return start_d, end_d


def parse_period_to_date(period_str: str) -> Optional[date]:
    """Convert 'Oct 2026' or '2026-10' into date (first of month)."""
    if not period_str:
        return None
    try:
        dt = datetime.strptime(period_str[:10], "%Y-%m-%d").date()
        return dt.replace(day=1)
    except Exception:
        pass
    for fmt in ("%b %Y", "%B %Y", "%Y-%m"):
        try:
            return datetime.strptime(period_str.strip(), fmt).date().replace(day=1)
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 1. Opportunity & Excess Stock Engine
# ─────────────────────────────────────────────────────────────────────────────


def compute_opportunities_and_excess(
    engine,
    period: str,
) -> Tuple[int, int]:
    """
    Compute savings opportunities and excess stock for a published inventory period.
    Preserves converted and dismissed status of existing rows for this period.

    Returns (opp_count, excess_items_count).
    """
    # Read settings
    months_of_cover = Decimal(str(get_setting_float(engine, "months_of_cover", default=6.0)))
    min_history = get_setting_int(engine, "min_history_months", default=3)
    history_window = get_setting_int(engine, "history_window_months", default=12)
    threshold_rs = get_setting_float(engine, "plant_head_threshold", default=200000.0)

    with engine.connect() as conn:
        # Find active inventory upload for this period
        inv_upload = conn.execute(
            text(
                """
                SELECT id, as_of_date, version FROM uploads
                WHERE dataset = 'inventory' AND period = :p AND status = 'active'
                ORDER BY version DESC, id DESC LIMIT 1
                """
            ),
            {"p": period},
        ).fetchone()

        if not inv_upload:
            logger.warning("No active inventory upload found for period %s", period)
            return 0, 0

        inv_uid, as_of_str, inv_ver = inv_upload

        # Preserve existing opportunity decisions for this period
        existing_decisions = {}
        prev_rows = conn.execute(
            text(
                """
                SELECT item_key, status, dismiss_reason, converted_entry_id
                FROM savings_opportunities
                WHERE period = :p
                """
            ),
            {"p": period},
        ).fetchall()
        for r in prev_rows:
            existing_decisions[r[0]] = (r[1], r[2], r[3])

        # Get the history window of published active inventory uploads up to this as_of_date
        hist_uploads = conn.execute(
            text(
                """
                SELECT id, period, as_of_date FROM uploads
                WHERE dataset = 'inventory' AND status = 'active' AND as_of_date <= :as_of
                ORDER BY as_of_date DESC LIMIT :win
                """
            ),
            {"as_of": as_of_str, "win": history_window},
        ).fetchall()

        hist_uids = [h[0] for h in hist_uploads]

        # 1. Calculate store reductions per item across historical periods (exclude transfers)
        store_red_rows = conn.execute(
            text(
                """
                SELECT upload_id, item_key, ABS(qty_delta) as red_qty
                FROM inventory_diff
                WHERE upload_id IN :uids
                  AND change_type IN ('Qty decrease', 'Removed item')
                  AND (flags NOT LIKE '%Transfer%' OR flags IS NULL)
                """
            ),
            {"uids": tuple(hist_uids) if hist_uids else (-1,)},
        ).fetchall()

        store_red_df = pd.DataFrame(store_red_rows, columns=["upload_id", "item_key", "red_qty"])
        if store_red_df.empty:
            store_red_df = pd.DataFrame(columns=["upload_id", "item_key", "red_qty"])

        # 2. Calculate matched PTW closed usage (Confirmed and Auto only, exclude Probable)
        ptw_usage_rows = conn.execute(
            text(
                """
                SELECT p.period, l.matched_item_key as item_key, SUM(l.quantity) as ptw_qty
                FROM ptw_lines l
                JOIN ptw_permits p ON l.permit_key = p.permit_key AND l.upload_id = p.upload_id
                JOIN uploads u ON p.upload_id = u.id
                WHERE u.status = 'active'
                  AND p.status = 'Closed'
                  AND l.status IN ('Confirmed', 'Auto')
                  AND l.matched_item_key IS NOT NULL
                GROUP BY p.period, l.matched_item_key
                """
            )
        ).fetchall()

        ptw_usage_df = pd.DataFrame(ptw_usage_rows, columns=["period", "item_key", "ptw_qty"])
        if ptw_usage_df.empty:
            ptw_usage_df = pd.DataFrame(columns=["period", "item_key", "ptw_qty"])

        # 3. Item presence across historical uploads
        item_presence_rows = conn.execute(
            text(
                """
                SELECT upload_id, item_key
                FROM inventory_items
                WHERE upload_id IN :uids
                """
            ),
            {"uids": tuple(hist_uids) if hist_uids else (-1,)},
        ).fetchall()

        presence_df = pd.DataFrame(item_presence_rows, columns=["upload_id", "item_key"])

        # 4. Current inventory items for this upload
        curr_items_rows = conn.execute(
            text(
                """
                SELECT item_key, material_code, description, department,
                       equipment_tag, qty, unit_price, price_carried, criticality
                FROM inventory_items
                WHERE upload_id = :uid
                """
            ),
            {"uid": inv_uid},
        ).fetchall()

        curr_df = pd.DataFrame(
            curr_items_rows,
            columns=[
                "item_key",
                "material_code",
                "description",
                "department",
                "equipment_tag",
                "qty",
                "unit_price",
                "price_carried",
                "criticality",
            ],
        )

        # 5. Inventory diff additions for this upload
        diff_rows = conn.execute(
            text(
                """
                SELECT item_key, change_type, qty_old, qty_new, qty_delta,
                       price_old, price_new, flags
                FROM inventory_diff
                WHERE upload_id = :uid
                  AND change_type IN ('Qty increase', 'New item')
                  AND (flags NOT LIKE '%Transfer%' OR flags IS NULL)
                  AND (flags NOT LIKE '%Price-only%' OR flags IS NULL)
                  AND (flags NOT LIKE '%UoM changed%' OR flags IS NULL)
                  AND (flags NOT LIKE '%Possible re-code%' OR flags IS NULL)
                  AND (flags NOT LIKE '%Moved-but-net-zero%' OR flags IS NULL)
                """
            ),
            {"uid": inv_uid},
        ).fetchall()

        diff_df = pd.DataFrame(
            diff_rows,
            columns=[
                "item_key",
                "change_type",
                "qty_old",
                "qty_new",
                "qty_delta",
                "price_old",
                "price_new",
                "flags",
            ],
        )

    # Calculate average consumption per item
    # Map upload_id to period for store reductions
    uid_to_period = {h[0]: h[1] for h in hist_uploads}
    if not store_red_df.empty:
        store_red_df["period"] = store_red_df["upload_id"].map(uid_to_period)

    # Merge store reductions and PTW usage per (period, item_key)
    period_consumption = {}
    if not store_red_df.empty:
        for _, row in store_red_df.iterrows():
            k = (row["period"], row["item_key"])
            period_consumption[k] = max(period_consumption.get(k, 0.0), float(row["red_qty"] or 0.0))

    if not ptw_usage_df.empty:
        for _, row in ptw_usage_df.iterrows():
            k = (row["period"], row["item_key"])
            period_consumption[k] = max(period_consumption.get(k, 0.0), float(row["ptw_qty"] or 0.0))

    # Calculate presence count & total consumption per item
    item_stats = {}
    if not presence_df.empty:
        for item_k, group in presence_df.groupby("item_key"):
            periods_present = len(group["upload_id"].unique())
            periods_list = [uid_to_period.get(uid) for uid in group["upload_id"].unique() if uid in uid_to_period]
            total_cons = sum(period_consumption.get((p, item_k), 0.0) for p in periods_list)
            item_stats[item_k] = {
                "periods_present": periods_present,
                "total_consumption": total_cons,
                "avg_monthly_consumption": (total_cons / periods_present) if periods_present > 0 else 0.0,
            }

    # ── Compute Opportunities ────────────────────────────────────────────────
    opportunities = []
    if not diff_df.empty:
        for _, d_row in diff_df.iterrows():
            ikey = d_row["item_key"]
            added_q = Decimal(str(d_row["qty_delta"] or 0.0))
            closing_q = Decimal(str(d_row["qty_new"] or 0.0))
            baseline_q = Decimal(str(d_row["qty_old"] or 0.0))

            stats = item_stats.get(ikey, {"periods_present": 1, "avg_monthly_consumption": 0.0, "total_consumption": 0.0})
            periods_pres = stats["periods_present"]
            monthly_cons = Decimal(str(round(stats["avg_monthly_consumption"], 4)))
            target_q = monthly_cons * months_of_cover

            # Match item metadata
            item_meta = curr_df[curr_df["item_key"] == ikey]
            crit = item_meta["criticality"].iloc[0] if not item_meta.empty and item_meta["criticality"].iloc[0] else ""
            is_critical = str(crit).strip().lower() == "high"

            # Check history requirement
            has_enough_hist = periods_pres >= min_history

            # Avoidable qty = min(added_qty, max(0, closing_qty - target_qty))
            excess_after = max(Decimal("0"), closing_q - target_q)
            avoidable_q = min(added_q, excess_after) if has_enough_hist else Decimal("0")

            # Valuation = avoidable_qty * min(baseline price, new price)
            p_old = Decimal(str(d_row["price_old"] or 0.0)) if d_row["price_old"] else Decimal("0")
            p_new = Decimal(str(d_row["price_new"] or 0.0)) if d_row["price_new"] else Decimal("0")
            if p_old > 0 and p_new > 0:
                eval_p = min(p_old, p_new)
            elif p_new > 0:
                eval_p = p_new
            else:
                eval_p = p_old

            is_carried = False
            if not item_meta.empty and item_meta["price_carried"].iloc[0]:
                is_carried = True

            flag_list = []
            if is_critical:
                flag_list.append("Critical spare")
            if not has_enough_hist:
                flag_list.append("Not enough history")
            if is_carried or eval_p <= 0:
                flag_list.append("Unvalued")

            # Avoidable valuation (integer paise)
            if is_critical or not has_enough_hist or eval_p <= 0:
                avoidable_paise = 0
            else:
                avoidable_paise = int(round(avoidable_q * eval_p * 100))

            if avoidable_paise >= int(round(threshold_rs * 100)):
                flag_list.append("Above Rs 2L")

            # Confidence
            if is_critical:
                confidence = "low"
            elif periods_pres >= 6 and stats["total_consumption"] > 0:
                confidence = "high"
            elif periods_pres >= 3 and stats["total_consumption"] > 0:
                confidence = "medium"
            else:
                confidence = "low"

            # Months cover after addition
            cover_after = float(closing_q / monthly_cons) if monthly_cons > 0 else (999.0 if closing_q > 0 else 0.0)

            # Preserve existing decision if present
            prev_status, prev_reason, prev_conv_id = existing_decisions.get(ikey, ("open", None, None))

            # Only record opportunities that had excess stock or addition to review
            if avoidable_q > 0 or not has_enough_hist:
                opportunities.append(
                    {
                        "period": period,
                        "upload_id": inv_uid,
                        "item_key": ikey,
                        "added_qty": float(added_q),
                        "baseline_qty": float(baseline_q),
                        "closing_qty": float(closing_q),
                        "monthly_consumption_est": float(monthly_cons),
                        "target_qty": float(target_q),
                        "months_cover_after": float(cover_after),
                        "avoidable_qty": float(avoidable_q),
                        "unit_price": float(eval_p),
                        "avoidable_value_paise": avoidable_paise,
                        "confidence": confidence,
                        "flags": ", ".join(flag_list) if flag_list else None,
                        "status": prev_status,
                        "dismiss_reason": prev_reason,
                        "converted_entry_id": prev_conv_id,
                    }
                )

    # ── Compute Excess Stock ─────────────────────────────────────────────────
    excess_items = []
    if not curr_df.empty:
        for _, c_row in curr_df.iterrows():
            ikey = c_row["item_key"]
            c_crit = str(c_row["criticality"] or "").strip().lower()
            if c_crit == "high":
                continue  # Never flag critical spares as excess stock

            stats = item_stats.get(ikey, {"periods_present": 1, "avg_monthly_consumption": 0.0})
            if stats["periods_present"] < min_history:
                continue  # Needs at least min_history_months

            monthly_cons = Decimal(str(round(stats["avg_monthly_consumption"], 4)))
            target_q = monthly_cons * months_of_cover
            current_q = Decimal(str(c_row["qty"] or 0.0))
            excess_q = max(Decimal("0"), current_q - target_q)

            if excess_q > 0:
                u_p = Decimal(str(c_row["unit_price"] or 0.0))
                excess_paise = int(round(excess_q * u_p * 100))
                excess_items.append(
                    {
                        "period": period,
                        "item_key": ikey,
                        "qty": float(current_q),
                        "target_qty": float(target_q),
                        "excess_qty": float(excess_q),
                        "excess_value_paise": excess_paise,
                    }
                )

    # Persist in DB atomically
    with engine.begin() as conn:
        # 1. Update savings_opportunities
        conn.execute(text("DELETE FROM savings_opportunities WHERE period = :p"), {"p": period})
        for opp in opportunities:
            conn.execute(
                text(
                    """
                    INSERT INTO savings_opportunities (
                        period, upload_id, item_key, added_qty, baseline_qty, closing_qty,
                        monthly_consumption_est, target_qty, months_cover_after, avoidable_qty,
                        unit_price, avoidable_value_paise, confidence, flags, status,
                        dismiss_reason, converted_entry_id
                    ) VALUES (
                        :period, :upload_id, :item_key, :added_qty, :baseline_qty, :closing_qty,
                        :monthly_consumption_est, :target_qty, :months_cover_after, :avoidable_qty,
                        :unit_price, :avoidable_value_paise, :confidence, :flags, :status,
                        :dismiss_reason, :converted_entry_id
                    )
                    """
                ),
                opp,
            )

        # 2. Update excess_stock
        conn.execute(text("DELETE FROM excess_stock WHERE period = :p"), {"p": period})
        for exc in excess_items:
            conn.execute(
                text(
                    """
                    INSERT INTO excess_stock (
                        period, item_key, qty, target_qty, excess_qty, excess_value_paise
                    ) VALUES (
                        :period, :item_key, :qty, :target_qty, :excess_qty, :excess_value_paise
                    )
                    """
                ),
                exc,
            )

    logger.info(
        "Opportunities and excess computed for %s: %d opportunities, %d excess items",
        period,
        len(opportunities),
        len(excess_items),
    )
    return len(opportunities), len(excess_items)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Manual Entry Lifecycle & Governance
# ─────────────────────────────────────────────────────────────────────────────


def create_savings_entry(
    engine,
    action_date: date,
    entry_type: str,
    requested_qty: float,
    avoided_qty: float,
    unit_price: float,
    created_by: str,
    item_key: Optional[str] = None,
    material_code: Optional[str] = None,
    description: Optional[str] = None,
    department: Optional[str] = None,
    equipment_tag: Optional[str] = None,
    uom: Optional[str] = None,
    reference: Optional[str] = None,
    requested_by: Optional[str] = None,
    note: Optional[str] = None,
    opportunity_id: Optional[int] = None,
    price_override_reason: Optional[str] = None,
) -> Tuple[bool, str, Optional[int]]:
    """
    Log an avoided order / savings entry.
    Validates quantities, price overrides, duplicates, and freezes amount in integer paise.
    """
    if avoided_qty <= 0:
        return False, "Avoided quantity must be greater than zero.", None
    if avoided_qty > requested_qty:
        return False, "Avoided quantity cannot exceed requested quantity.", None
    if unit_price < 0:
        return False, "Unit price cannot be negative.", None

    period_str = action_date.strftime("%b %Y")

    # Determine price source & check deviation
    price_source = "manual"
    price_overridden = 0
    ref_price = 0.0

    with engine.connect() as conn:
        if item_key:
            row = conn.execute(
                text(
                    """
                    SELECT unit_price, price_carried, description, department, equipment_tag, uom, material_code
                    FROM inventory_items
                    WHERE item_key = :ik
                    ORDER BY id DESC LIMIT 1
                    """
                ),
                {"ik": item_key},
            ).fetchone()

            if row:
                ref_price = float(row[0] or 0.0)
                price_source = "carried" if row[1] else "inventory_latest"
                description = description or row[2]
                department = department or row[3]
                equipment_tag = equipment_tag or row[4]
                uom = uom or row[5]
                material_code = material_code or row[6]

                if ref_price > 0:
                    pct_diff = abs(unit_price - ref_price) / ref_price
                    if pct_diff > 0.20:
                        if not price_override_reason:
                            return (
                                False,
                                f"Price differs by {pct_diff:.1%} from inventory catalog (Rs {ref_price:,.2f}). A reason is required.",
                                None,
                            )
                        price_overridden = 1

        # Check unique constraint on (item_key, reference, type)
        if reference and reference.strip():
            existing = conn.execute(
                text(
                    """
                    SELECT id FROM savings_entries
                    WHERE item_key = :ik AND reference = :ref AND type = :t AND status != 'voided'
                    """
                ),
                {"ik": item_key or "", "ref": reference.strip(), "t": entry_type},
            ).fetchone()
            if existing:
                return False, f"An active entry with reference '{reference}' and type '{entry_type}' already exists for this item.", None

        # Check for possible duplicate warning (same item, same period, similar quantity within 10%)
        if item_key:
            sim = conn.execute(
                text(
                    """
                    SELECT id, avoided_qty FROM savings_entries
                    WHERE item_key = :ik AND period = :p AND status != 'voided'
                    """
                ),
                {"ik": item_key, "p": period_str},
            ).fetchall()
            for s_id, s_qty in sim:
                if s_qty and abs(float(s_qty) - avoided_qty) / float(s_qty) <= 0.10:
                    logger.warning("Possible duplicate saving: item %s has existing entry #%s with qty %s", item_key, s_id, s_qty)

    # Calculate frozen paise amounts
    dec_avoided = Decimal(str(avoided_qty))
    dec_req = Decimal(str(requested_qty))
    dec_price = Decimal(str(unit_price))

    amount_paise = int(round(dec_avoided * dec_price * 100))
    order_value_paise = int(round(dec_req * dec_price * 100))

    data_check = "not_applicable" if not item_key else "pending"
    now_iso = datetime.now(timezone.utc).isoformat()
    source = "opportunity" if opportunity_id else "manual"

    with engine.begin() as conn:
        # If converting opportunity, check that it is open
        if opportunity_id:
            opp_row = conn.execute(
                text("SELECT status FROM savings_opportunities WHERE id = :oid"),
                {"oid": opportunity_id},
            ).fetchone()
            if not opp_row or opp_row[0] != "open":
                return False, "This opportunity is no longer open for conversion.", None

        res = conn.execute(
            text(
                """
                INSERT INTO savings_entries (
                    period, action_date, item_key, material_code, description, department,
                    equipment_tag, type, requested_qty, avoided_qty, uom, unit_price,
                    price_source, price_overridden, amount_paise, order_value_paise,
                    reference, requested_by, note, source, opportunity_id, status,
                    data_check, created_by, created_at, version
                ) VALUES (
                    :period, :action_date, :item_key, :material_code, :description, :department,
                    :equipment_tag, :type, :requested_qty, :avoided_qty, :uom, :unit_price,
                    :price_source, :price_overridden, :amount_paise, :order_value_paise,
                    :reference, :requested_by, :note, :source, :opportunity_id, 'actioned',
                    :data_check, :created_by, :created_at, 1
                )
                """
            ),
            {
                "period": period_str,
                "action_date": action_date.isoformat(),
                "item_key": item_key,
                "material_code": material_code,
                "description": description,
                "department": department,
                "equipment_tag": equipment_tag,
                "type": entry_type,
                "requested_qty": float(requested_qty),
                "avoided_qty": float(avoided_qty),
                "uom": uom,
                "unit_price": float(unit_price),
                "price_source": price_source,
                "price_overridden": price_overridden,
                "amount_paise": amount_paise,
                "order_value_paise": order_value_paise,
                "reference": reference,
                "requested_by": requested_by,
                "note": (f"{note} (Override: {price_override_reason})" if price_override_reason else note),
                "source": source,
                "opportunity_id": opportunity_id,
                "data_check": data_check,
                "created_by": created_by,
                "created_at": now_iso,
            },
        )
        new_entry_id = res.lastrowid

        # Update opportunity status to converted
        if opportunity_id:
            conn.execute(
                text(
                    """
                    UPDATE savings_opportunities
                    SET status = 'converted', converted_entry_id = :eid
                    WHERE id = :oid
                    """
                ),
                {"eid": new_entry_id, "oid": opportunity_id},
            )

    log_audit_event(
        engine,
        event_type="savings_logged",
        details=f"Logged {entry_type} entry #{new_entry_id} for Rs {amount_paise / 100:,.2f} ({description or item_key})",
        username=created_by,
    )
    return True, f"Saving entry #{new_entry_id} recorded successfully.", new_entry_id


def edit_savings_entry(
    engine,
    entry_id: int,
    user: str,
    avoided_qty: float,
    unit_price: float,
    requested_qty: float,
    entry_type: str,
    reference: Optional[str] = None,
    requested_by: Optional[str] = None,
    note: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Edit an existing savings entry while it is in 'actioned' status.
    Locked once verified.
    """
    if avoided_qty <= 0 or avoided_qty > requested_qty:
        return False, "Invalid quantities: avoided quantity must be > 0 and <= requested quantity."

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, version FROM savings_entries WHERE id = :id"),
            {"id": entry_id},
        ).fetchone()

        if not row:
            return False, "Savings entry not found."
        if row[0] != "actioned":
            return False, "Verified or voided entries cannot be edited. Void and re-enter if corrections are required."

        new_version = (row[1] or 1) + 1

    dec_avoided = Decimal(str(avoided_qty))
    dec_price = Decimal(str(unit_price))
    dec_req = Decimal(str(requested_qty))
    amount_paise = int(round(dec_avoided * dec_price * 100))
    order_value_paise = int(round(dec_req * dec_price * 100))

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE savings_entries
                SET avoided_qty = :aq, unit_price = :up, requested_qty = :rq,
                    type = :t, reference = :ref, requested_by = :rb, note = :n,
                    amount_paise = :ap, order_value_paise = :ovp, version = :ver
                WHERE id = :id
                """
            ),
            {
                "aq": float(avoided_qty),
                "up": float(unit_price),
                "rq": float(requested_qty),
                "t": entry_type,
                "ref": reference,
                "rb": requested_by,
                "n": note,
                "ap": amount_paise,
                "ovp": order_value_paise,
                "ver": new_version,
                "id": entry_id,
            },
        )

    log_audit_event(
        engine,
        event_type="savings_edited",
        details=f"Edited saving entry #{entry_id} (version {new_version})",
        username=user,
    )
    return True, f"Entry #{entry_id} updated successfully."


def verify_savings_entry(
    engine,
    entry_id: int,
    user: str,
    note: str,
) -> Tuple[bool, str]:
    """
    Verify an actioned savings entry.
    Enforces maker-checker rule if configured.
    """
    if not note or not note.strip():
        return False, "A verification note is required."

    maker_checker = get_setting(engine, "maker_checker", default="false").lower() == "true"

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, created_by, amount_paise FROM savings_entries WHERE id = :id"),
            {"id": entry_id},
        ).fetchone()

        if not row:
            return False, "Savings entry not found."
        status, created_by, amount_paise = row
        if status != "actioned":
            return False, f"Entry is already {status}."

        if maker_checker and created_by.strip().lower() == user.strip().lower():
            return False, "Maker-checker policy is enabled: the verifying admin must be different from the creator."

    now_iso = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE savings_entries
                SET status = 'verified', verified_by = :vby, verified_at = :vat, note = COALESCE(note || ' | ', '') || :n
                WHERE id = :id
                """
            ),
            {"vby": user, "vat": now_iso, "n": f"Verified: {note.strip()}", "id": entry_id},
        )

    log_audit_event(
        engine,
        event_type="savings_verified",
        details=f"Verified saving entry #{entry_id} for Rs {amount_paise / 100:,.2f}",
        username=user,
    )
    return True, f"Entry #{entry_id} verified successfully."


def void_savings_entry(
    engine,
    entry_id: int,
    user: str,
    reason: str,
) -> Tuple[bool, str]:
    """
    Void a savings entry. Record is preserved with reason, excluded from all totals.
    If converted from an opportunity, the opportunity is reopened.
    """
    if not reason or not reason.strip():
        return False, "A void reason is required."

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, opportunity_id FROM savings_entries WHERE id = :id"),
            {"id": entry_id},
        ).fetchone()

        if not row:
            return False, "Savings entry not found."
        status, opp_id = row
        if status == "voided":
            return False, "Entry is already voided."

    now_iso = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE savings_entries
                SET status = 'voided', void_reason = :r, voided_by = :vby, voided_at = :vat
                WHERE id = :id
                """
            ),
            {"r": reason.strip(), "vby": user, "vat": now_iso, "id": entry_id},
        )

        # Reopen linked opportunity
        if opp_id:
            conn.execute(
                text(
                    """
                    UPDATE savings_opportunities
                    SET status = 'open', converted_entry_id = NULL
                    WHERE id = :oid
                    """
                ),
                {"oid": opp_id},
            )

    log_audit_event(
        engine,
        event_type="savings_voided",
        details=f"Voided saving entry #{entry_id}: {reason.strip()}",
        username=user,
    )
    return True, f"Entry #{entry_id} has been voided."


def dismiss_opportunity(
    engine,
    opportunity_id: int,
    user: str,
    reason: str,
) -> Tuple[bool, str]:
    """
    Dismiss a savings opportunity with a mandatory reason.
    """
    if not reason or not reason.strip():
        return False, "A dismissal reason is required."

    with engine.begin() as conn:
        res = conn.execute(
            text(
                """
                UPDATE savings_opportunities
                SET status = 'dismissed', dismiss_reason = :r
                WHERE id = :oid AND status = 'open'
                """
            ),
            {"r": reason.strip(), "oid": opportunity_id},
        )
        if res.rowcount == 0:
            return False, "Opportunity is not open or does not exist."

    log_audit_event(
        engine,
        event_type="opportunity_dismissed",
        details=f"Dismissed opportunity #{opportunity_id}: {reason.strip()}",
        username=user,
    )
    return True, "Opportunity dismissed."


# ─────────────────────────────────────────────────────────────────────────────
# 3. Automated Data Check Engine
# ─────────────────────────────────────────────────────────────────────────────


def run_data_check(engine) -> int:
    """
    Re-runs automated data verification on all non-voided savings entries with an item_key.
    Checks the inventory diff of the 1 to 3 published periods following action_date:
    - If an addition >= avoided_qty appears -> data_check = 'contradicted'
    - If at least one later period shows no such addition -> data_check = 'supported'
    - Otherwise -> data_check = 'pending'

    Returns number of entries updated.
    """
    with engine.connect() as conn:
        entries = conn.execute(
            text(
                """
                SELECT id, action_date, item_key, avoided_qty, status
                FROM savings_entries
                WHERE status != 'voided' AND item_key IS NOT NULL AND item_key != ''
                """
            )
        ).fetchall()

        if not entries:
            return 0

        # Get all active published inventory uploads ordered by date
        uploads = conn.execute(
            text(
                """
                SELECT id, period, as_of_date FROM uploads
                WHERE dataset = 'inventory' AND status = 'active'
                ORDER BY as_of_date ASC
                """
            )
        ).fetchall()

        diffs = conn.execute(
            text(
                """
                SELECT upload_id, item_key, qty_delta
                FROM inventory_diff
                WHERE change_type IN ('Qty increase', 'New item')
                  AND (flags NOT LIKE '%Transfer%' OR flags IS NULL)
                """
            )
        ).fetchall()

    diff_map: Dict[Tuple[int, str], float] = {}
    for uid, ikey, qd in diffs:
        diff_map[(uid, ikey)] = diff_map.get((uid, ikey), 0.0) + float(qd or 0.0)

    updated_count = 0
    updates = []

    for eid, action_d_str, ikey, avoided_q, stat in entries:
        try:
            act_d = datetime.strptime(action_d_str[:10], "%Y-%m-%d").date()
        except Exception:
            continue

        # Look for 1 to 3 published periods strictly after action_date
        later_uploads = [u for u in uploads if datetime.strptime(u[2][:10], "%Y-%m-%d").date() > act_d][:3]

        if not later_uploads:
            new_check = "pending"
            check_period = None
        else:
            contradicted = False
            for lu in later_uploads:
                added_in_period = diff_map.get((lu[0], ikey), 0.0)
                if added_in_period >= float(avoided_q):
                    contradicted = True
                    check_period = lu[1]
                    break

            if contradicted:
                new_check = "contradicted"
            else:
                new_check = "supported"
                check_period = later_uploads[0][1]

        updates.append((new_check, check_period, eid))

    with engine.begin() as conn:
        for chk, cp, eid in updates:
            conn.execute(
                text(
                    """
                    UPDATE savings_entries
                    SET data_check = :chk, data_check_period = :cp
                    WHERE id = :id AND (data_check != :chk OR data_check_period IS NOT :cp)
                    """
                ),
                {"chk": chk, "cp": cp, "id": eid},
            )
            updated_count += 1

    return updated_count


# ─────────────────────────────────────────────────────────────────────────────
# 4. Savings Summary Layer (Single Source of Truth)
# ─────────────────────────────────────────────────────────────────────────────


def savings_summary(
    engine,
    scope: str = "period",
    period: Optional[str] = None,
    user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate unified savings metrics, opportunity breakdowns, and excess stock figures.
    Scope:
      'period' -> current active reporting period
      'FY'     -> financial year containing period (or today)
      'all'    -> all published history
    """
    fy_start_m = get_setting_int(engine, "fy_start_month", default=4)
    threshold_rs = get_setting_float(engine, "plant_head_threshold", default=200000.0)

    target_d = parse_period_to_date(period) if period else date.today()
    if not target_d:
        target_d = date.today()

    fy_label = get_fy_label(target_d, fy_start_month=fy_start_m)
    fy_start, fy_end = get_fy_date_bounds(fy_label, fy_start_month=fy_start_m)

    with engine.connect() as conn:
        # 1. Fetch savings entries based on scope
        query = """
            SELECT id, period, action_date, item_key, material_code, description,
                   department, equipment_tag, type, requested_qty, avoided_qty,
                   unit_price, amount_paise, order_value_paise, status, data_check,
                   created_by, verified_by, reference
            FROM savings_entries
            WHERE status != 'voided'
        """
        params: Dict[str, Any] = {}

        if scope == "period" and period:
            query += " AND period = :p"
            params["p"] = period
        elif scope == "FY":
            query += " AND action_date >= :fy_s AND action_date <= :fy_e"
            params["fy_s"] = fy_start.isoformat()
            params["fy_e"] = fy_end.isoformat()

        query += " ORDER BY action_date DESC"
        entries_df = pd.read_sql_query(query, con=engine, params=params)

        # 2. Opportunities for period or all
        opp_query = "SELECT * FROM savings_opportunities WHERE 1=1"
        opp_params = {}
        if period and scope == "period":
            opp_query += " AND period = :p"
            opp_params["p"] = period
        opp_df = pd.read_sql_query(opp_query, con=engine, params=opp_params)

        # 3. Excess stock for period
        exc_query = "SELECT * FROM excess_stock WHERE 1=1"
        exc_params = {}
        if period:
            exc_query += " AND period = :p"
            exc_params["p"] = period
        exc_df = pd.read_sql_query(exc_query, con=engine, params=exc_params)

        # Previous period excess stock for delta
        prev_excess_paise = 0
        if period:
            # find chronologically previous period
            avail_periods = [
                r[0]
                for r in conn.execute(
                    text(
                        """
                        SELECT period FROM uploads
                        WHERE dataset = 'inventory' AND status = 'active'
                        GROUP BY period ORDER BY as_of_date DESC
                        """
                    )
                ).fetchall()
            ]
            if period in avail_periods:
                idx = avail_periods.index(period)
                if idx + 1 < len(avail_periods):
                    prev_p = avail_periods[idx + 1]
                    prev_r = conn.execute(
                        text("SELECT SUM(excess_value_paise) FROM excess_stock WHERE period = :prev"),
                        {"prev": prev_p},
                    ).scalar()
                    prev_excess_paise = prev_r or 0

        # Monthly trend for current FY
        fy_months_rows = conn.execute(
            text(
                """
                SELECT period, status, SUM(amount_paise) as total_p
                FROM savings_entries
                WHERE status IN ('actioned', 'verified')
                  AND action_date >= :fys AND action_date <= :fye
                GROUP BY period, status
                """
            ),
            {"fys": fy_start.isoformat(), "fye": fy_end.isoformat()},
        ).fetchall()

    # Aggregate entries
    verified_paise = 0
    actioned_paise = 0
    flagged_count = 0
    orders_reviewed = len(entries_df)
    orders_above_threshold = 0
    savings_by_type: Dict[str, float] = {}
    savings_by_dept: Dict[str, float] = {}
    savings_by_equip: Dict[str, float] = {}

    if not entries_df.empty:
        for _, row in entries_df.iterrows():
            amt = int(row["amount_paise"] or 0)
            st_val = row["status"]
            if st_val == "verified":
                verified_paise += amt
            elif st_val == "actioned":
                actioned_paise += amt

            if row["data_check"] == "contradicted":
                flagged_count += 1

            if (amt / 100.0) >= threshold_rs:
                orders_above_threshold += 1

            # Only count verified + actioned toward breakdowns
            t = row["type"] or "other"
            d = row["department"] or "General"
            eq = row["equipment_tag"] or "Unassigned"

            amt_rs = amt / 100.0
            savings_by_type[t] = savings_by_type.get(t, 0.0) + amt_rs
            savings_by_dept[d] = savings_by_dept.get(d, 0.0) + amt_rs
            savings_by_equip[eq] = savings_by_equip.get(eq, 0.0) + amt_rs

    # Aggregate opportunities
    opp_summary = {
        "high": {"count": 0, "paise": 0, "rs": 0.0},
        "medium": {"count": 0, "paise": 0, "rs": 0.0},
        "low": {"count": 0, "paise": 0, "rs": 0.0},
        "total_count": 0,
        "total_paise": 0,
        "total_rs": 0.0,
    }

    if not opp_df.empty:
        open_opps = opp_df[opp_df["status"] == "open"]
        for _, o_row in open_opps.iterrows():
            c_level = str(o_row["confidence"]).lower()
            if c_level not in opp_summary:
                c_level = "low"
            p_val = int(o_row["avoidable_value_paise"] or 0)
            opp_summary[c_level]["count"] += 1
            opp_summary[c_level]["paise"] += p_val
            opp_summary[c_level]["rs"] += p_val / 100.0
            opp_summary["total_count"] += 1
            opp_summary["total_paise"] += p_val
            opp_summary["total_rs"] += p_val / 100.0

    # Aggregate excess stock
    excess_stock_paise = int(exc_df["excess_value_paise"].sum()) if not exc_df.empty else 0
    excess_delta_paise = excess_stock_paise - prev_excess_paise

    # Compile FY monthly trend data
    monthly_map: Dict[str, Dict[str, float]] = {}
    for p_name, s_name, p_sum in fy_months_rows:
        if p_name not in monthly_map:
            monthly_map[p_name] = {"verified_rs": 0.0, "actioned_rs": 0.0}
        if s_name == "verified":
            monthly_map[p_name]["verified_rs"] += (p_sum or 0) / 100.0
        elif s_name == "actioned":
            monthly_map[p_name]["actioned_rs"] += (p_sum or 0) / 100.0

    monthly_trend = []
    cumul_rs = 0.0
    for p_name, vals in monthly_map.items():
        v_rs = vals["verified_rs"]
        a_rs = vals["actioned_rs"]
        cumul_rs += v_rs + a_rs
        monthly_trend.append(
            {
                "period": p_name,
                "verified_rs": v_rs,
                "actioned_rs": a_rs,
                "cumulative_fy_rs": cumul_rs,
            }
        )

    return {
        "scope": scope,
        "period": period,
        "fy_label": fy_label,
        "verified_paise": verified_paise,
        "verified_rs": verified_paise / 100.0,
        "actioned_paise": actioned_paise,
        "actioned_rs": actioned_paise / 100.0,
        "total_savings_rs": (verified_paise + actioned_paise) / 100.0,
        "flagged_count": flagged_count,
        "opportunities": opp_summary,
        "excess_stock_paise": excess_stock_paise,
        "excess_stock_rs": excess_stock_paise / 100.0,
        "excess_stock_delta_rs": excess_delta_paise / 100.0,
        "orders_reviewed_count": orders_reviewed,
        "orders_above_threshold_count": orders_above_threshold,
        "savings_by_type": savings_by_type,
        "savings_by_department": savings_by_dept,
        "savings_by_equipment": savings_by_equip,
        "monthly_trend": monthly_trend,
        "entries_df": entries_df,
        "opportunities_df": opp_df,
    }

