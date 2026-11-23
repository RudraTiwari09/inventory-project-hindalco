"""
Centralized session state management for Recon Dashboard.
Defines all default state keys, accessors, and reset helpers to prevent KeyErrors.
"""

from datetime import date
from typing import Any, Dict, Optional
import streamlit as st

DEFAULTS: Dict[str, Any] = {
    # Authentication & Session
    "authenticated": False,
    "user": None,
    "flow_stage": "login",  # 'login', 'landing', 'upload', 'review', 'main'
    "last_activity": None,
    "selected_period": None,
    "selected_page": "Overview",
    "current_page": "Overview",

    # Upload Partition Modes (None = unselected by default)
    "inventory_mode": None,  # None, 'new', 'old'
    "ptw_mode": None,        # None, 'new', 'old'
    "inv_mode": None,        # alias for backwards compatibility

    # Uploaded Data & Staging
    "inventory_as_of": None,
    "inv_as_of": None,
    "ptw_as_of": None,
    "inventory_mapping": {},
    "inv_mapping": {},
    "ptw_mapping": {},
    "inventory_parsed": None,
    "inv_parsed": None,
    "ptw_parsed": None,
    "inventory_validation": None,
    "inv_validation": None,
    "ptw_validation": None,
    "inventory_file_bytes": None,
    "inv_file_bytes": None,
    "ptw_file_bytes": None,
    "inventory_file_name": None,
    "inv_file_name": None,
    "ptw_file_name": None,

    # Legacy / Active DataFrames
    "inventory_df": None,
    "ptw_df": None,
    "inventory_raw": None,
    "ptw_raw": None,
    "inv_row_count": 0,
    "ptw_row_count": 0,
    "last_upload_date": None,
    "data_loaded": False,

    # Savings Tracker (Step 1)
    "savings_active_tab": "Summary",
    "savings_filter_status": "All",
    "savings_filter_dept": "All",
    "savings_search": "",

    # Reports Generator (Step 1)
    "reports_period": None,
    "report_generation_status": None,
    "report_history": [],
    "active_report_run": None,
}


def _get_alias(key: str) -> Optional[str]:
    """Return alias key if exists (e.g. inventory_mode <-> inv_mode)."""
    if key.startswith("inventory_"):
        return "inv_" + key[len("inventory_"):]
    elif key.startswith("inv_"):
        return "inventory_" + key[len("inv_"):]
    return None


def init_state() -> None:
    """
    Ensure every required key in DEFAULTS is present in st.session_state.
    Must be called at the very top of main.py before any logic or routing.
    """
    for key, default_val in DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default_val


def get(key: str, default: Any = None) -> Any:
    """
    Safe getter for session state.
    Returns session_state[key] if present, else checks alias, else default or DEFAULTS[key].
    """
    if key in st.session_state and st.session_state[key] is not None:
        return st.session_state[key]
    
    # Try alias
    alias = _get_alias(key)
    if alias and alias in st.session_state and st.session_state[alias] is not None:
        return st.session_state[alias]

    if default is not None:
        return default
    return DEFAULTS.get(key)


def set(key: str, value: Any) -> None:
    """
    Safe setter for session state.
    Sets key and its alias (if any).
    """
    st.session_state[key] = value
    alias = _get_alias(key)
    if alias:
        st.session_state[alias] = value


def reset_upload_state() -> None:
    """
    Reset all upload partition selections, parsed files, and staging data
    when entering the Upload screen from Landing or Main app.
    Does NOT wipe authentication or global selection.
    """
    upload_keys = [
        "inventory_mode",
        "inv_mode",
        "ptw_mode",
        "inventory_as_of",
        "inv_as_of",
        "ptw_as_of",
        "inventory_mapping",
        "inv_mapping",
        "ptw_mapping",
        "inventory_parsed",
        "inv_parsed",
        "ptw_parsed",
        "inventory_validation",
        "inv_validation",
        "ptw_validation",
        "inventory_file_bytes",
        "inv_file_bytes",
        "ptw_file_bytes",
        "inventory_file_name",
        "inv_file_name",
        "ptw_file_name",
    ]
    for k in upload_keys:
        st.session_state[k] = DEFAULTS.get(k)


def clear_all_state() -> None:
    """
    Reset all session state to defaults on logout.
    """
    st.session_state.clear()
    init_state()
