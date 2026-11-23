"""
Data pipeline module: Stage, Preview, Commit, Rollback, Archival, and Backups.
Implements the atomic staging pipeline specified in Section 8.
"""

import os
import shutil
import glob
from datetime import datetime, timezone, date
from typing import Optional, Dict, Any, List, Tuple
import pandas as pd
from sqlalchemy import text

from core.audit import log_audit_event
from core.auth import require_admin
from core.database import get_data_dir, get_default_db_path

logger = logging = __import__("logging").getLogger(__name__)


def archive_raw_file(
    file_bytes: bytes,
    dataset: str,
    period: str,
    filename: str,
) -> str:
    """
    Save the raw uploaded file read-only at:
    data/raw/{dataset}/{clean_period}_{hash8}{ext}
    """
    clean_period = period.replace(" ", "_").replace("/", "-")
    hash8 = __import__("hashlib").sha256(file_bytes).hexdigest()[:8]
    ext = os.path.splitext(filename)[1].lower()

    target_dir = os.path.abspath(os.path.join(get_data_dir(), "raw", dataset))
    os.makedirs(target_dir, exist_ok=True)

    dest_filename = f"{clean_period}_{hash8}{ext}"
    dest_path = os.path.join(target_dir, dest_filename)

    if os.path.exists(dest_path):
        return dest_path

    with open(dest_path, "wb") as f:
        f.write(file_bytes)

    # Set read-only permissions
    try:
        os.chmod(dest_path, 0o444)
    except Exception:
        pass

    return dest_path



def backup_database(
    db_path: Optional[str] = None,
    max_backups: int = 12,
) -> Optional[str]:
    """
    Create a timestamped backup of the SQLite database in data/backups/.
    Retains the latest `max_backups` copies.
    """
    abs_db = os.path.abspath(db_path or get_default_db_path())
    if not os.path.exists(abs_db):
        return None

    backup_dir = os.path.abspath(os.path.join(get_data_dir(), "backups"))
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(backup_dir, f"reconciliation_{timestamp}.db")

    shutil.copy2(abs_db, backup_file)

    # Rotate old backups
    all_backups = sorted(glob.glob(os.path.join(backup_dir, "reconciliation_*.db")))
    if len(all_backups) > max_backups:
        for old_b in all_backups[:-max_backups]:
            try:
                os.remove(old_b)
            except Exception:
                pass

    return backup_file


def find_baseline_upload(
    engine,
    dataset: str,
    as_of_date_str: str,
) -> Optional[Dict[str, Any]]:
    """
    Find the latest ACTIVE snapshot with an earlier as-of date (by data date).
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, period, as_of_date, version, row_count, file_name, file_hash
                    FROM uploads
                    WHERE dataset = :ds AND status = 'active' AND as_of_date < :as_of
                    ORDER BY as_of_date DESC, id DESC
                    LIMIT 1
                    """
                ),
                {"ds": dataset, "as_of": as_of_date_str},
            ).fetchone()

            if not row:
                return None

            return {
                "id": row[0],
                "period": row[1],
                "as_of_date": row[2],
                "version": row[3],
                "row_count": row[4],
                "file_name": row[5],
                "file_hash": row[6],
            }
    except Exception as exc:
        logger.error("Failed to find baseline upload: %s", exc)
        return None


def detect_period_conflict(
    engine,
    dataset: str,
    period: str,
) -> Optional[Dict[str, Any]]:
    """
    Check if an active snapshot already exists for this exact period.
    Returns metadata dict if found, else None.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, period, as_of_date, version, row_count, file_name, uploaded_at, uploaded_by
                    FROM uploads
                    WHERE dataset = :ds AND period = :p AND status = 'active'
                    ORDER BY version DESC, id DESC
                    LIMIT 1
                    """
                ),
                {"ds": dataset, "p": period},
            ).fetchone()

            if not row:
                return None

            return {
                "id": row[0],
                "period": row[1],
                "as_of_date": row[2],
                "version": row[3],
                "row_count": row[4],
                "file_name": row[5],
                "uploaded_at": row[6],
                "uploaded_by": row[7],
            }
    except Exception as exc:
        logger.error("Failed to detect period conflict: %s", exc)
        return None


def stage_inventory_upload(
    engine,
    period: str,
    as_of_date: date,
    filename: str,
    file_bytes: bytes,
    file_hash: str,
    cleaned_df: pd.DataFrame,
    rejected_rows: List[Dict[str, Any]],
    user: str,
) -> int:
    """
    Stage an inventory snapshot. Inserts upload record (status='staged')
    and inventory_items records.
    """
    stored_path = archive_raw_file(file_bytes, "inventory", period, filename)
    now = datetime.now(timezone.utc).isoformat()
    as_of_str = as_of_date.strftime("%Y-%m-%d")

    baseline = find_baseline_upload(engine, "inventory", as_of_str)
    is_baseline = 1 if baseline is None else 0

    with engine.begin() as conn:
        # Insert staged upload record
        res = conn.execute(
            text(
                """
                INSERT INTO uploads
                    (dataset, period, as_of_date, file_name, file_hash, stored_path,
                     uploaded_by, uploaded_at, status, version, row_count, rejected_count, is_baseline)
                VALUES
                    ('inventory', :p, :aod, :fn, :h, :sp, :ub, :ua, 'staged', 1, :rc, :rjc, :ib)
                """
            ),
            {
                "p": period,
                "aod": as_of_str,
                "fn": filename,
                "h": file_hash,
                "sp": stored_path,
                "ub": user,
                "ua": now,
                "rc": len(cleaned_df),
                "rjc": len(rejected_rows),
                "ib": is_baseline,
            },
        )
        upload_id = res.lastrowid

        # Insert rejected rows
        for r in rejected_rows:
            conn.execute(
                text(
                    """
                    INSERT INTO rejected_rows (upload_id, dataset, row_number, raw_content, reason)
                    VALUES (:uid, 'inventory', :rn, :raw, :reason)
                    """
                ),
                {
                    "uid": upload_id,
                    "rn": r.get("row_number", 0),
                    "raw": str(r.get("raw_content", "")),
                    "reason": r.get("reason", ""),
                },
            )

        # Insert inventory items
        items_to_insert = []
        for _, row in cleaned_df.iterrows():
            mcode = str(row.get("material_code", "")).strip()
            loc = str(row.get("storage_location", "")).strip() if pd.notna(row.get("storage_location")) else ""
            item_key = f"{mcode}|{loc}" if loc else mcode

            qty = float(row.get("quantity_in_stock", 0.0))
            price = float(row.get("unit_price")) if pd.notna(row.get("unit_price")) else None
            val = qty * price if price is not None else 0.0

            items_to_insert.append({
                "uid": upload_id,
                "ikey": item_key,
                "mcode": mcode,
                "loc": loc,
                "desc": str(row.get("spare_description", "")),
                "uom": str(row.get("uom", "")) if pd.notna(row.get("uom")) else "",
                "qty": qty,
                "price": price,
                "val": val,
                "lud": str(row.get("last_used_date", "")) if pd.notna(row.get("last_used_date")) else "",
                "lrd": str(row.get("date_received", "")) if pd.notna(row.get("date_received")) else "",
                "exp": str(row.get("expiry_date", "")) if pd.notna(row.get("expiry_date")) else "",
                "dept": str(row.get("department", "")) if pd.notna(row.get("department")) else "",
                "cat": str(row.get("category", "")) if pd.notna(row.get("category")) else "",
                "crit": str(row.get("criticality_flag", "")) if pd.notna(row.get("criticality_flag")) else "",
                "eq_tag": str(row.get("equipment_tag", "")) if pd.notna(row.get("equipment_tag")) else "",
            })

        if items_to_insert:
            conn.execute(
                text(
                    """
                    INSERT INTO inventory_items
                        (upload_id, item_key, material_code, location, description, uom,
                         qty, unit_price, value, last_used_date, last_received_date,
                         expiry_date, department, category, criticality, equipment_tag)
                    VALUES
                        (:uid, :ikey, :mcode, :loc, :desc, :uom, :qty, :price, :val,
                         :lud, :lrd, :exp, :dept, :cat, :crit, :eq_tag)
                    """
                ),
                items_to_insert,
            )


    log_audit_event(
        engine,
        event_type="upload_staged",
        details=f"Inventory staged for period '{period}' ({len(cleaned_df)} items)",
        username=user,
    )
    return upload_id


def stage_ptw_upload(
    engine,
    period: str,
    as_of_date: date,
    filename: str,
    file_bytes: bytes,
    file_hash: str,
    cleaned_df: pd.DataFrame,
    rejected_rows: List[Dict[str, Any]],
    user: str,
) -> int:
    """
    Stage a PTW upload. Inserts upload record and merges permits/lines.
    """
    from core.ptw_merge import merge_and_upsert_ptw

    stored_path = archive_raw_file(file_bytes, "ptw", period, filename)
    now = datetime.now(timezone.utc).isoformat()
    as_of_str = as_of_date.strftime("%Y-%m-%d")

    baseline = find_baseline_upload(engine, "ptw", as_of_str)
    baseline_as_of = baseline["as_of_date"] if baseline else None

    with engine.begin() as conn:
        res = conn.execute(
            text(
                """
                INSERT INTO uploads
                    (dataset, period, as_of_date, file_name, file_hash, stored_path,
                     uploaded_by, uploaded_at, status, version, row_count, rejected_count)
                VALUES
                    ('ptw', :p, :aod, :fn, :h, :sp, :ub, :ua, 'staged', 1, :rc, :rjc)
                """
            ),
            {
                "p": period,
                "aod": as_of_str,
                "fn": filename,
                "h": file_hash,
                "sp": stored_path,
                "ub": user,
                "ua": now,
                "rc": len(cleaned_df),
                "rjc": len(rejected_rows),
            },
        )
        upload_id = res.lastrowid

        for r in rejected_rows:
            conn.execute(
                text(
                    """
                    INSERT INTO rejected_rows (upload_id, dataset, row_number, raw_content, reason)
                    VALUES (:uid, 'ptw', :rn, :raw, :reason)
                    """
                ),
                {
                    "uid": upload_id,
                    "rn": r.get("row_number", 0),
                    "raw": str(r.get("raw_content", "")),
                    "reason": r.get("reason", ""),
                },
            )

    # Perform upsert
    merge_and_upsert_ptw(engine, upload_id, cleaned_df, period, baseline_as_of, user)

    log_audit_event(
        engine,
        event_type="upload_staged",
        details=f"PTW staged for period '{period}' ({len(cleaned_df)} permits)",
        username=user,
    )
    return upload_id


    log_audit_event(
        engine,
        event_type="upload_staged",
        details=f"PTW staged for period '{period}' ({len(cleaned_df)} permits)",
        username=user,
    )
    return upload_id


def commit_and_publish(
    engine,
    upload_id: int,
    user: str,
    ip_address: str = "127.0.0.1",
    replace_existing: bool = False,
) -> Tuple[bool, str]:
    """
    Atomic publish:
    1. Creates DB backup
    2. Opens transaction
    3. If replacing existing: supersedes old version
    4. Marks staged upload as active
    5. Commits and records audit log
    Any exception rolls back completely!
    """
    backup_database()

    try:
        with engine.begin() as conn:
            # Query staged upload
            row = conn.execute(
                text(
                    """
                    SELECT dataset, period, as_of_date, version, file_name
                    FROM uploads
                    WHERE id = :uid AND status = 'staged'
                    """
                ),
                {"uid": upload_id},
            ).fetchone()

            if not row:
                return False, "Staged upload not found or already published."

            dataset, period, as_of, current_ver, fname = row

            new_version = 1
            if replace_existing:
                # Find old active upload to supersede
                old_row = conn.execute(
                    text(
                        """
                        SELECT id, version FROM uploads
                        WHERE dataset = :ds AND period = :p AND status = 'active'
                        ORDER BY version DESC LIMIT 1
                        """
                    ),
                    {"ds": dataset, "p": period},
                ).fetchone()

                if old_row:
                    old_id, old_ver = old_row
                    conn.execute(
                        text("UPDATE uploads SET status = 'superseded' WHERE id = :oid"),
                        {"oid": old_id},
                    )
                    new_version = old_ver + 1

            # Mark staged upload as active
            conn.execute(
                text(
                    """
                    UPDATE uploads
                    SET status = 'active', version = :ver
                    WHERE id = :uid
                    """
                ),
                {"ver": new_version, "uid": upload_id},
            )

        log_audit_event(
            engine,
            event_type="upload_published",
            details=f"Published {dataset} for period '{period}' (v{new_version})",
            username=user,
            ip_address=ip_address,
        )

        # Trigger savings opportunity calculation and automated data check (Step 2)
        try:
            from core.savings import compute_opportunities_and_excess, run_data_check
            if dataset == "inventory":
                compute_opportunities_and_excess(engine, period)
            run_data_check(engine)
        except Exception as s_exc:
            logger.warning("Post-publish savings recompute: %s", s_exc)

        return True, f"Successfully published {dataset} for {period}."
    except Exception as exc:
        logger.error("Commit and publish failed (rolled back): %s", exc)
        return False, f"Publication error: {str(exc)}"


def discard_staged_upload(engine, upload_id: int, user: str) -> None:
    """
    Discard a staged upload and all its child records.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM inventory_items WHERE upload_id = :uid"), {"uid": upload_id})
            conn.execute(text("DELETE FROM inventory_diff WHERE upload_id = :uid"), {"uid": upload_id})
            conn.execute(text("DELETE FROM ptw_lines WHERE upload_id = :uid"), {"uid": upload_id})
            conn.execute(text("DELETE FROM ptw_permits WHERE upload_id = :uid"), {"uid": upload_id})
            conn.execute(text("DELETE FROM rejected_rows WHERE upload_id = :uid"), {"uid": upload_id})
            conn.execute(text("DELETE FROM uploads WHERE id = :uid"), {"uid": upload_id})

        log_audit_event(
            engine,
            event_type="upload_discarded",
            details=f"Discarded staged upload #{upload_id}",
            username=user,
        )
    except Exception as exc:
        logger.error("Failed to discard upload: %s", exc)


def void_latest_upload(engine, upload_id: int, user: str) -> Tuple[bool, str]:
    """
    Admin-only: Void the active upload and restore previous superseded version.
    """
    backup_database()

    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT dataset, period, version FROM uploads WHERE id = :uid AND status = 'active'"),
                {"uid": upload_id},
            ).fetchone()

            if not row:
                return False, "Active upload not found."

            dataset, period, ver = row

            # Mark as voided
            conn.execute(text("UPDATE uploads SET status = 'voided' WHERE id = :uid"), {"uid": upload_id})

            # Look for previous superseded version
            prev_row = conn.execute(
                text(
                    """
                    SELECT id FROM uploads
                    WHERE dataset = :ds AND period = :p AND status = 'superseded'
                    ORDER BY version DESC LIMIT 1
                    """
                ),
                {"ds": dataset, "p": period},
            ).fetchone()

            if prev_row:
                conn.execute(text("UPDATE uploads SET status = 'active' WHERE id = :pid"), {"pid": prev_row[0]})

        log_audit_event(
            engine,
            event_type="upload_voided",
            details=f"Voided {dataset} #{upload_id} for period '{period}'",
            username=user,
        )

        try:
            from core.savings import compute_opportunities_and_excess, run_data_check
            if dataset == "inventory":
                compute_opportunities_and_excess(engine, period)
            run_data_check(engine)
        except Exception:
            pass

        return True, f"Upload #{upload_id} successfully voided."
    except Exception as exc:
        logger.error("Failed to void upload: %s", exc)
        return False, str(exc)
