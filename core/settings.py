"""
Settings and configuration management.
Provides configurable thresholds for reconciliation, gap detection, and alerts.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from sqlalchemy import text

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "overstock_months_threshold": ("6", "Months of average PTW usage to flag overstock"),
    "high_value_approval_rs": ("200000", "Amount threshold (Rs) requiring plant head approval"),
    "reconciliation_tolerance_rs": ("1.0", "Reconciliation balance tolerance (Rs)"),
    "gap_tolerance_percent": ("5.0", "Store vs PTW quantity difference tolerance percentage"),
    "gap_tolerance_units": ("1.0", "Store vs PTW quantity difference tolerance in units"),
    "qty_change_multiple_verify": ("3.0", "Multiple of previous quantity to flag for verification"),
    "large_value_change_rs": ("500000", "Value change (Rs) to flag for verification"),
    "stale_days_threshold": ("35", "Days since data as-of date before flagging data as stale"),
    # Savings and Report Settings (Step 1)
    "months_of_cover": ("6", "Target months of inventory cover for stock planning"),
    "min_history_months": ("3", "Minimum published periods of history required for consumption estimate"),
    "history_window_months": ("12", "Window of past published periods used to calculate average monthly consumption"),
    "fy_start_month": ("4", "Financial year starting month (1=Jan, 4=Apr)"),
    "plant_head_threshold": ("200000", "Order value threshold (Rs) requiring plant head approval"),
    "maker_checker": ("false", "Require verification of savings by a different user than the creator"),
    "include_slides": ("true", "Include PowerPoint slides deck in report generation"),
    "pdf_top_n": ("10", "Number of top items to show in summary charts and tables of PDF report"),
    "plant_name": ("Captive Power Plant", "Official plant facility name for reports and headers"),
    "site_name": ("", "Site or location name (optional)"),
    "footer_text": ("Internal use only", "Confidentiality or distribution notice on report footers"),
}


def initialize_settings(engine) -> None:
    """
    Ensure all default settings exist in the database.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        with engine.connect() as conn:
            for key, (val, desc) in DEFAULT_SETTINGS.items():
                conn.execute(
                    text(
                        """
                        INSERT OR IGNORE INTO settings (key, value, description, updated_at, updated_by)
                        VALUES (:key, :val, :desc, :now, 'system')
                        """
                    ),
                    {"key": key, "val": val, "desc": desc, "now": now},
                )
            conn.commit()
    except Exception as exc:
        logger.error("Failed to initialize default settings: %s", exc)


def get_setting(engine, key: str, default: Optional[str] = None) -> str:
    """
    Retrieve a setting value as string.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM settings WHERE key = :key"),
                {"key": key},
            ).fetchone()
            if row:
                return row[0]
    except Exception as exc:
        logger.error("Failed to read setting '%s': %s", key, exc)

    if default is not None:
        return default
    if key in DEFAULT_SETTINGS:
        return DEFAULT_SETTINGS[key][0]
    return ""


def get_setting_float(engine, key: str, default: float = 0.0) -> float:
    """
    Retrieve a numeric setting value as float.
    """
    val = get_setting(engine, key)
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_setting_int(engine, key: str, default: int = 0) -> int:
    """
    Retrieve a numeric setting value as int.
    """
    val = get_setting(engine, key)
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def get_setting_bool(engine, key: str, default: bool = False) -> bool:
    """
    Retrieve a boolean setting value.
    """
    val = get_setting(engine, key)
    if not val:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on", "t")


def set_setting(engine, key: str, value: str, updated_by: str = "admin") -> None:
    """
    Update or insert a setting value.
    """
    now = datetime.now(timezone.utc).isoformat()
    desc = DEFAULT_SETTINGS.get(key, ("", ""))[1]
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO settings (key, value, description, updated_at, updated_by)
                    VALUES (:key, :value, :desc, :now, :by)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at,
                        updated_by = excluded.updated_by
                    """
                ),
                {"key": key, "value": str(value), "desc": desc, "now": now, "by": updated_by},
            )
            conn.commit()
    except Exception as exc:
        logger.error("Failed to set setting '%s': %s", key, exc)
        raise


def get_all_settings(engine) -> Dict[str, Dict[str, Any]]:
    """
    Retrieve all settings as a dictionary.
    """
    initialize_settings(engine)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT key, value, description, updated_at, updated_by FROM settings")
            ).fetchall()
            return {
                r[0]: {
                    "value": r[1],
                    "description": r[2],
                    "updated_at": r[3],
                    "updated_by": r[4],
                }
                for r in rows
            }
    except Exception as exc:
        logger.error("Failed to fetch all settings: %s", exc)
        return {}

