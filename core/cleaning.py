"""
Data cleaning module for the Inventory-PTW Reconciliation Dashboard.
Handles text normalization, date parsing, deduplication, and quality reporting.
"""

import json
import logging
import re
import string
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

# Optional dependency: dateutil for fallback date parsing
try:
    from dateutil import parser as dateutil_parser
    _DATEUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _DATEUTIL_AVAILABLE = False

# Module-level logger
logger = logging.getLogger(__name__)

# ── Column classification ─────────────────────────────────────────────────────

# Fields whose content should be text-normalized
_TEXT_COLUMNS = {
    "spare_description",
    "spare_description_text",
    "remarks",
    "category",
    "department",
    "criticality_flag",
}

# Fields that should be parsed as dates
_DATE_COLUMNS = {
    "date_received",
    "last_used_date",
    "expiry_date",
    "permit_start",
    "permit_end",
}

# Fields that should be coerced to numeric quantities
_QUANTITY_COLUMNS = {
    "quantity_in_stock",
    "quantity_used",
    "unit_cost",
}


# ── 1. Abbreviation loading ───────────────────────────────────────────────────

def load_abbreviations(path: str = "config/abbreviations.json") -> dict:
    """
    Load an abbreviation expansion dictionary from a JSON file.

    The JSON file is expected to map short forms to their full equivalents,
    e.g. {"brg": "bearing", "mtg": "mounting"}.

    Parameters
    ----------
    path : str
        Path to the JSON file (relative or absolute).

    Returns
    -------
    dict
        Abbreviation map, or {} if the file is missing or unreadable.
    """
    abs_path = Path(path)

    # Warn and return empty dict rather than crashing if the file is absent
    if not abs_path.exists():
        warnings.warn(
            f"Abbreviations file not found at '{path}'. Proceeding without abbreviation expansion.",
            UserWarning,
            stacklevel=2,
        )
        return {}

    try:
        with abs_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        # Normalise all keys to lowercase for case-insensitive lookup later
        normalised = {k.lower(): v for k, v in data.items()}
        logger.info("Loaded %d abbreviations from '%s'.", len(normalised), path)
        return normalised
    except Exception as exc:
        logger.error("Could not read abbreviations file '%s': %s", path, exc)
        return {}


# ── 2. Text normalisation ─────────────────────────────────────────────────────

def normalize_text(text, abbreviations: Optional[dict] = None) -> str:
    """
    Normalise a raw text value into a clean, comparable string.

    Processing steps (in order):
      a. Handle None / NaN → return "".
      b. Convert to str and strip surrounding whitespace.
      c. Lowercase the entire string.
      d. Remove punctuation EXCEPT hyphens that sit between word characters
         (e.g. "anti-friction" is preserved; a leading/trailing "-" is removed).
      e. Collapse runs of whitespace to a single space.
      f. Expand known abbreviations word-by-word.
      g. Final strip.

    Parameters
    ----------
    text : any
        Raw input (str, None, float NaN, ...).
    abbreviations : dict, optional
        Mapping of lowercase abbreviation -> expansion.

    Returns
    -------
    str
        Cleaned text, or "" for null/empty inputs.
    """
    # a. Guard against None and pandas NaN (NaN == NaN is False, so use pd.isna)
    if text is None:
        return ""
    try:
        if pd.isna(text):
            return ""
    except (TypeError, ValueError):
        pass  # pd.isna can raise on some types; treat as non-null in that case

    # b. Force to string and strip outer whitespace
    text = str(text).strip()

    # c. Lowercase for uniformity
    text = text.lower()

    # d. Remove punctuation but preserve intra-word hyphens.
    #    Strategy:
    #      1. Replace hyphens that are NOT surrounded by word chars with a space.
    #      2. Remove all remaining punctuation characters (excluding "-").
    #    Step 1: leading/trailing hyphens -> space
    text = re.sub(r"(?<!\w)-|-(?!\w)", " ", text)
    #    Step 2: strip punctuation chars (excluding "-" which is now only intra-word)
    punct_no_hyphen = string.punctuation.replace("-", "")
    # Build a translation table that maps every punct char to None (delete)
    translator = str.maketrans("", "", punct_no_hyphen)
    text = text.translate(translator)

    # e. Collapse multiple whitespace characters (spaces, tabs, newlines) to one space
    text = re.sub(r"\s+", " ", text)

    # f. Expand abbreviations using the helper function
    if abbreviations:
        text = expand_abbreviations_in_text(text, abbreviations)

    # g. Final trim after all transformations
    text = text.strip()

    return text


# ── 7. Abbreviation expansion helper ─────────────────────────────────────────

def expand_abbreviations_in_text(text: str, abbreviations: dict) -> str:
    """
    Replace known abbreviations in a whitespace-tokenised string.

    Each token is compared (case-insensitively) against the abbreviations
    dictionary; matching tokens are replaced with their expansion.
    Non-matching tokens are left unchanged.

    Parameters
    ----------
    text : str
        Already-lowercased input string.
    abbreviations : dict
        {"abbrev_lowercase": "full expansion", ...}

    Returns
    -------
    str
        Text with abbreviations expanded.
    """
    if not text or not abbreviations:
        return text

    # Split on whitespace, expand token-by-token, then rejoin
    tokens = text.split()
    expanded_tokens = [
        abbreviations.get(token, token)  # replace if found, else keep original
        for token in tokens
    ]
    return " ".join(expanded_tokens)


# ── 3. Date parsing ───────────────────────────────────────────────────────────

def parse_date(value):
    """
    Attempt to parse a value into a pandas Timestamp.

    Parsing strategy:
      1. Return NaT immediately for None / NaN inputs.
      2. Try pandas pd.to_datetime with format inference.
      3. If that fails, try dateutil.parser.parse (more lenient).
      4. If both fail, return pd.NaT.

    Parameters
    ----------
    value : any
        Raw date-like value (str, datetime, int, float, None, NaN, ...).

    Returns
    -------
    pd.Timestamp or pd.NaT
    """
    # Guard: null-like inputs
    if value is None:
        return pd.NaT
    try:
        if pd.isna(value):
            return pd.NaT
    except (TypeError, ValueError):
        pass

    # Attempt 1: pandas native parser (fast, handles most ISO and common formats)
    try:
        return pd.to_datetime(value, infer_datetime_format=True)
    except Exception:
        pass  # fall through to dateutil

    # Attempt 2: dateutil for non-standard or locale-specific strings
    if _DATEUTIL_AVAILABLE:
        try:
            return pd.Timestamp(dateutil_parser.parse(str(value)))
        except Exception:
            pass

    # Both parsers failed
    logger.debug("Could not parse date value: %r", value)
    return pd.NaT


# ── 4. Quantity cleaning ──────────────────────────────────────────────────────

def clean_quantity(value) -> Optional[float]:
    """
    Coerce a raw value to a float, stripping non-numeric characters.

    Parameters
    ----------
    value : any
        Raw quantity (str like "$1,234.56", int, float, None, NaN, ...).

    Returns
    -------
    float or None
        Parsed numeric value, or None if conversion is impossible.
    """
    # Guard against null-like inputs
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    # If already numeric, cast directly (handles int and float)
    if isinstance(value, (int, float)):
        return float(value)

    # Strip everything that is not a digit or a decimal point
    cleaned = re.sub(r"[^\d.]", "", str(value))

    if not cleaned:
        # Nothing numeric left after stripping
        return None

    try:
        return float(cleaned)
    except ValueError:
        logger.debug("Could not convert quantity value to float: %r", value)
        return None


# ── 5. DataFrame cleaning ─────────────────────────────────────────────────────

def clean_dataframe(
    df: pd.DataFrame,
    column_mapping: dict,
    dataset_type: str,
    abbreviations: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Apply the full cleaning pipeline to a raw uploaded DataFrame.

    Steps:
      a. Rename columns according to column_mapping (only columns present in
         both the mapping and the DataFrame are renamed).
      b. Normalise text columns with normalize_text().
      c. Parse date columns with parse_date().
      d. Clean quantity columns with clean_quantity().
      e. Drop exact duplicate rows.
      f. Add _cleaned_description derived from the description field.

    Parameters
    ----------
    df : pd.DataFrame
        Raw data as uploaded by the user.
    column_mapping : dict
        {"standard_field": "uploaded_column"} mapping produced by the UI.
    dataset_type : str
        "inventory" or "ptw" -- used for logging/context only.
    abbreviations : dict, optional
        Abbreviation expansion map from load_abbreviations().

    Returns
    -------
    pd.DataFrame
        Cleaned copy of the input DataFrame.
    """
    if df is None or df.empty:
        logger.warning("clean_dataframe received an empty DataFrame for '%s'.", dataset_type)
        return pd.DataFrame()

    # Work on a copy to avoid mutating the caller's data
    df_clean = df.copy()

    # ── a. Rename columns ──────────────────────────────────────────────────
    # Build an inverted mapping: uploaded_column -> standard_field
    # Only rename columns that actually exist in the DataFrame
    rename_map = {
        uploaded_col: std_field
        for std_field, uploaded_col in column_mapping.items()
        if uploaded_col in df_clean.columns
    }
    df_clean.rename(columns=rename_map, inplace=True)

    # ── b. Normalise text columns ──────────────────────────────────────────
    for col in _TEXT_COLUMNS:
        if col in df_clean.columns:
            df_clean[col] = df_clean[col].apply(
                lambda val: normalize_text(val, abbreviations)
            )

    # ── c. Parse date columns ──────────────────────────────────────────────
    for col in _DATE_COLUMNS:
        if col in df_clean.columns:
            df_clean[col] = df_clean[col].apply(parse_date)

    # ── d. Clean quantity columns ──────────────────────────────────────────
    for col in _QUANTITY_COLUMNS:
        if col in df_clean.columns:
            df_clean[col] = df_clean[col].apply(clean_quantity)

    # ── e. Remove exact duplicate rows ─────────────────────────────────────
    before_dedup = len(df_clean)
    df_clean.drop_duplicates(inplace=True)
    dupes_removed = before_dedup - len(df_clean)
    if dupes_removed:
        logger.info("Removed %d duplicate rows from '%s'.", dupes_removed, dataset_type)

    # ── f. Add _cleaned_description ────────────────────────────────────────
    # Look for the description field under its standard name or the inventory alias
    description_col = None
    for candidate in ("spare_description", "spare_description_text", "remarks"):
        if candidate in df_clean.columns:
            description_col = candidate
            break

    if description_col:
        # Apply normalize_text again on the chosen description column
        df_clean["_cleaned_description"] = df_clean[description_col].apply(
            lambda val: normalize_text(val, abbreviations)
        )
    else:
        # No description column found; create an empty placeholder
        df_clean["_cleaned_description"] = ""

    return df_clean


# ── 6. Quality report ─────────────────────────────────────────────────────────

def generate_quality_report(
    df_raw: pd.DataFrame,
    df_cleaned: pd.DataFrame,
    column_mapping: dict,
) -> dict:
    """
    Produce a summary report comparing raw vs. cleaned data quality.

    Metrics returned:
      - total_rows_input        : row count before cleaning
      - total_rows_after_dedup  : row count after deduplication
      - duplicates_removed      : difference of the above
      - missing_per_field       : {field: {"count": N, "pct": X.X}} for mapped fields
      - unparseable_dates       : {date_field: count_of_NaT} for date columns
      - quality_score           : 0-100 score (100 = fully populated)

    Parameters
    ----------
    df_raw : pd.DataFrame
        Original DataFrame before any cleaning.
    df_cleaned : pd.DataFrame
        DataFrame after the cleaning pipeline.
    column_mapping : dict
        {"standard_field": "uploaded_column"} used during cleaning.

    Returns
    -------
    dict
        Quality report with the keys described above.
    """
    report: dict = {}

    # Row counts
    total_input = len(df_raw) if df_raw is not None else 0
    total_cleaned = len(df_cleaned) if df_cleaned is not None else 0

    report["total_rows_input"] = total_input
    report["total_rows_after_dedup"] = total_cleaned
    report["duplicates_removed"] = max(0, total_input - total_cleaned)

    # Early exit if cleaned DataFrame is empty
    if df_cleaned is None or df_cleaned.empty:
        report["missing_per_field"] = {}
        report["unparseable_dates"] = {}
        report["quality_score"] = 0.0
        return report

    # ── Missing values per mapped field ────────────────────────────────────
    missing_per_field: dict = {}
    for std_field in column_mapping.keys():
        if std_field not in df_cleaned.columns:
            continue  # field was not retained after rename
        col = df_cleaned[std_field]
        # Count nulls (None, NaN, NaT) and empty strings as "missing"
        null_mask = col.isna()
        empty_mask = col.astype(str).str.strip().eq("")
        missing_count = int((null_mask | empty_mask).sum())
        pct = round(missing_count / total_cleaned * 100, 1) if total_cleaned else 0.0
        missing_per_field[std_field] = {"count": missing_count, "pct": pct}

    report["missing_per_field"] = missing_per_field

    # ── Unparseable dates (NaT count after parse_date) ─────────────────────
    unparseable_dates: dict = {}
    for col_name in _DATE_COLUMNS:
        if col_name in df_cleaned.columns:
            nat_count = int(df_cleaned[col_name].isna().sum())
            unparseable_dates[col_name] = nat_count

    report["unparseable_dates"] = unparseable_dates

    # ── Quality score: 100 minus weighted average missing percentage ────────
    if missing_per_field:
        avg_missing_pct = sum(
            info["pct"] for info in missing_per_field.values()
        ) / len(missing_per_field)
        quality_score = round(max(0.0, 100.0 - avg_missing_pct), 1)
    else:
        quality_score = 100.0  # no mapped fields to check -> assume perfect

    report["quality_score"] = quality_score

    return report


# ── Demo / smoke-test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pprint

    print("=" * 60)
    print("cleaning.py - demonstration run")
    print("=" * 60)

    # ── load_abbreviations ────────────────────────────────────────────────
    print("\n1. load_abbreviations()")
    abbrevs = load_abbreviations("config/abbreviations.json")
    print(f"   Loaded {len(abbrevs)} abbreviation(s): {abbrevs}")

    # ── normalize_text ────────────────────────────────────────────────────
    print("\n2. normalize_text()")
    sample_abbrevs = {"brg": "bearing", "mtg": "mounting", "assy": "assembly"}
    samples = [
        "  Anti-Friction BRG  Assy!!  ",
        None,
        float("nan"),
        "O-Ring   (Type-A) -- replace",
    ]
    for s in samples:
        print(f"   {str(s):40s} -> {normalize_text(s, sample_abbrevs)!r}")

    # ── parse_date ────────────────────────────────────────────────────────
    print("\n3. parse_date()")
    date_samples = ["2024-01-15", "15/01/2024", "Jan 15 2024", "not-a-date", None]
    for d in date_samples:
        print(f"   {str(d):20s} -> {parse_date(d)}")

    # ── clean_quantity ────────────────────────────────────────────────────
    print("\n4. clean_quantity()")
    qty_samples = ["$1,234.56", "100 units", 42, None, "N/A"]
    for q in qty_samples:
        print(f"   {str(q):15s} -> {clean_quantity(q)}")

    # ── clean_dataframe ───────────────────────────────────────────────────
    print("\n5. clean_dataframe()")
    raw_df = pd.DataFrame(
        {
            "Part Code": ["SP-001", "SP-002", "SP-001"],  # SP-001 is a duplicate
            "Spare Desc": ["Anti-Friction BRG", "O-Ring Type-A", "Anti-Friction BRG"],
            "Qty": ["10 pcs", "$5.00", "10 pcs"],
            "Date In": ["2024-01-15", "not-a-date", "2024-01-15"],
        }
    )
    mapping = {
        "spare_code": "Part Code",
        "spare_description": "Spare Desc",
        "quantity_in_stock": "Qty",
        "date_received": "Date In",
    }
    cleaned = clean_dataframe(raw_df, mapping, "inventory", sample_abbrevs)
    print(cleaned.to_string())

    # ── generate_quality_report ───────────────────────────────────────────
    print("\n6. generate_quality_report()")
    report = generate_quality_report(raw_df, cleaned, mapping)
    pprint.pprint(report)

    # ── expand_abbreviations_in_text ──────────────────────────────────────
    print("\n7. expand_abbreviations_in_text()")
    result = expand_abbreviations_in_text("brg mtg assy", sample_abbrevs)
    print(f"   'brg mtg assy' -> {result!r}")

    print("\nAll demonstrations complete.")
