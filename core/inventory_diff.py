"""
Inventory Snapshot Comparison and Diff Engine.
Calculates change types, monetary effects (quantity effect, price effect),
and executes the exact reconciliation identity:
Opening + Added - Reduced + Revaluation = Closing
"""

import json
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
from sqlalchemy import text

from core.settings import (
    get_setting_float,
    get_setting_int,
)

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")


def to_dec(val: Any) -> Decimal:
    """Convert numeric value to Decimal safely."""
    if val is None or pd.isna(val):
        return Decimal("0.00")
    try:
        return Decimal(str(val)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def load_item_aliases(engine) -> Dict[str, str]:
    """Load old_code -> new_code mappings from item_aliases table."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT old_code, new_code FROM item_aliases")).fetchall()
            return {r[0]: r[1] for r in rows}
    except Exception as exc:
        logger.error("Failed to load item aliases: %s", exc)
        return {}


def compute_inventory_diff(
    engine,
    new_upload_id: int,
    baseline_upload_id: Optional[int],
    as_of_date_str: str,
) -> Dict[str, Any]:
    """
    Compute full inventory diff between new upload and baseline snapshot.
    Persists diff rows into inventory_diff table.
    Returns reconciliation summary dictionary.
    """
    aliases = load_item_aliases(engine)

    # 1. Fetch new items
    with engine.connect() as conn:
        new_items_df = pd.read_sql_query(
            """
            SELECT id, item_key, material_code, location, description, uom,
                   qty, unit_price, value, last_used_date, last_received_date,
                   expiry_date, department, category, criticality
            FROM inventory_items
            WHERE upload_id = :uid
            """,
            con=engine,
            params={"uid": new_upload_id},
        )

        # 2. Fetch baseline items if baseline exists
        baseline_items_df = pd.DataFrame()
        if baseline_upload_id:
            baseline_items_df = pd.read_sql_query(
                """
                SELECT id, item_key, material_code, location, description, uom,
                       qty, unit_price, value, last_used_date, last_received_date,
                       expiry_date, department, category, criticality
                FROM inventory_items
                WHERE upload_id = :uid
                """,
                con=engine,
                params={"uid": baseline_upload_id},
            )

    # Configurable thresholds
    high_val_thresh = Decimal(str(get_setting_float(engine, "high_value_approval_rs", default=200000.0)))
    qty_mult_thresh = Decimal(str(get_setting_float(engine, "qty_change_multiple_verify", default=3.0)))
    recon_tol = Decimal(str(get_setting_float(engine, "reconciliation_tolerance_rs", default=1.0)))

    # FIRST EVER UPLOAD (BASELINE CREATION)
    if baseline_upload_id is None or baseline_items_df.empty:
        total_val = Decimal("0.00")
        for _, r in new_items_df.iterrows():
            q = to_dec(r.get("qty", 0))
            p = to_dec(r.get("unit_price")) if pd.notna(r.get("unit_price")) else None
            if p is not None:
                total_val += q * p

        return {
            "is_baseline": True,
            "opening_val": Decimal("0.00"),
            "added_val": total_val,
            "reduced_val": Decimal("0.00"),
            "revaluation_val": Decimal("0.00"),
            "closing_val": total_val,
            "net_change_val": total_val,
            "is_reconciled": True,
            "reconciliation_gap": Decimal("0.00"),
            "diff_rows": [],
            "items_count": len(new_items_df),
        }

    # Normalize aliases on material codes
    if aliases:
        new_items_df["material_code"] = new_items_df["material_code"].apply(lambda c: aliases.get(c, c))
        baseline_items_df["material_code"] = baseline_items_df["material_code"].apply(lambda c: aliases.get(c, c))

    # Merge duplicates inside each file (sum qty, weighted avg price)
    def aggregate_items(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        # Rebuild item_key
        df["item_key"] = df.apply(
            lambda r: f"{r['material_code']}|{r['location']}" if r.get("location") else r["material_code"],
            axis=1,
        )
        grouped = []
        for ikey, grp in df.groupby("item_key"):
            first_row = grp.iloc[0].to_dict()
            total_qty = grp["qty"].sum()
            total_val = sum(r["qty"] * (r["unit_price"] if pd.notna(r["unit_price"]) else 0.0) for _, r in grp.iterrows())
            avg_price = (total_val / total_qty) if total_qty > 0 else (first_row.get("unit_price") or 0.0)

            first_row["qty"] = total_qty
            first_row["unit_price"] = avg_price
            first_row["value"] = total_val
            grouped.append(first_row)
        return pd.DataFrame(grouped)

    new_agg = aggregate_items(new_items_df)
    base_agg = aggregate_items(baseline_items_df)

    # Index by item_key
    new_dict = {r["item_key"]: r for _, r in new_agg.iterrows()}
    base_dict = {r["item_key"]: r for _, r in base_agg.iterrows()}

    all_keys = sorted(set(new_dict.keys()) | set(base_dict.keys()))

    # Transfer detection: matching delta between locations of the same material code
    # Pre-calculate candidate deltas
    material_deltas: Dict[str, List[Tuple[str, float]]] = {}
    for k in all_keys:
        n_row = new_dict.get(k)
        b_row = base_dict.get(k)
        q_n = n_row["qty"] if n_row is not None else 0.0
        q_b = b_row["qty"] if b_row is not None else 0.0
        delta = q_n - q_b
        mcode = k.split("|")[0]
        material_deltas.setdefault(mcode, []).append((k, delta))

    transfer_keys = set()
    for mcode, deltas in material_deltas.items():
        if len(deltas) >= 2:
            negatives = [(k, d) for k, d in deltas if d < 0]
            positives = [(k, d) for k, d in deltas if d > 0]
            for nk, nd in negatives:
                for pk, pd_val in positives:
                    if abs(nd + pd_val) < 1e-4 and nk not in transfer_keys and pk not in transfer_keys:
                        transfer_keys.add(nk)
                        transfer_keys.add(pk)

    diff_records = []
    opening_val = Decimal("0.00")
    closing_val = Decimal("0.00")
    added_val = Decimal("0.00")
    reduced_val = Decimal("0.00")
    revaluation_val = Decimal("0.00")

    # Sum opening value
    for _, b_row in base_agg.iterrows():
        qb = to_dec(b_row.get("qty", 0))
        pb = to_dec(b_row.get("unit_price")) if pd.notna(b_row.get("unit_price")) else None
        if pb is not None and pb > 0:
            opening_val += qb * pb

    # Sum closing value
    for _, n_row in new_agg.iterrows():
        qn = to_dec(n_row.get("qty", 0))
        pn = to_dec(n_row.get("unit_price")) if pd.notna(n_row.get("unit_price")) else None
        if pn is not None and pn > 0:
            closing_val += qn * pn

    for k in all_keys:
        n_row = new_dict.get(k)
        b_row = base_dict.get(k)

        q_b = to_dec(b_row.get("qty", 0)) if b_row is not None else Decimal("0.00")
        p_b = to_dec(b_row.get("unit_price")) if b_row is not None and pd.notna(b_row.get("unit_price")) else None

        q_n = to_dec(n_row.get("qty", 0)) if n_row is not None else Decimal("0.00")
        p_n = to_dec(n_row.get("unit_price")) if n_row is not None and pd.notna(n_row.get("unit_price")) else None

        flags: List[str] = []

        # Price carry-forward
        if n_row is not None and (p_n is None or p_n == Decimal("0.00")) and (p_b is not None and p_b > 0):
            p_n = p_b
            flags.append("Price carried")
        elif (p_n is None or p_n == 0) and (p_b is None or p_b == 0):
            flags.append("Unvalued item")

        # Determine change type and monetary effects
        qty_effect = Decimal("0.00")
        price_effect = Decimal("0.00")
        change_type = "Unchanged"

        # Transfer
        if k in transfer_keys:
            change_type = "Transfer"
            qty_delta = q_n - q_b
            # Excluded from Added and Reduced monetary totals per Section 9
            qty_effect = Decimal("0.00")
            price_effect = Decimal("0.00")

        # New item
        elif b_row is None and n_row is not None:
            change_type = "New item"
            qty_delta = q_n
            price_effective = p_n or Decimal("0.00")
            qty_effect = q_n * price_effective
            price_effect = Decimal("0.00")
            added_val += qty_effect

        # Removed item
        elif b_row is not None and n_row is None:
            change_type = "Removed item"
            qty_delta = -q_b
            price_effective = p_b or Decimal("0.00")
            qty_effect = -q_b * price_effective
            price_effect = Decimal("0.00")
            reduced_val += abs(qty_effect)

        # Both exist
        else:
            qty_delta = q_n - q_b
            pb_eff = p_b or Decimal("0.00")
            pn_eff = p_n or pb_eff

            qty_effect = qty_delta * pb_eff
            price_effect = q_n * (pn_eff - pb_eff)

            if qty_delta > 0:
                change_type = "Qty increase"
                added_val += qty_effect
            elif qty_delta < 0:
                change_type = "Qty decrease"
                reduced_val += abs(qty_effect)
            elif pn_eff != pb_eff:
                change_type = "Price change only"
            else:
                # Check for moved but net zero
                lud_n = str(n_row.get("last_used_date", ""))
                lud_b = str(b_row.get("last_used_date", ""))
                if lud_n and lud_n != lud_b:
                    change_type = "Moved but net zero"
                    flags.append("Net-change blind spot")
                else:
                    change_type = "Unchanged"

            revaluation_val += price_effect

        # Flags check
        if qty_effect >= high_val_thresh or (price_effect >= high_val_thresh):
            flags.append("Above Rs 2L")

        if b_row is not None and q_b > 0 and qty_delta >= qty_mult_thresh * q_b:
            flags.append("Verify: large qty increase")

        # Expiry write-off
        if n_row is not None and qty_delta < 0:
            exp = str(n_row.get("expiry_date", ""))[:10]
            if exp and exp <= as_of_date_str:
                flags.append("Likely expiry write-off")


        diff_records.append({
            "upload_id": new_upload_id,
            "baseline_upload_id": baseline_upload_id,
            "item_key": k,
            "material_code": k.split("|")[0],
            "change_type": change_type,
            "qty_old": float(q_b),
            "qty_new": float(q_n),
            "qty_delta": float(qty_delta),
            "price_old": float(p_b) if p_b is not None else None,
            "price_new": float(p_n) if p_n is not None else None,
            "qty_effect": float(qty_effect),
            "price_effect": float(price_effect),
            "flags": json.dumps(flags) if flags else None,
        })

    # Save to inventory_diff
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM inventory_diff WHERE upload_id = :uid"), {"uid": new_upload_id})
        if diff_records:
            conn.execute(
                text(
                    """
                    INSERT INTO inventory_diff
                        (upload_id, baseline_upload_id, item_key, change_type,
                         qty_old, qty_new, qty_delta, price_old, price_new,
                         qty_effect, price_effect, flags)
                    VALUES
                        (:upload_id, :baseline_upload_id, :item_key, :change_type,
                         :qty_old, :qty_new, :qty_delta, :price_old, :price_new,
                         :qty_effect, :price_effect, :flags)
                    """
                ),
                diff_records,
            )

    # RECONCILIATION CHECK: Opening + Added - Reduced + Revaluation = Closing
    expected_closing = opening_val + added_val - reduced_val + revaluation_val
    reconciliation_gap = abs(expected_closing - closing_val)
    is_reconciled = reconciliation_gap <= recon_tol

    return {
        "is_baseline": False,
        "opening_val": opening_val,
        "added_val": added_val,
        "reduced_val": reduced_val,
        "revaluation_val": revaluation_val,
        "closing_val": closing_val,
        "expected_closing": expected_closing,
        "net_change_val": closing_val - opening_val,
        "is_reconciled": is_reconciled,
        "reconciliation_gap": reconciliation_gap,
        "diff_rows": diff_records,
        "transfers_count": len(transfer_keys) // 2,
    }
