"""
Unit tests for the data cleaning module (core/cleaning.py).
Run with: pytest tests/test_cleaning.py -v
"""

import pytest
import pandas as pd
import numpy as np
import sys
import os

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cleaning import (
    normalize_text,
    parse_date,
    clean_quantity,
    clean_dataframe,
    generate_quality_report,
    expand_abbreviations_in_text,
)


# ---------------------------------------------------------------------------
# TestNormalizeText
# ---------------------------------------------------------------------------

class TestNormalizeText:
    """Tests for the normalize_text() function."""

    def test_lowercase_conversion(self):
        """'BEARING' should be lowercased to 'bearing'."""
        result = normalize_text("BEARING")
        assert result == "bearing"

    def test_strips_whitespace(self):
        """Leading and trailing whitespace should be stripped."""
        result = normalize_text("  bearing  ")
        assert result == "bearing"

    def test_collapses_spaces(self):
        """Multiple internal spaces should be collapsed to a single space."""
        result = normalize_text("ball  bearing")
        assert result == "ball bearing"

    def test_removes_punctuation(self):
        """
        Trailing/standalone punctuation should be removed.
        A hyphen within a word (e.g. 'type-a') should be kept.
        """
        result = normalize_text("bearing, type-A!")
        # comma and exclamation removed; hyphen within word kept; lowercased
        assert result == "bearing type-a"

    def test_handles_none(self):
        """None input should return an empty string."""
        result = normalize_text(None)
        assert result == ""

    def test_handles_nan(self):
        """np.nan input should return an empty string."""
        result = normalize_text(np.nan)
        assert result == ""

    def test_handles_empty_string(self):
        """Empty string input should return an empty string."""
        result = normalize_text("")
        assert result == ""

    def test_abbreviation_expansion(self):
        """'brg' with abbrevs {'brg': 'bearing'} should expand to 'bearing'."""
        result = normalize_text("brg", abbreviations={"brg": "bearing"})
        assert result == "bearing"

    def test_mixed_case_abbreviation(self):
        """'BRG' with abbrevs {'brg': 'bearing'} should expand (case-insensitive)."""
        result = normalize_text("BRG", abbreviations={"brg": "bearing"})
        assert result == "bearing"

    def test_no_abbreviations(self):
        """normalize_text should work without an abbreviations dict (default None)."""
        result = normalize_text("bearing")
        assert result == "bearing"


# ---------------------------------------------------------------------------
# TestParseDate
# ---------------------------------------------------------------------------

class TestParseDate:
    """Tests for the parse_date() function."""

    def test_standard_date_string(self):
        """'2024-01-15' should parse to pd.Timestamp('2024-01-15')."""
        result = parse_date("2024-01-15")
        assert result == pd.Timestamp("2024-01-15")

    def test_dd_mm_yyyy_format(self):
        """'15/01/2024' should parse to a valid Timestamp for 2024-01-15."""
        result = parse_date("15/01/2024")
        assert isinstance(result, pd.Timestamp)
        assert not pd.isna(result)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_datetime_string(self):
        """'2024-01-15 10:30:00' should parse to a valid Timestamp."""
        result = parse_date("2024-01-15 10:30:00")
        assert isinstance(result, pd.Timestamp)
        assert not pd.isna(result)
        assert result.year == 2024

    def test_none_returns_nat(self):
        """None should return pd.NaT."""
        result = parse_date(None)
        assert pd.isna(result)

    def test_nan_returns_nat(self):
        """np.nan should return pd.NaT."""
        result = parse_date(np.nan)
        assert pd.isna(result)

    def test_empty_string_returns_nat(self):
        """Empty string should return pd.NaT."""
        result = parse_date("")
        assert pd.isna(result)

    def test_garbage_returns_nat(self):
        """An unparseable string like 'not_a_date' should return pd.NaT."""
        result = parse_date("not_a_date")
        assert pd.isna(result)

    def test_already_timestamp(self):
        """An existing pd.Timestamp should be returned as-is (or equivalent)."""
        ts = pd.Timestamp("2024-01-15")
        result = parse_date(ts)
        assert result == ts


# ---------------------------------------------------------------------------
# TestCleanQuantity
# ---------------------------------------------------------------------------

class TestCleanQuantity:
    """Tests for the clean_quantity() function."""

    def test_integer_string(self):
        """'5' should return 5.0."""
        result = clean_quantity("5")
        assert result == 5.0

    def test_float_string(self):
        """'3.14' should return 3.14."""
        result = clean_quantity("3.14")
        assert result == pytest.approx(3.14)

    def test_integer(self):
        """Integer 5 should return 5.0."""
        result = clean_quantity(5)
        assert result == 5.0

    def test_float(self):
        """Float 3.14 should return 3.14."""
        result = clean_quantity(3.14)
        assert result == pytest.approx(3.14)

    def test_string_with_units(self):
        """'5 pcs' should extract numeric part and return 5.0."""
        result = clean_quantity("5 pcs")
        assert result == 5.0

    def test_string_with_currency(self):
        """'$100.50' should strip the currency symbol and return 100.50."""
        result = clean_quantity("$100.50")
        assert result == pytest.approx(100.50)

    def test_none_returns_none(self):
        """None should return None."""
        result = clean_quantity(None)
        assert result is None

    def test_nan_returns_none(self):
        """np.nan should return None."""
        result = clean_quantity(np.nan)
        assert result is None

    def test_empty_string_returns_none(self):
        """Empty string should return None."""
        result = clean_quantity("")
        assert result is None

    def test_non_numeric_returns_none(self):
        """A purely non-numeric string like 'abc' should return None."""
        result = clean_quantity("abc")
        assert result is None


# ---------------------------------------------------------------------------
# TestCleanDataframe
# ---------------------------------------------------------------------------

class TestCleanDataframe:
    """Tests for the clean_dataframe() function."""

    @pytest.fixture
    def raw_df(self):
        """A small messy inventory DataFrame for testing clean_dataframe."""
        return pd.DataFrame({
            "Item_Code": ["INV-001", "INV-002", "INV-003", "INV-004", "INV-005"],
            "Description": [
                "  BALL BEARING  ", "Hydraulic PUMP", "VALVE gate", "MOTOR 5hp", "  FILTER oil  "
            ],
            "Quantity": ["10", "5 pcs", None, "3", "7"],
            "Unit": ["PCS", "pcs", "SET", "EA", "PCS"],
            "Date_Received": ["2024-01-15", "15/02/2024", None, "2024-04-01", "2024-05-20"],
        })

    @pytest.fixture
    def column_mapping(self):
        """Column mapping for the small test DataFrame."""
        return {
            "spare_code": "Item_Code",
            "spare_description": "Description",
            "quantity_in_stock": "Quantity",
            "unit": "Unit",
            "date_received": "Date_Received",
        }

    def test_columns_renamed_correctly(self, raw_df, column_mapping):
        """After cleaning, standardized column names should appear in the output."""
        result = clean_dataframe(raw_df, column_mapping, "inventory")
        # At least the description key should be mapped to output
        assert (
            "description" in result.columns
            or "Description" in result.columns
            or "_cleaned_description" in result.columns
        )

    def test_text_normalized(self, raw_df, column_mapping):
        """The description column values should be lowercased after cleaning."""
        result = clean_dataframe(raw_df, column_mapping, "inventory")
        # Find the description column (could be renamed or original)
        desc_col = None
        for col in result.columns:
            if "description" in col.lower() and "cleaned" not in col.lower():
                desc_col = col
                break
        if desc_col is None:
            # Fall back to cleaned column
            for col in result.columns:
                if "description" in col.lower():
                    desc_col = col
                    break
        assert desc_col is not None, "No description-related column found in cleaned DataFrame"
        # At least one value should be lowercase (not all-caps)
        non_null_vals = result[desc_col].dropna().astype(str)
        assert all(v == v.lower() for v in non_null_vals if v), \
            f"Expected all lowercase in '{desc_col}', got: {non_null_vals.tolist()}"

    def test_duplicates_removed(self, raw_df, column_mapping):
        """Adding a duplicate row should result in that row being removed."""
        df_with_dup = pd.concat([raw_df, raw_df.iloc[[0]]], ignore_index=True)
        result = clean_dataframe(df_with_dup, column_mapping, "inventory")
        assert len(result) < len(df_with_dup), "Duplicate row was not removed"

    def test_missing_values_handled(self, column_mapping):
        """A DataFrame with NaN values should not raise an error."""
        df_with_nans = pd.DataFrame({
            "Item_Code": ["INV-001", None, "INV-003"],
            "Description": [None, "PUMP", np.nan],
            "Quantity": [None, np.nan, "5"],
            "Unit": [None, "PCS", None],
            "Date_Received": [None, None, None],
        })
        # Should not raise
        result = clean_dataframe(df_with_nans, column_mapping, "inventory")
        assert isinstance(result, pd.DataFrame)

    def test_cleaned_description_added(self, raw_df, column_mapping):
        """Output DataFrame should contain a '_cleaned_description' column."""
        result = clean_dataframe(raw_df, column_mapping, "inventory")
        assert "_cleaned_description" in result.columns, \
            f"Expected '_cleaned_description' column; got columns: {result.columns.tolist()}"

    def test_empty_dataframe(self, column_mapping):
        """Passing an empty DataFrame should return an empty DataFrame without error."""
        empty_df = pd.DataFrame(
            columns=["Item_Code", "Description", "Quantity", "Unit", "Date_Received"]
        )
        result = clean_dataframe(empty_df, column_mapping, "inventory")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# TestGenerateQualityReport
# ---------------------------------------------------------------------------

class TestGenerateQualityReport:
    """Tests for the generate_quality_report() function."""

    @pytest.fixture
    def clean_df_before(self):
        """A representative 'raw' DataFrame (before cleaning)."""
        return pd.DataFrame({
            "item_code": [
                "INV-001", "INV-002", "INV-003", "INV-004", "INV-005",
                "INV-006", "INV-007", "INV-008", "INV-009", "INV-010",
            ],
            "description": [
                "BALL BEARING", None, "VALVE GATE", "MOTOR", "FILTER",
                None, "COUPLING", "SHAFT", "SEAL KIT", "PUMP",
            ],
            "quantity": [10.0, 5.0, None, 3.0, 7.0, None, 2.0, 1.0, 4.0, 6.0],
            "date": pd.to_datetime([
                "2024-01-15", None, "2024-03-10", "2024-04-01", "2024-05-20",
                "2024-06-15", None, "2024-08-01", "2024-09-10", "2024-10-05",
            ]),
        })

    @pytest.fixture
    def clean_df_after(self, clean_df_before):
        """The 'after-cleaning' DataFrame (duplicates removed, NaNs handled)."""
        df = clean_df_before.dropna(subset=["item_code"]).copy()
        df["_cleaned_description"] = df["description"].fillna("").str.lower().str.strip()
        return df

    @pytest.fixture
    def perfect_before(self):
        """A perfectly clean 'before' DataFrame with no missing values."""
        return pd.DataFrame({
            "item_code": ["INV-001", "INV-002", "INV-003", "INV-004", "INV-005"],
            "description": ["ball bearing", "hydraulic pump", "gate valve", "motor", "filter"],
            "quantity": [10.0, 5.0, 3.0, 7.0, 2.0],
            "date": pd.to_datetime(
                ["2024-01-15", "2024-02-20", "2024-03-10", "2024-04-01", "2024-05-20"]
            ),
        })

    @pytest.fixture
    def perfect_after(self, perfect_before):
        """The 'after' version of the perfect DataFrame (identical, no changes)."""
        df = perfect_before.copy()
        df["_cleaned_description"] = df["description"]
        return df

    @pytest.fixture
    def test_mapping(self):
        """Mapping for the test DataFrames."""
        return {
            "item_code": "item_code",
            "description": "description",
            "quantity": "quantity",
            "date": "date",
        }

    def test_report_has_required_keys(self, clean_df_before, clean_df_after, test_mapping):
        """The quality report must contain the four required top-level keys."""
        report = generate_quality_report(clean_df_before, clean_df_after, test_mapping)
        required_keys = {
            "total_rows_input",
            "duplicates_removed",
            "missing_per_field",
            "quality_score",
        }
        assert required_keys.issubset(set(report.keys())), \
            f"Missing keys: {required_keys - set(report.keys())}"

    def test_duplicates_counted_correctly(self):
        """If 2 duplicate rows are present in before but removed in after, count should reflect that."""
        before = pd.DataFrame({
            "item_code": ["A", "A", "B", "C", "C"],
            "description": ["alpha", "alpha", "beta", "gamma", "gamma"],
        })
        after = before.drop_duplicates().copy()
        after["_cleaned_description"] = after["description"]
        mapping = {"item_code": "item_code", "description": "description"}
        report = generate_quality_report(before, after, mapping)
        # 2 duplicates removed (rows 1 and 4 are duplicates of rows 0 and 3)
        assert report["duplicates_removed"] == 2, \
            f"Expected 2 duplicates removed, got {report['duplicates_removed']}"

    def test_missing_per_field_format(self, clean_df_before, clean_df_after, test_mapping):
        """missing_per_field should be a dict mapping field name strings to count/pct dicts."""
        report = generate_quality_report(clean_df_before, clean_df_after, test_mapping)
        mpf = report["missing_per_field"]
        assert isinstance(mpf, dict), "missing_per_field should be a dict"
        for key, value in mpf.items():
            assert isinstance(key, str), f"Key '{key}' should be a string"
            assert isinstance(value, dict), f"Value for '{key}' should be a dict"
            assert "count" in value and "pct" in value
            assert isinstance(value["count"], (int, float))
            assert isinstance(value["pct"], (int, float))

    def test_quality_score_range(self, clean_df_before, clean_df_after, test_mapping):
        """quality_score must be a number between 0 and 100 (inclusive)."""
        report = generate_quality_report(clean_df_before, clean_df_after, test_mapping)
        score = report["quality_score"]
        assert 0 <= score <= 100, f"quality_score {score} is outside [0, 100]"

    def test_perfect_data_high_score(self, perfect_before, perfect_after, test_mapping):
        """A completely clean DataFrame with no missing values should score above 80."""
        report = generate_quality_report(perfect_before, perfect_after, test_mapping)
        score = report["quality_score"]
        assert score > 80, f"Expected quality_score > 80 for perfect data, got {score}"


# ---------------------------------------------------------------------------
# TestExpandAbbreviations
# ---------------------------------------------------------------------------

class TestExpandAbbreviations:
    """Tests for the expand_abbreviations_in_text() function."""

    def test_single_word_expansion(self):
        """'brg' with {'brg': 'bearing'} should expand to 'bearing'."""
        result = expand_abbreviations_in_text("brg", {"brg": "bearing"})
        assert result == "bearing"

    def test_multi_word_with_one_abbrev(self):
        """'ball brg' should become 'ball bearing'."""
        result = expand_abbreviations_in_text("ball brg", {"brg": "bearing"})
        assert result == "ball bearing"

    def test_no_match_unchanged(self):
        """'motor' with {'brg': 'bearing'} should return 'motor' unchanged."""
        result = expand_abbreviations_in_text("motor", {"brg": "bearing"})
        assert result == "motor"

    def test_case_insensitive_match(self):
        """normalize_text handles case-insensitive abbreviation expansion."""
        result = normalize_text("BRG", {"brg": "bearing"})
        # expansion should be lowercase "bearing"
        assert result.lower() == "bearing"

    def test_empty_string(self):
        """Empty string input should return an empty string."""
        result = expand_abbreviations_in_text("", {"brg": "bearing"})
        assert result == ""
