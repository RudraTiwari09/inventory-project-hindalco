"""
Shared pytest fixtures for the Inventory-PTW Reconciliation Dashboard test suite.
These fixtures provide reusable test data across multiple test modules.
"""

import pytest
import pandas as pd
import numpy as np


@pytest.fixture
def sample_inventory_df():
    """
    A 10-row DataFrame with realistic but messy inventory data.
    Includes mixed casing, extra whitespace, missing values, and duplicate rows.
    """
    data = {
        "Item_Code": [
            "INV-001", "INV-002", "INV-003", "INV-004", "INV-005",
            "INV-006", "INV-007", "INV-008", "INV-009", "INV-002",   # last is duplicate of row 2
        ],
        "Description": [
            "  BALL BEARING  ", "Hydraulic PUMP", "VALVE, gate type-A", "BRG cylindrical",
            "MOTOR electric 5hp", "  Seal kit  ", "FILTER oil", "COUPLING flexible",
            "SHAFT main drive", "Hydraulic PUMP",
        ],
        "Quantity": [
            "10", "5 pcs", "$200.00", "3", "1", None, "7", "2", np.nan, "5 pcs",
        ],
        "Unit": [
            "PCS", "pcs", "SET", "PCS", "EA", "SET", "pcs", "PCS", "EA", "pcs",
        ],
        "Date_Received": [
            "2024-01-15", "15/02/2024", "2024-03-10 08:00:00", "2024-04-01",
            "not_a_date", None, "2024-06-20", "2024-07-05", "", "15/02/2024",
        ],
        "Location": [
            "Warehouse A", "Warehouse B", None, "Warehouse A", "Workshop",
            "Warehouse C", "Warehouse A", np.nan, "Warehouse B", "Warehouse B",
        ],
        "Cost": [
            "1500.00", "2500.50", "350.00", "800.00", "12000.00",
            "150.00", "250.00", "400.00", "5000.00", "2500.50",
        ],
    }
    return pd.DataFrame(data)


@pytest.fixture
def sample_ptw_df():
    """
    A 10-row DataFrame with realistic but messy Permit-to-Work (PTW) data.
    Includes varied descriptions, missing fields, and date formats.
    """
    data = {
        "Permit_No": [
            "PTW-2024-001", "PTW-2024-002", "PTW-2024-003", "PTW-2024-004", "PTW-2024-005",
            "PTW-2024-006", "PTW-2024-007", "PTW-2024-008", "PTW-2024-009", "PTW-2024-010",
        ],
        "Work_Description": [
            "Replace BALL BRG on pump", "Overhaul hydraulic pump unit", "Install gate VALVE",
            "Lubrication of MOTOR bearings", "  FILTER replacement oil system  ",
            "COUPLING alignment check", "SEAL kit replacement", "Shaft inspection MAIN",
            "Bearing housing repair BRG", "PUMP hydraulic overhaul",
        ],
        "Requested_Item": [
            "BALL BEARING", "HYD PUMP", "GATE VALVE", "BRG",
            "OIL FILTER", "FLEX COUPLING", "SEAL KIT", "DRIVE SHAFT",
            "BEARING", "HYDRAULIC PUMP",
        ],
        "Qty_Required": [
            "2", "1", "1 set", "4 pcs", None, "1", "2 sets", "1",
            np.nan, "1",
        ],
        "Issue_Date": [
            "2024-01-20", "2024-02-18", "2024-03-12", "2024-04-05", "2024-05-15",
            "2024-06-25", "2024-07-10", "2024-08-02", "invalid", None,
        ],
        "Department": [
            "Maintenance", "Operations", "Maintenance", "Electrical",
            None, "Mechanical", "Maintenance", "Operations", "Mechanical", np.nan,
        ],
        "Priority": [
            "HIGH", "Medium", "low", "HIGH", "Medium",
            "LOW", "High", "medium", "HIGH", "MEDIUM",
        ],
    }
    return pd.DataFrame(data)


@pytest.fixture
def inventory_mapping():
    """
    Mapping of standardized field names to actual column names for the sample inventory DataFrame.
    """
    return {
        "item_code": "Item_Code",
        "description": "Description",
        "quantity": "Quantity",
        "unit": "Unit",
        "date": "Date_Received",
        "location": "Location",
        "cost": "Cost",
    }


@pytest.fixture
def ptw_mapping():
    """
    Mapping of standardized field names to actual column names for the sample PTW DataFrame.
    """
    return {
        "permit_no": "Permit_No",
        "description": "Work_Description",
        "requested_item": "Requested_Item",
        "quantity": "Qty_Required",
        "date": "Issue_Date",
        "department": "Department",
        "priority": "Priority",
    }


@pytest.fixture
def sample_abbreviations():
    """
    Dictionary of common inventory/engineering abbreviations and their expansions.
    """
    return {
        "brg": "bearing",
        "hyd": "hydraulic",
        "pmp": "pump",
        "mtr": "motor",
        "vlv": "valve",
        "flt": "filter",
        "cpl": "coupling",
        "sft": "shaft",
        "skt": "socket",
        "assy": "assembly",
        "ea": "each",
        "pcs": "pieces",
        "qty": "quantity",
        "inv": "inventory",
        "ptw": "permit to work",
    }
