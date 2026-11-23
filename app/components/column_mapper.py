"""
column_mapper.py — Interactive column mapping component for the
Inventory-PTW Reconciliation Dashboard.

Renders a UI that lets plant engineers map uploaded file columns to the
standard field schema required by the reconciliation engine.  Auto-suggests
the best match per field using rapidfuzz string similarity.

Public API
----------
render_column_mapper(df, dataset_name, required_fields, db_engine)
"""

import json
import os
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

# ── Optional imports (graceful degradation) ──────────────────────────────────
try:
    from rapidfuzz import fuzz as _fuzz

    def _similarity(a: str, b: str) -> float:
        """Return token-sort ratio similarity [0–100] between two strings."""
        return _fuzz.token_sort_ratio(a.lower(), b.lower())

except ImportError:
    # Fallback: simple character-overlap heuristic if rapidfuzz is not installed
    def _similarity(a: str, b: str) -> float:  # type: ignore[misc]
        """Fallback similarity using common-character overlap."""
        a_set = set(a.lower().replace("_", " ").split())
        b_set = set(b.lower().replace("_", " ").split())
        if not a_set or not b_set:
            return 0.0
        return 100.0 * len(a_set & b_set) / max(len(a_set), len(b_set))


try:
    from core.database import save_column_mapping as _db_save_mapping
    from core.database import load_column_mapping as _db_load_mapping
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False


# ── Constants ─────────────────────────────────────────────────────────────────
_CONFIG_DIR = Path("config")
_NO_SELECTION = "— (not mapped) —"

_MAPPER_CSS = """
<style>
.mapper-header {
    color: #1E3A5F;
    font-size: 1.1rem;
    font-weight: 700;
    margin: 0.5rem 0 0.1rem;
}
.required-star { color: #C0392B; font-weight: 700; }
.optional-label { color: #888; font-size: 0.8rem; }
.preview-caption {
    font-size: 0.8rem;
    color: #888;
    margin-bottom: 0.25rem;
}
.mapper-divider {
    border: none;
    border-top: 1px solid #D0DBE8;
    margin: 0.75rem 0;
}
</style>
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _best_match(field_name: str, columns: list[str]) -> str:
    """
    Return the column name with the highest string-similarity score against
    ``field_name``.  Falls back to ``_NO_SELECTION`` when no column scores
    above 30 %.
    """
    scores = {col: _similarity(field_name, col) for col in columns}
    best_col = max(scores, key=scores.get)
    return best_col if scores[best_col] >= 30 else _NO_SELECTION


def _config_path(dataset_name: str) -> Path:
    """Return the config JSON path for a given dataset."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return _CONFIG_DIR / f"mapping_{dataset_name}.json"


def _save_to_json(dataset_name: str, mapping: dict) -> None:
    """Persist the mapping dict to a JSON config file."""
    path = _config_path(dataset_name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "dataset": dataset_name,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "mapping": mapping,
            },
            fh,
            indent=2,
        )


def _load_from_json(dataset_name: str) -> dict | None:
    """Load a previously saved mapping from the config JSON file, or None."""
    path = _config_path(dataset_name)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("mapping", {})
    except (json.JSONDecodeError, OSError):
        return None


# ── Main public function ──────────────────────────────────────────────────────

def render_column_mapper(
    df: pd.DataFrame,
    dataset_name: str,
    required_fields: list[dict],
    db_engine,
) -> dict:
    """
    Render an interactive column-mapping UI and return the confirmed mapping.

    Parameters
    ----------
    df : pd.DataFrame
        The uploaded DataFrame whose columns are to be mapped.
    dataset_name : str
        "inventory" or "ptw" — used for session-state keys and file naming.
    required_fields : list[dict]
        Each dict must have:
          - "field"       : str  — standard field name (e.g. "spare_code")
          - "description" : str  — human-readable explanation
          - "required"    : bool — whether this field is mandatory
    db_engine :
        SQLAlchemy engine for persisting mappings; may be None if DB is
        unavailable (the component will still save to JSON).

    Returns
    -------
    dict
        {standard_field: uploaded_column_or_empty_string, ...}
        Returns an empty dict if the user has not yet validated the mapping.
    """
    st.markdown(_MAPPER_CSS, unsafe_allow_html=True)

    # ── 1. Data preview ───────────────────────────────────────────────────────
    st.markdown(
        f"<p class='preview-caption'>Preview — first 20 rows of uploaded {dataset_name} data</p>",
        unsafe_allow_html=True,
    )
    st.dataframe(df.head(20), use_container_width=True)

    st.markdown("<hr class='mapper-divider'>", unsafe_allow_html=True)
    st.markdown(
        "<p class='mapper-header'>🗂️ Map Your Columns to Standard Fields</p>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Best matches are pre-selected based on column-name similarity. "
        "Adjust any that look wrong, then validate and save."
    )

    # ── 2. Build the selectbox options list ───────────────────────────────────
    all_columns: list[str] = df.columns.tolist()
    options = [_NO_SELECTION] + all_columns

    # ── 3. Load existing mapping from session state (pre-populate) ────────────
    ss_key = f"col_map_{dataset_name}"
    if ss_key not in st.session_state:
        saved = _load_from_json(dataset_name)
        if saved:
            st.session_state[ss_key] = saved
        else:
            # Auto-suggest defaults
            st.session_state[ss_key] = {
                f["field"]: _best_match(f["field"], all_columns)
                for f in required_fields
            }

    current_mapping: dict = st.session_state[ss_key]

    # ── 4. Render one selectbox per field ─────────────────────────────────────
    new_mapping: dict = {}

    for field_def in required_fields:
        field = field_def["field"]
        description = field_def["description"]
        is_required = field_def.get("required", False)

        # Build a label with required/optional indicator
        req_html = (
            "<span class='required-star'> *</span>"
            if is_required
            else " <span class='optional-label'>(optional)</span>"
        )
        label_md = f"**`{field}`**{req_html} — {description}"

        st.markdown(label_md, unsafe_allow_html=True)

        # Determine currently selected value (may come from saved mapping)
        saved_val = current_mapping.get(field, _NO_SELECTION)
        if saved_val not in options:
            saved_val = _NO_SELECTION
        default_idx = options.index(saved_val)

        chosen = st.selectbox(
            label=f"Map field: {field}",
            options=options,
            index=default_idx,
            key=f"mapper_{dataset_name}_{field}",
            label_visibility="collapsed",
        )
        new_mapping[field] = chosen if chosen != _NO_SELECTION else ""

    # Keep session state in sync
    st.session_state[ss_key] = new_mapping

    st.markdown("<hr class='mapper-divider'>", unsafe_allow_html=True)

    # ── 5. Action buttons ─────────────────────────────────────────────────────
    btn_validate, btn_save, btn_load = st.columns([1, 1, 1])

    validation_errors: list[str] = []
    validation_ok: bool = False

    with btn_validate:
        if st.button("🔍 Validate Mapping", key=f"validate_{dataset_name}"):
            # a) Check all required fields are mapped
            for field_def in required_fields:
                if field_def.get("required") and not new_mapping.get(field_def["field"]):
                    validation_errors.append(
                        f"Required field **`{field_def['field']}`** is not mapped."
                    )

            # b) Check no two required fields share the same column
            mapped_cols = [
                v for f, v in new_mapping.items()
                if v
                and any(
                    fd["field"] == f and fd.get("required")
                    for fd in required_fields
                )
            ]
            if len(mapped_cols) != len(set(mapped_cols)):
                seen: set = set()
                for col in mapped_cols:
                    if col in seen:
                        validation_errors.append(
                            f"Column **`{col}`** is assigned to more than one "
                            "required field."
                        )
                    seen.add(col)

            if validation_errors:
                for err in validation_errors:
                    st.error(err)
            else:
                validation_ok = True
                st.session_state[f"validated_{dataset_name}"] = True
                st.success(
                    "✅ Mapping is valid — all required fields are mapped and "
                    "each column is used at most once."
                )

    with btn_save:
        already_validated = st.session_state.get(f"validated_{dataset_name}", False)
        save_disabled = not already_validated

        if st.button(
            "💾 Save Mapping",
            key=f"save_{dataset_name}",
            disabled=save_disabled,
            help="Validate the mapping first before saving.",
        ):
            try:
                # Save to JSON
                _save_to_json(dataset_name, new_mapping)

                # Save to DB (if available)
                if _DB_AVAILABLE and db_engine is not None:
                    try:
                        _db_save_mapping(db_engine, dataset_name, new_mapping)
                    except Exception as db_err:
                        st.warning(
                            f"⚠️ Mapping saved to file but DB write failed: {db_err}"
                        )

                # Persist in session state under the canonical key
                st.session_state[f"{dataset_name}_mapping"] = new_mapping
                st.toast(
                    f"✅ {dataset_name.capitalize()} column mapping saved!",
                    icon="💾",
                )
                st.success(
                    f"Mapping saved to `config/mapping_{dataset_name}.json`."
                )
            except Exception as exc:
                st.error(f"❌ Failed to save mapping: {exc}")

    with btn_load:
        if st.button("📂 Load Saved Mapping", key=f"load_{dataset_name}"):
            loaded = _load_from_json(dataset_name)
            if loaded is None:
                st.warning(
                    f"No saved mapping found for **{dataset_name}** at "
                    f"`config/mapping_{dataset_name}.json`."
                )
            else:
                st.session_state[ss_key] = loaded
                st.session_state[f"{dataset_name}_mapping"] = loaded
                st.success(
                    f"✅ Loaded saved mapping for **{dataset_name}** "
                    f"({len(loaded)} fields)."
                )
                st.rerun()

    return new_mapping if st.session_state.get(f"validated_{dataset_name}") else {}
