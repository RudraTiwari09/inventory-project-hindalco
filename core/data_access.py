"""
Data access layer for period-based snapshot retrieval.
Caches snapshots by snapshot upload ID and provides query methods for:
- get_inventory(period)
- get_ptw(period)
- get_available_periods()
- get_latest_snapshot_info(dataset)
- is_snapshot_stale(as_of_date)
"""

import logging
from datetime import datetime, timezone, date
from typing import Optional, Dict, Any, List, Tuple
import pandas as pd
from sqlalchemy import text

from core.settings import get_setting_int

logger = logging.getLogger(__name__)

# Cache for loaded snapshots: maps (dataset, upload_id) -> DataFrame
_SNAPSHOT_CACHE: Dict[Tuple[str, int], pd.DataFrame] = {}


def clear_snapshot_cache() -> None:
    """Clear memory cache of loaded snapshots."""
    _SNAPSHOT_CACHE.clear()


def get_available_periods(engine) -> List[str]:
    """
    Return all distinct active periods across both inventory and PTW uploads,
    sorted newest first by as_of_date.
    """
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT period, MAX(as_of_date) as max_date
                    FROM uploads
                    WHERE status = 'active'
                    GROUP BY period
                    ORDER BY max_date DESC
                    """
                )
            ).fetchall()
            return [r[0] for r in rows if r[0]]
    except Exception as exc:
        logger.error("Failed to fetch available periods: %s", exc)
        return []


def get_latest_period(engine) -> Optional[str]:
    """
    Return the period corresponding to the most recent active upload.
    """
    periods = get_available_periods(engine)
    return periods[0] if periods else None


def get_latest_snapshot_info(engine, dataset: str) -> Optional[Dict[str, Any]]:
    """
    Return metadata for the latest active snapshot of a dataset ('inventory' or 'ptw').
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, dataset, period, as_of_date, file_name, file_hash,
                           uploaded_by, uploaded_at, status, version, row_count, is_baseline
                    FROM uploads
                    WHERE dataset = :ds AND status = 'active'
                    ORDER BY as_of_date DESC, id DESC
                    LIMIT 1
                    """
                ),
                {"ds": dataset},
            ).fetchone()

            if not row:
                return None

            upload_id = row[0]
            val_total = 0.0
            if dataset == "inventory":
                vrow = conn.execute(
                    text("SELECT SUM(value) FROM inventory_items WHERE upload_id = :uid"),
                    {"uid": upload_id},
                ).fetchone()
                val_total = vrow[0] if vrow and vrow[0] is not None else 0.0

            return {
                "id": row[0],
                "dataset": row[1],
                "period": row[2],
                "as_of_date": row[3],
                "file_name": row[4],
                "file_hash": row[5],
                "uploaded_by": row[6],
                "uploaded_at": row[7],
                "status": row[8],
                "version": row[9],
                "row_count": row[10] or 0,
                "is_baseline": bool(row[11]),
                "total_value": val_total,
            }
    except Exception as exc:
        logger.error("Failed to get latest snapshot info for %s: %s", dataset, exc)
        return None


def is_snapshot_stale(engine, as_of_date_str: Optional[str]) -> bool:
    """
    Return True if as_of_date is older than stale_days_threshold (default 35 days).
    """
    if not as_of_date_str:
        return True
    try:
        as_of = datetime.strptime(as_of_date_str[:10], "%Y-%m-%d").date()
        threshold_days = get_setting_int(engine, "stale_days_threshold", default=35)
        today = date.today()
        return (today - as_of).days > threshold_days
    except Exception:
        return False


def get_inventory(engine, period: Optional[str] = None) -> pd.DataFrame:
    """
    Retrieve the active inventory snapshot DataFrame for a given period.
    If period is None, fetches the latest active inventory snapshot.
    Cached by upload ID.
    """
    try:
        with engine.connect() as conn:
            if period:
                row = conn.execute(
                    text(
                        """
                        SELECT id FROM uploads
                        WHERE dataset = 'inventory' AND period = :p AND status = 'active'
                        ORDER BY version DESC, id DESC
                        LIMIT 1
                        """
                    ),
                    {"p": period},
                ).fetchone()
            else:
                row = conn.execute(
                    text(
                        """
                        SELECT id FROM uploads
                        WHERE dataset = 'inventory' AND status = 'active'
                        ORDER BY as_of_date DESC, version DESC, id DESC
                        LIMIT 1
                        """
                    )
                ).fetchone()

            if not row:
                # Fallback to legacy inventory_cleaned if present
                legacy_df = pd.read_sql_table("inventory_cleaned", con=engine)
                return legacy_df if not legacy_df.empty else pd.DataFrame()

            upload_id = row[0]
            cache_key = ("inventory", upload_id)
            if cache_key in _SNAPSHOT_CACHE:
                return _SNAPSHOT_CACHE[cache_key].copy()

            df = pd.read_sql_query(
                """
                SELECT item_key, material_code, location, description, uom,
                       qty as quantity_in_stock, unit_price as unit_cost, price_carried,
                       value, last_used_date, last_received_date as date_received,
                       expiry_date, department, category, criticality as criticality_flag
                FROM inventory_items
                WHERE upload_id = :uid
                """,
                con=engine,
                params={"uid": upload_id},
            )

            # Ensure compatibility column names
            if "description" in df.columns and "spare_description_cleaned" not in df.columns:
                df["spare_description_cleaned"] = df["description"]
            if "material_code" in df.columns and "spare_code" not in df.columns:
                df["spare_code"] = df["material_code"]

            _SNAPSHOT_CACHE[cache_key] = df
            return df.copy()
    except Exception as exc:
        logger.error("Failed to load inventory for period %s: %s", period, exc)
        return pd.DataFrame()


def get_ptw(engine, period: Optional[str] = None) -> pd.DataFrame:
    """
    Retrieve active PTW records for a given period.
    If period is None, fetches all active PTW records up to latest period.
    """
    try:
        with engine.connect() as conn:
            query = """
                SELECT p.permit_key, p.permit_id, p.department, p.equipment_tag,
                       p.permit_start, p.permit_end, p.issue_date, p.period, p.status, p.remarks,
                       l.line_number, l.raw_description, l.cleaned_description,
                       l.quantity as quantity_used, l.uom, l.matched_item_key,
                       l.match_method, l.match_confidence, l.status as match_status
                FROM ptw_permits p
                JOIN ptw_lines l ON p.permit_key = l.permit_key AND p.upload_id = l.upload_id
                JOIN uploads u ON p.upload_id = u.id
                WHERE u.status = 'active'
            """
            params = {}
            if period:
                query += " AND p.period = :period"
                params["period"] = period

            query += " ORDER BY p.permit_start DESC"

            df = pd.read_sql_query(query, con=engine, params=params)
            if not df.empty:
                # Add compatibility column
                if "cleaned_description" in df.columns and "spare_description_cleaned" not in df.columns:
                    df["spare_description_cleaned"] = df["cleaned_description"]
                return df

            # Fallback to legacy ptw_cleaned
            legacy_df = pd.read_sql_table("ptw_cleaned", con=engine)
            return legacy_df if not legacy_df.empty else pd.DataFrame()
    except Exception as exc:
        logger.error("Failed to load PTW for period %s: %s", period, exc)
        return pd.DataFrame()

