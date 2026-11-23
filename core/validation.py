"""
File validation and normalization engine.
Handles Excel/CSV parsing, header detection, column auto-mapping,
data cleaning, and validation rules specified in Section 7.
"""

import hashlib
import io
import re
from datetime import datetime, date, timezone
from typing import Dict, Any, List, Tuple, Optional
import pandas as pd
from sqlalchemy import text

from app.ui.demo_data import INVENTORY_FIELDS, PTW_FIELDS
from core.database import load_column_mapping

try:
    from rapidfuzz import fuzz
    def _fuzzy_score(a: str, b: str) -> float:
        return float(fuzz.token_sort_ratio(str(a).lower(), str(b).lower()))
except ImportError:
    def _fuzzy_score(a: str, b: str) -> float:
        set_a = set(str(a).lower().replace("_", " ").split())
        set_b = set(str(b).lower().replace("_", " ").split())
        if not set_a or not set_b:
            return 0.0
        return 100.0 * len(set_a & set_b) / max(len(set_a), len(set_b))

INVENTORY_REQUIRED_FIELDS = ["material_code", "spare_description", "quantity_in_stock"]
PTW_REQUIRED_FIELDS = ["permit_id", "spare_description_text", "quantity_used"]

# Field alias aliases for auto-matching
COMMON_FIELD_ALIASES = {
    "material_code": ["spare_code", "item_code", "part_number", "part_code", "mat_code", "code"],
    "spare_description": ["item_description", "description", "spare_name", "part_name", "material_description"],
    "quantity_in_stock": ["stock_qty", "qty_in_stock", "quantity", "current_stock", "qty", "balance_qty"],
    "unit_price": ["unit_cost", "price", "rate", "cost", "avg_cost"],
    "storage_location": ["location", "bin", "bin_location", "warehouse", "loc"],
    "department": ["dept", "section", "cost_center"],
    "equipment_tag": ["tag", "equipment", "asset_tag", "unit"],
    "permit_id": ["ptw_no", "permit_no", "ptw_id", "work_permit_no"],
    "spare_description_text": ["spare_used", "description", "material_text", "spare_description", "items_used"],
    "quantity_used": ["qty_used", "quantity", "qty", "consumed_qty"],
    "permit_start": ["start_date", "start_time", "commencement_date"],
    "permit_end": ["end_date", "end_time", "completion_date"],
    "status": ["permit_status", "job_status", "state"],
}


def compute_file_hash(content_bytes: bytes) -> str:
    """Calculate SHA-256 hash of file contents."""
    return hashlib.sha256(content_bytes).hexdigest()


def detect_header_row(df_raw: pd.DataFrame) -> int:
    """
    Auto-detect the table header row by scanning first 15 rows
    for strings matching common column keywords.
    """
    best_idx = 0
    max_score = -1

    sample_rows = min(15, len(df_raw))
    all_keywords = {
        "code", "spare", "part", "item", "qty", "quantity", "stock", "price",
        "cost", "permit", "tag", "dept", "department", "date", "status", "desc"
    }

    for i in range(sample_rows):
        row_vals = [str(x).lower().strip() for x in df_raw.iloc[i].dropna()]
        if not row_vals:
            continue
        score = sum(any(k in val for k in all_keywords) for val in row_vals)
        if score > max_score:
            max_score = score
            best_idx = i

    return best_idx


def parse_raw_file(
    uploaded_file,
    sheet_name: Optional[str] = None,
    header_override: Optional[int] = None,
) -> Tuple[Optional[pd.DataFrame], List[str], Optional[str]]:
    """
    Parse CSV or Excel file into DataFrame with header detection.
    Returns: (df, available_sheets, error_message)
    """
    fname = uploaded_file.name.lower()
    sheets: List[str] = []

    try:
        if fname.endswith((".xlsx", ".xls")):
            xl = pd.ExcelFile(uploaded_file)
            sheets = xl.sheet_names
            target_sheet = sheet_name or sheets[0]

            if header_override is not None:
                df = xl.parse(target_sheet, header=header_override)
            else:
                # Preview without header first to detect
                df_preview = xl.parse(target_sheet, header=None, nrows=20)
                detected_hdr = detect_header_row(df_preview)
                df = xl.parse(target_sheet, header=detected_hdr)
            return df, sheets, None

        elif fname.endswith(".csv"):
            content = uploaded_file.read()
            uploaded_file.seek(0)
            try:
                text_content = content.decode("utf-8")
            except UnicodeDecodeError:
                text_content = content.decode("latin-1")

            if header_override is not None:
                df = pd.read_csv(io.StringIO(text_content), header=header_override)
            else:
                df_preview = pd.read_csv(io.StringIO(text_content), header=None, nrows=20)
                detected_hdr = detect_header_row(df_preview)
                df = pd.read_csv(io.StringIO(text_content), header=detected_hdr)
            return df, [], None

        else:
            return None, [], "Unsupported file format. Please upload CSV or Excel (.xlsx, .xls)."

    except Exception as exc:
        return None, [], f"Corrupt or unreadable file: {str(exc)}"


def suggest_column_mappings(
    columns: List[str],
    dataset: str,
    saved_mappings: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """
    Auto-suggest column mapping for standard fields.
    Checks saved mappings first, then exact/aliases, then fuzzy matching.
    Each uploaded column is uniquely assigned to avoid collision.
    """
    target_fields = (
        ["material_code", "spare_description", "quantity_in_stock", "unit_price",
         "storage_location", "department", "equipment_tag", "last_used_date", "expiry_date", "category"]
        if dataset == "inventory"
        else ["permit_id", "spare_description_text", "quantity_used", "permit_start",
              "permit_end", "department", "equipment_tag", "status", "remarks"]
    )

    mapping: Dict[str, str] = {}
    used_cols = set()
    cols_clean = {c: str(c).strip() for c in columns}
    cols_lower = {str(c).strip().lower(): c for c in columns}

    # Pass 1: exact and alias matches
    for std_f in target_fields:
        # 1. Saved mapping
        if saved_mappings and std_f in saved_mappings and saved_mappings[std_f] in columns:
            col = saved_mappings[std_f]
            if col not in used_cols:
                mapping[std_f] = col
                used_cols.add(col)
                continue

        # 2. Exact match on field name
        if std_f in cols_lower:
            col = cols_lower[std_f]
            if col not in used_cols:
                mapping[std_f] = col
                used_cols.add(col)
                continue

        # 3. Alias dictionary match
        aliases = COMMON_FIELD_ALIASES.get(std_f, [])
        for a in aliases:
            if a in cols_lower:
                col = cols_lower[a]
                if col not in used_cols:
                    mapping[std_f] = col
                    used_cols.add(col)
                    break

    # Pass 2: Fuzzy match for remaining unmapped target fields using unmapped source columns
    for std_f in target_fields:
        if std_f in mapping:
            continue
        aliases = COMMON_FIELD_ALIASES.get(std_f, [])
        best_col = None
        best_score = 0.0
        for col_raw, col_str in cols_clean.items():
            if col_raw in used_cols:
                continue
            score = _fuzzy_score(std_f, col_str)
            for a in aliases:
                score = max(score, _fuzzy_score(a, col_str))
            if score > best_score:
                best_score = score
                best_col = col_raw

        if best_score >= 70 and best_col:
            mapping[std_f] = best_col
            used_cols.add(best_col)

    return mapping


def detect_wrong_slot(columns: List[str], expected_dataset: str) -> Optional[str]:
    """
    Check if a file intended for 'inventory' looks like 'ptw' (or vice versa).
    """
    col_str = " ".join([str(c).lower() for c in columns])
    has_ptw_markers = any(k in col_str for k in ["permit", "ptw", "permit_id", "remarks", "job_status"])
    has_inv_markers = any(k in col_str for k in ["stock", "quantity_in_stock", "unit_cost", "unit_price", "material_code", "bin"])

    if expected_dataset == "inventory" and has_ptw_markers and not has_inv_markers:
        return "This file contains permit columns and appears to be a PTW file. Please upload to the PTW partition."
    elif expected_dataset == "ptw" and has_inv_markers and not has_ptw_markers:
        return "This file contains stock/inventory columns and appears to be an Inventory file. Please upload to the Inventory partition."
    return None


def clean_material_code(val: Any) -> str:
    """
    Read material code strictly as TEXT:
    - Preserve leading zeros
    - Strip trailing '.0' from float conversions
    - Reject scientific notation (e.g. 1.2E+11)
    """
    if pd.isna(val) or val is None:
        return ""
    val_str = str(val).strip()

    # Reject scientific notation
    if re.search(r"^\d+(\.\d+)?[eE][+-]?\d+$", val_str):
        raise ValueError(f"Material code '{val_str}' is mangled in scientific notation.")

    # Strip trailing float '.0'
    if val_str.endswith(".0") and val_str[:-2].replace("-", "").isdigit():
        val_str = val_str[:-2]

    return val_str


def validate_and_normalize(
    df_raw: pd.DataFrame,
    dataset: str,
    mapping: Dict[str, str],
    as_of_date: date,
    engine,
    file_hash: str,
) -> Dict[str, Any]:
    """
    Execute Section 7 validation rules:
    - Blocking error checks
    - Auto-fixes (blank rows, Total rows, spaces, numbers with currency)
    - Row rejections and warnings
    """
    errors: List[str] = []
    warnings: List[str] = []
    rejected_rows: List[Dict[str, Any]] = []

    if df_raw.empty:
        return {"valid": False, "errors": ["File contains zero rows."], "warnings": [], "rejected_rows": [], "cleaned_df": None}

    # Slot check
    slot_err = detect_wrong_slot(df_raw.columns.tolist(), dataset)
    if slot_err:
        errors.append(slot_err)

    # Date check
    if as_of_date > date.today():
        errors.append(f"Data as-of date ({as_of_date}) cannot be in the future.")

    # Duplicate file hash check
    try:
        with engine.connect() as conn:
            period_str = as_of_date.strftime("%b %Y")
            row = conn.execute(
                text(
                    """
                    SELECT id FROM uploads
                    WHERE dataset = :ds AND file_hash = :h AND status = 'active'
                    """
                ),
                {"ds": dataset, "h": file_hash},
            ).fetchone()
            if row:
                errors.append("Identical file hash already committed for this dataset. Use existing data or upload a revised file.")
    except Exception as exc:
        pass

    # Required fields mapped check
    req_fields = INVENTORY_REQUIRED_FIELDS if dataset == "inventory" else PTW_REQUIRED_FIELDS
    missing_req = [f for f in req_fields if f not in mapping or not mapping[f]]
    if missing_req:
        errors.append(f"Required column(s) not mapped: {', '.join(missing_req)}")

    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings, "rejected_rows": [], "cleaned_df": None}

    # Auto-fixes
    df = df_raw.copy()

    # Invert mapping: source_col -> standard_field (prioritizing required fields)
    inv_map = {}
    for std, src in mapping.items():
        if src in df.columns and std not in req_fields and src not in inv_map:
            inv_map[src] = std
    for std in req_fields:
        src = mapping.get(std)
        if src and src in df.columns:
            inv_map[src] = std
    df = df.rename(columns=inv_map)

    # Drop fully blank rows
    orig_count = len(df)
    df = df.dropna(how="all").reset_index(drop=True)

    # Filter out Total / Grand Total / Subtotal rows
    total_keywords = ["total", "grand total", "sub total", "subtotal"]
    key_col = "material_code" if dataset == "inventory" else "permit_id"
    desc_col = "spare_description" if dataset == "inventory" else "spare_description_text"

    is_total_mask = pd.Series(False, index=df.index)
    for col_chk in [key_col, desc_col]:
        if col_chk in df.columns:
            is_total_mask |= df[col_chk].astype(str).str.strip().str.lower().isin(total_keywords)

    total_rows_count = int(is_total_mask.sum())
    if total_rows_count > 0:
        for idx in df[is_total_mask].index:
            rejected_rows.append({
                "row_number": int(idx) + 1,
                "reason": "Total / summary row excluded",
                "raw_content": str(df.iloc[idx].to_dict()),
            })
        df = df[~is_total_mask].reset_index(drop=True)

    # Clean Material Codes / Permit IDs strictly as TEXT
    valid_rows_mask = pd.Series(True, index=df.index)

    if dataset == "inventory":
        cleaned_codes = []
        for idx, val in enumerate(df["material_code"]):
            try:
                c = clean_material_code(val)
                if not c:
                    valid_rows_mask.iloc[idx] = False
                    rejected_rows.append({"row_number": idx + 1, "reason": "Missing material code", "raw_content": str(df.iloc[idx].to_dict())})
                cleaned_codes.append(c)
            except ValueError as ve:
                valid_rows_mask.iloc[idx] = False
                rejected_rows.append({"row_number": idx + 1, "reason": str(ve), "raw_content": str(df.iloc[idx].to_dict())})
                cleaned_codes.append("")
        df["material_code"] = cleaned_codes

        # Clean quantity
        if "quantity_in_stock" in df.columns:
            qty_clean = []
            for idx, val in enumerate(df["quantity_in_stock"]):
                if not valid_rows_mask.iloc[idx]:
                    qty_clean.append(0.0)
                    continue
                try:
                    # Strip commas, units
                    val_str = re.sub(r"[^\d.-]", "", str(val).strip())
                    q = float(val_str) if val_str else 0.0
                    if q < 0:
                        valid_rows_mask.iloc[idx] = False
                        rejected_rows.append({"row_number": idx + 1, "reason": f"Negative quantity rejected ({q})", "raw_content": str(df.iloc[idx].to_dict())})
                        warnings.append(f"Row {idx + 1}: Negative stock quantity ({q}) was rejected.")
                    qty_clean.append(q)
                except Exception:
                    valid_rows_mask.iloc[idx] = False
                    rejected_rows.append({"row_number": idx + 1, "reason": f"Unparseable quantity '{val}'", "raw_content": str(df.iloc[idx].to_dict())})
                    qty_clean.append(0.0)
            df["quantity_in_stock"] = qty_clean

        # Clean unit price
        if "unit_price" in df.columns:
            prices = []
            for idx, val in enumerate(df["unit_price"]):
                try:
                    p_str = re.sub(r"[^\d.-]", "", str(val).strip())
                    p = float(p_str) if p_str else None
                    if p is None or p <= 0:
                        warnings.append(f"Row {idx + 1}: Zero or missing price for {df['material_code'].iloc[idx]}.")
                    prices.append(p)
                except Exception:
                    prices.append(None)
            df["unit_price"] = prices
        else:
            df["unit_price"] = None

    elif dataset == "ptw":
        # PTW cleaning
        cleaned_permits = []
        for idx, val in enumerate(df["permit_id"]):
            val_str = str(val).strip().upper() if pd.notna(val) else ""
            if not val_str:
                valid_rows_mask.iloc[idx] = False
                rejected_rows.append({"row_number": idx + 1, "reason": "Missing permit ID", "raw_content": str(df.iloc[idx].to_dict())})
            cleaned_permits.append(val_str)
        df["permit_id"] = cleaned_permits

        # Clean quantity used
        if "quantity_used" in df.columns:
            qty_used = []
            for idx, val in enumerate(df["quantity_used"]):
                if not valid_rows_mask.iloc[idx]:
                    qty_used.append(0.0)
                    continue
                try:
                    q_str = re.sub(r"[^\d.-]", "", str(val).strip())
                    q = float(q_str) if q_str else 0.0
                    qty_used.append(q)
                except Exception:
                    qty_used.append(0.0)
            df["quantity_used"] = qty_used

    # Retain only valid rows
    df_valid = df[valid_rows_mask].reset_index(drop=True)

    if df_valid.empty:
        errors.append("File contains zero valid data rows after validation.")
        return {"valid": False, "errors": errors, "warnings": warnings, "rejected_rows": rejected_rows, "cleaned_df": None}

    return {
        "valid": True,
        "errors": [],
        "warnings": warnings,
        "rejected_rows": rejected_rows,
        "cleaned_df": df_valid,
        "total_rows_input": orig_count,
        "valid_rows_count": len(df_valid),
        "rejected_rows_count": len(rejected_rows),
    }

