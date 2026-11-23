"""
Helper utility to load and clean synthetic demo datasets.
"""

from datetime import date
from pathlib import Path
import pandas as pd
import streamlit as st

from core.cleaning import clean_dataframe, load_abbreviations

try:
    from core.database import get_engine, initialize_database, save_dataframe
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False


INVENTORY_FIELDS = {
    "spare_code": "spare_code",
    "spare_description": "spare_description",
    "equipment_tag": "equipment_tag",
    "quantity_in_stock": "quantity_in_stock",
    "unit_cost": "unit_cost",
    "date_received": "date_received",
    "last_used_date": "last_used_date",
    "expiry_date": "expiry_date",
    "category": "category",
    "department": "department",
    "criticality_flag": "criticality_flag",
}

PTW_FIELDS = {
    "permit_id": "permit_id",
    "spare_description_text": "spare_description_text",
    "quantity_used": "quantity_used",
    "equipment_tag": "equipment_tag",
    "department": "department",
    "permit_start": "permit_start",
    "permit_end": "permit_end",
    "remarks": "remarks",
}


def load_demo_datasets() -> bool:
    """
    Load sample CSV files from data/, clean them with core.cleaning,
    and store results in SQLite and session state.

    Returns
    -------
    bool
        True if demo data was loaded successfully.
    """
    inv_path = Path("data/inventory_sample.csv")
    ptw_path = Path("data/ptw_sample.csv")

    if not inv_path.exists() or not ptw_path.exists():
        st.error("Demo data files not found in data/. Run python core/generate_dummy_data.py first.")
        return False

    try:
        inv_raw = pd.read_csv(inv_path)
        ptw_raw = pd.read_csv(ptw_path)

        abbrevs = load_abbreviations("config/abbreviations.json")
        inv_clean = clean_dataframe(inv_raw, INVENTORY_FIELDS, "inventory", abbrevs)
        ptw_clean = clean_dataframe(ptw_raw, PTW_FIELDS, "ptw", abbrevs)

        # Store in session state
        st.session_state["inventory_raw"] = inv_raw
        st.session_state["inventory_df"] = inv_clean
        st.session_state["inv_row_count"] = len(inv_clean)
        st.session_state["inventory_mapping"] = INVENTORY_FIELDS

        st.session_state["ptw_raw"] = ptw_raw
        st.session_state["ptw_df"] = ptw_clean
        st.session_state["ptw_row_count"] = len(ptw_clean)
        st.session_state["ptw_mapping"] = PTW_FIELDS

        st.session_state["last_upload_date"] = date.today().isoformat()
        st.session_state["data_loaded"] = True

        # Save to database if available
        if _DB_AVAILABLE:
            try:
                engine = get_engine()
                initialize_database(engine)
                save_dataframe(engine, inv_clean, "inventory_cleaned", if_exists="replace")
                save_dataframe(engine, ptw_clean, "ptw_cleaned", if_exists="replace")
            except Exception:
                pass

        return True
    except Exception as exc:
        st.error(f"Failed to load demo data: {exc}")
        return False

