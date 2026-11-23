"""
PTW merge and event record update engine.
Implements key-based upsert, permit history tracking, cumulative vs monthly detection,
date-based period assignment, and status classification per Section 10.
"""

import hashlib
import logging
import re
from datetime import datetime, timezone, date
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

CLOSED_STATUSES = {"closed", "completed", "done", "executed"}
PENDING_STATUSES = {"open", "in progress", "pending", "approved", "issued"}
EXCLUDED_STATUSES = {"cancelled", "canceled", "rejected", "withdrawn", "void"}


def normalize_permit_key(val: Any) -> str:
    """Trim, uppercase, and standardize permit key."""
    if val is None or pd.isna(val):
        return ""
    s = str(val).strip().upper()
    # Replace multiple internal spaces with a single space
    return re.sub(r"\s+", " ", s)


def parse_quantity_and_uom(val: Any) -> Tuple[float, str]:
    """
    Parse numeric quantity and unit of measure from text.
    e.g. '2 nos' -> (2.0, 'nos'), '5.5 MTR' -> (5.5, 'mtr'), '10' -> (10.0, '')
    """
    if val is None or pd.isna(val):
        return 0.0, ""
    val_str = str(val).strip().lower()

    # Match number followed by optional unit
    m = re.match(r"^([\d,.]+)\s*([a-zA-Z]*)$", val_str)
    if m:
        num_str = m.group(1).replace(",", "")
        uom_str = m.group(2).strip()
        try:
            return float(num_str), uom_str
        except Exception:
            pass

    # Fallback: extract first float
    nums = re.findall(r"[-+]?(?:\d*\.\d+|\d+)", val_str)
    if nums:
        try:
            return float(nums[0]), ""
        except Exception:
            pass
    return 0.0, ""


def classify_permit_status(status_raw: Any) -> str:
    """
    Classify status into: 'Closed' (counts as usage),
    'Pending' (not counted), or 'Cancelled' (excluded).
    """
    if status_raw is None or pd.isna(status_raw):
        return "Closed"  # Default assumption for maintenance event records
    s = str(status_raw).strip().lower()
    if s in CLOSED_STATUSES:
        return "Closed"
    if s in PENDING_STATUSES:
        return "Pending"
    if s in EXCLUDED_STATUSES:
        return "Cancelled"
    return "Closed"


def detect_file_type(engine, permit_keys: List[str]) -> Tuple[str, float]:
    """
    Auto-detect if file is cumulative (>50% keys exist) or monthly.
    Returns: ('cumulative' or 'monthly', match_percentage)
    """
    if not permit_keys:
        return "monthly", 0.0

    unique_keys = list(set([k for k in permit_keys if k]))
    if not unique_keys:
        return "monthly", 0.0

    try:
        with engine.connect() as conn:
            existing = 0
            # Chunk keys to avoid SQLite parameter limit
            chunk_size = 500
            for i in range(0, len(unique_keys), chunk_size):
                chunk = unique_keys[i:i + chunk_size]
                placeholders = ", ".join([f":k{j}" for j in range(len(chunk))])
                params = {f"k{j}": k for j, k in enumerate(chunk)}
                query = text(f"SELECT COUNT(DISTINCT permit_key) FROM ptw_permits WHERE permit_key IN ({placeholders})")
                cnt = conn.execute(query, params).fetchone()[0]
                existing += cnt

            ratio = (existing / len(unique_keys)) * 100.0
            ftype = "cumulative" if ratio >= 50.0 else "monthly"
            return ftype, ratio
    except Exception as exc:
        logger.error("Error detecting PTW file type: %s", exc)
        return "monthly", 0.0


def merge_and_upsert_ptw(
    engine,
    upload_id: int,
    cleaned_df: pd.DataFrame,
    period: str,
    baseline_as_of_str: Optional[str] = None,
    user: str = "system",
) -> Dict[str, Any]:
    """
    Merge PTW rows by permit_key:
    - New keys -> insert
    - Changed keys -> update and log to ptw_permit_history
    - Same keys & same hash -> keep
    - Late entries -> tag if dated before baseline as-of date
    Returns merge statistics dict.
    """
    now = datetime.now(timezone.utc).isoformat()
    inserted_count = 0
    updated_count = 0
    unchanged_count = 0
    late_entries_count = 0
    cancelled_count = 0

    with engine.begin() as conn:
        for _, row in cleaned_df.iterrows():
            raw_pid = row.get("permit_id")
            pkey = normalize_permit_key(raw_pid)
            if not pkey:
                continue

            stat = classify_permit_status(row.get("status"))
            if stat == "Cancelled":
                cancelled_count += 1

            dept = str(row.get("department", "")) if pd.notna(row.get("department")) else ""
            tag = str(row.get("equipment_tag", "")) if pd.notna(row.get("equipment_tag")) else ""
            pstart = str(row.get("permit_start", "")) if pd.notna(row.get("permit_start")) else ""
            pend = str(row.get("permit_end", "")) if pd.notna(row.get("permit_end")) else ""
            iss = str(row.get("issue_date", "")) if pd.notna(row.get("issue_date")) else ""
            rem = str(row.get("remarks", "")) if pd.notna(row.get("remarks")) else ""

            # Check late entry
            is_late = False
            if baseline_as_of_str and pstart and pstart[:10] <= baseline_as_of_str:
                is_late = True
                late_entries_count += 1

            # Row hash
            row_dict = {"dept": dept, "tag": tag, "pstart": pstart, "pend": pend, "stat": stat, "rem": rem}
            rhash = hashlib.sha256(str(sorted(row_dict.items())).encode("utf-8")).hexdigest()[:12]

            # Check existing permit
            existing = conn.execute(
                text(
                    """
                    SELECT id, row_hash, status, department, equipment_tag, permit_start, permit_end, remarks
                    FROM ptw_permits
                    WHERE permit_key = :pk
                    ORDER BY id DESC LIMIT 1
                    """
                ),
                {"pk": pkey},
            ).fetchone()

            if not existing:
                # Insert new permit
                conn.execute(
                    text(
                        """
                        INSERT INTO ptw_permits
                            (upload_id, permit_key, permit_id, department, equipment_tag,
                             permit_start, permit_end, issue_date, period, status, remarks, row_hash, first_seen_upload_id)
                        VALUES
                            (:uid, :pk, :pid, :dept, :tag, :ps, :pe, :iss, :period, :stat, :rem, :rh, :uid)
                        """
                    ),
                    {
                        "uid": upload_id, "pk": pkey, "pid": pkey, "dept": dept, "tag": tag,
                        "ps": pstart, "pe": pend, "iss": iss, "period": period, "stat": stat,
                        "rem": rem, "rh": rhash,
                    },
                )
                inserted_count += 1
            elif existing[1] != rhash:
                # Changed fields -> update and log history
                old_id, old_hash, old_stat, old_dept, old_tag, old_ps, old_pe, old_rem = existing
                fields_to_check = [
                    ("status", old_stat, stat),
                    ("department", old_dept, dept),
                    ("equipment_tag", old_tag, tag),
                    ("permit_start", old_ps, pstart),
                    ("permit_end", old_pe, pend),
                    ("remarks", old_rem, rem),
                ]
                for fname, old_val, new_val in fields_to_check:
                    if str(old_val) != str(new_val):
                        conn.execute(
                            text(
                                """
                                INSERT INTO ptw_permit_history
                                    (permit_key, upload_id, field_name, old_value, new_value, changed_at)
                                VALUES
                                    (:pk, :uid, :fn, :ov, :nv, :ca)
                                """
                            ),
                            {"pk": pkey, "uid": upload_id, "fn": fname, "ov": str(old_val), "nv": str(new_val), "ca": now},
                        )

                conn.execute(
                    text(
                        """
                        UPDATE ptw_permits
                        SET upload_id = :uid, department = :dept, equipment_tag = :tag,
                            permit_start = :ps, permit_end = :pe, status = :stat, remarks = :rem, row_hash = :rh
                        WHERE permit_key = :pk
                        """
                    ),
                    {"uid": upload_id, "dept": dept, "tag": tag, "ps": pstart, "pe": pend, "stat": stat, "rem": rem, "rh": rhash, "pk": pkey},
                )
                updated_count += 1
            else:
                unchanged_count += 1

            # Insert line item
            qty_val, uom_val = parse_quantity_and_uom(row.get("quantity_used"))
            desc_text = str(row.get("spare_description_text", ""))

            conn.execute(
                text(
                    """
                    INSERT INTO ptw_lines
                        (permit_key, upload_id, line_number, raw_description,
                         cleaned_description, quantity, uom, status)
                    VALUES
                        (:pk, :uid, 1, :rdesc, :cdesc, :qty, :uom, 'Unmatched')
                    """
                ),
                {
                    "pk": pkey,
                    "uid": upload_id,
                    "rdesc": desc_text,
                    "cdesc": desc_text.lower().strip(),
                    "qty": qty_val,
                    "uom": uom_val,
                },
            )

    return {
        "inserted": inserted_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "late_entries": late_entries_count,
        "cancelled": cancelled_count,
        "total_lines": len(cleaned_df),
    }

