"""
core/generate_dummy_data.py
============================
Synthetic data generator for the Inventory-PTW Reconciliation Dashboard.

Generates:
  - data/inventory_sample.csv   (~500 rows)
  - data/ptw_sample.csv         (~300 rows, with messy free-text descriptions)
  - data/ground_truth_anomalies.csv (30 known anomalies for validation)

Run directly:
  python core/generate_dummy_data.py

Seed: 42 (fully reproducible)
"""

import os
import random
import string
from pathlib import Path
from datetime import datetime, timedelta, date, timezone

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Configuration & constants
# ---------------------------------------------------------------------------

SEED = 42
random.seed(SEED)
np.random.seed(SEED)

# Project root is two levels up from this file (core/ -> project root)
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

NUM_INVENTORY = 500
NUM_PTW = 300
NUM_ANOMALIES_EACH = 10  # 10 of Type A, 10 of Type B, 10 of Type C

# Controlled vocabulary for spare parts ─ realistic industrial components
SPARE_TEMPLATES = [
    "Deep groove ball bearing 6205",
    "Angular contact bearing 7308",
    "Cylindrical roller bearing NU2210",
    "Thrust ball bearing 51107",
    "Gate valve 2 inch DN50",
    "Globe valve 1.5 inch SS",
    "Ball valve 3 inch flanged",
    "Butterfly valve 4 inch wafer",
    "Check valve 2 inch swing type",
    "Centrifugal pump impeller 150mm",
    "Pump mechanical seal type 21",
    "Pump coupling flexible jaw",
    "Pump shaft sleeve stainless steel",
    "O-ring seal nitrile 50x3",
    "Spiral wound gasket DN100",
    "Ring joint gasket R-24",
    "Flange gasket PTFE 4 inch",
    "Oil filter element 10 micron",
    "Air filter element compressed",
    "Fuel filter cartridge diesel",
    "Hydraulic filter high pressure",
    "V-belt type A-56",
    "Timing belt HTD 5M 450",
    "Conveyor belt rubber 500mm",
    "Electric motor 7.5 kW 4-pole",
    "Motor start capacitor 25uF",
    "Motor control relay 24VDC",
    "Contactor 3-phase 40A",
    "Pressure gauge 0-10 bar",
    "Temperature transmitter PT100",
    "Level switch float type",
    "Flow meter electromagnetic 2 inch",
    "Safety relief valve 1 inch",
    "Pressure relief valve 15 bar",
    "Lubricating grease EP2 15kg",
    "Hydraulic oil ISO VG 46 20L",
    "Gear oil 220 cSt 20L",
    "Compressor oil synthetic 5L",
    "Packing gland PTFE 12mm",
    "Mechanical packing braided 10mm",
    "Hex bolt M16x60 grade 8.8",
    "Stud bolt M20x150 SS316",
    "Hex nut M16 galvanised",
    "Spring washer M16 SS",
    "Flat washer M20 SS316",
    "Pipe flange raised face DN80",
    "Pipe elbow 90 degree 2 inch SS",
    "Pipe tee equal 1.5 inch SS",
    "Coupling rigid 25mm shaft",
    "Coupling flexible spider insert",
    "Gearbox output shaft seal",
    "Turbine blade inspection cover",
    "Heat exchanger tube bundle gasket",
    "Vessel manhole cover gasket",
    "Control panel door seal",
    "Generator AVR module",
    "Compressor piston ring set",
    "Compressor valve plate",
    "Vibration isolator mounting pad",
    "Lube oil pump gear set",
    "Cooling fan motor 0.75 kW",
]

CATEGORIES = ["Mechanical", "Electrical", "Instrumentation", "Civil", "Consumables"]
DEPARTMENTS = ["Maintenance", "Operations", "Utilities", "Production"]
CRITICALITY = ["High", "Medium", "Low"]

# Common abbreviation substitutions used to make descriptions messy
ABBREVIATION_MAP = {
    "bearing": ["BRG", "Brg.", "brng", "Bearing", "BEARING"],
    "valve": ["VLV", "vlv", "Vlv.", "VALVE"],
    "pump": ["PMP", "pmp", "Pmp.", "PUMP"],
    "gasket": ["GSKT", "gskt", "Gskt"],
    "filter": ["FLT", "flt", "Flt."],
    "seal": ["SEAL", "Seal", "sl"],
    "coupling": ["CPL", "cpl", "Cpl."],
    "motor": ["MOT", "mot", "Mtr"],
    "assembly": ["ASSY", "assy", "Assy"],
    "electrical": ["ELEC", "elec", "Elec."],
    "mechanical": ["MECH", "mech", "Mech."],
    "compressor": ["COMP", "comp", "Cmpr"],
    "turbine": ["TURB", "turb", "Trb"],
    "generator": ["GEN", "gen", "Gnr"],
    "flange": ["FLNG", "flng", "Flng."],
    "grease": ["GRSE", "grse", "Grse."],
    "lubricant": ["LUBE", "lube", "Lub"],
    "exchanger": ["EXCH", "exch", "Exchr"],
}

# Realistic plant remarks to replace faker sentence generation
REMARKS_TEMPLATES = [
    "Replaced as part of scheduled maintenance.",
    "Part failed during operation - emergency replacement.",
    "Used during annual overhaul of unit.",
    "Installed per work order WO-2024-0045.",
    "Replaced due to wear and tear.",
    "Part used for preventive maintenance job.",
    "Installed as per OEM recommendation.",
    "Replaced after vibration analysis findings.",
    "Emergency breakdown repair.",
    "Used in shutdown maintenance window.",
    "Part swapped during routine inspection.",
    "Replaced per engineer recommendation.",
    "Used in corrective maintenance.",
    "Part consumed during testing and commissioning.",
    "Installed after root cause analysis recommendation.",
]

# Noise prefixes/suffixes added to PTW descriptions to simulate real data messiness
NOISE_PREFIXES = [
    "replaced old ", "installed new ", "req. ", "need ", "request for ",
    "urgently needed ", "as per WO ", "ref: PM-", "", "", "", "",
]
NOISE_SUFFIXES = [
    " - urgent", " as per schedule", " qty req.", " for PM", "",
    " see remarks", " check stock", "", "", "",
]

# Completely unrelated descriptions used for Type-B anomalies
BOGUS_DESCRIPTIONS = [
    "Laptop power adapter 65W",
    "Safety harness full body",
    "Hard hat class E yellow",
    "Fire extinguisher ABC 9kg",
    "Welding rod E6013 3.2mm",
    "Paint brush set industrial",
    "Cable tie 300mm black",
    "Adhesive epoxy two part",
    "Sandpaper grit 120 sheet",
    "Measuring tape 5m steel",
]


# ---------------------------------------------------------------------------
# Helper: random date utilities
# ---------------------------------------------------------------------------

def random_date_in_last_n_years(n: int) -> date:
    """Return a random date within the past *n* years from today."""
    today = date.today()
    start = today - timedelta(days=365 * n)
    delta_days = (today - start).days
    return start + timedelta(days=random.randint(0, delta_days))


def random_datetime_in_last_n_years(n: int) -> datetime:
    """Return a random datetime within the past *n* years from now."""
    now = datetime.now()
    start = now - timedelta(days=365 * n)
    delta_seconds = int((now - start).total_seconds())
    return start + timedelta(seconds=random.randint(0, delta_seconds))


def random_future_date(max_years: int = 3) -> date:
    """Return a random future date within *max_years* years from today."""
    today = date.today()
    return today + timedelta(days=random.randint(1, 365 * max_years))


# ---------------------------------------------------------------------------
# Core: make_messy_description
# ---------------------------------------------------------------------------

def make_messy_description(text: str, seed: int = None) -> str:
    """
    Apply random, realistic transformations to a spare-part description to
    simulate the messy free-text found in PTW (Permit-to-Work) systems.

    Transformations applied (randomly combined):
      1. Random casing  : UPPER / lower / Title / mIxEd
      2. Abbreviations  : replace full words with known industrial abbreviations
      3. Typos          : swap adjacent letters, double a letter, drop a letter
      4. Extra spaces   : insert 1-3 extra spaces between random words
      5. Noise words    : prepend / append common PTW noise phrases

    Parameters
    ----------
    text : str
        The clean spare-part description to corrupt.
    seed : int, optional
        Local random seed for this transformation (keeps outer seed intact).

    Returns
    -------
    str
        A messily transformed version of *text*.
    """
    if seed is not None:
        rng = random.Random(seed)
    else:
        rng = random

    result = text

    # --- 1. Apply abbreviation substitutions (probability 0.6) ---
    if rng.random() < 0.6:
        for full_word, abbr_list in ABBREVIATION_MAP.items():
            if full_word in result.lower():
                abbr = rng.choice(abbr_list)
                # Case-insensitive replace of the first occurrence
                lower_result = result.lower()
                idx = lower_result.find(full_word)
                if idx != -1:
                    result = result[:idx] + abbr + result[idx + len(full_word):]

    # --- 2. Apply random casing ---
    casing_choice = rng.choice(["upper", "lower", "title", "mixed", "original"])
    if casing_choice == "upper":
        result = result.upper()
    elif casing_choice == "lower":
        result = result.lower()
    elif casing_choice == "title":
        result = result.title()
    elif casing_choice == "mixed":
        # Randomly toggle case of each character
        result = "".join(
            c.upper() if rng.random() > 0.5 else c.lower() for c in result
        )
    # else: leave original casing

    # --- 3. Inject a typo (probability 0.4) ---
    if rng.random() < 0.4 and len(result) > 4:
        words = result.split()
        if words:
            target_idx = rng.randrange(len(words))
            word = words[target_idx]
            if len(word) > 2:
                typo_type = rng.choice(["swap", "double", "drop"])
                char_idx = rng.randint(0, len(word) - 2)
                if typo_type == "swap":
                    # Swap two adjacent characters
                    w = list(word)
                    w[char_idx], w[char_idx + 1] = w[char_idx + 1], w[char_idx]
                    word = "".join(w)
                elif typo_type == "double":
                    # Double a character
                    word = word[:char_idx] + word[char_idx] + word[char_idx:]
                elif typo_type == "drop":
                    # Drop a character
                    word = word[:char_idx] + word[char_idx + 1:]
                words[target_idx] = word
            result = " ".join(words)

    # --- 4. Add extra spaces (probability 0.3) ---
    if rng.random() < 0.3:
        words = result.split()
        # Insert 1-3 extra spaces at random word boundaries
        num_extra = rng.randint(1, 3)
        for _ in range(num_extra):
            if len(words) > 1:
                insert_at = rng.randint(0, len(words) - 1)
                words[insert_at] = words[insert_at] + " "
        result = " ".join(words)

    # --- 5. Add noise prefix / suffix ---
    prefix = rng.choice(NOISE_PREFIXES)
    suffix = rng.choice(NOISE_SUFFIXES)
    result = prefix + result + suffix

    return result.strip()


# ---------------------------------------------------------------------------
# Generator: Inventory data
# ---------------------------------------------------------------------------

def generate_inventory(n: int = NUM_INVENTORY) -> pd.DataFrame:
    """
    Generate a synthetic inventory DataFrame with *n* rows.

    Each row represents one spare-part stock record with realistic attributes
    including codes, descriptions, quantities, costs, dates, and flags.

    Parameters
    ----------
    n : int
        Number of inventory rows to generate.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: spare_code, spare_description, category,
        equipment_tag, department, quantity_in_stock, unit_cost,
        date_received, last_used_date, expiry_date, criticality_flag.
    """
    rows = []
    today = date.today()

    # Pre-generate a pool of equipment tags (shared with PTW)
    equipment_tags = [f"EQ-{str(i).zfill(3)}" for i in range(1, 101)]

    for i in range(n):
        # Unique spare code: INV-XXXX
        spare_code = f"INV-{random.randint(1000, 9999)}"

        # Pick a spare description from templates, optionally tweak with a size/spec
        base_desc = random.choice(SPARE_TEMPLATES)

        # Category and department
        category = random.choice(CATEGORIES)
        department = random.choice(DEPARTMENTS)

        # Equipment tag
        equip_tag = random.choice(equipment_tags)

        # Quantity in stock (0-200, weighted toward lower values)
        qty = int(np.random.exponential(scale=40))
        qty = max(0, min(qty, 200))

        # Unit cost (10-5000, log-normal distribution for realism)
        unit_cost = round(float(np.random.lognormal(mean=5.5, sigma=1.2)), 2)
        unit_cost = max(10.0, min(unit_cost, 5000.0))

        # Date received: within last 3 years
        date_received = random_date_in_last_n_years(3)

        # Last used date: sometimes None, otherwise after date_received
        if random.random() < 0.25:
            last_used_date = None
        else:
            days_since_received = (today - date_received).days
            if days_since_received > 0:
                last_used_date = date_received + timedelta(
                    days=random.randint(0, days_since_received)
                )
            else:
                last_used_date = None

        # Expiry date: sometimes None (non-perishables), otherwise future date
        expiry_date = None if random.random() < 0.4 else random_future_date(3)

        # Criticality flag (weighted: more High/Medium than Low)
        criticality_flag = random.choices(
            CRITICALITY, weights=[0.3, 0.5, 0.2], k=1
        )[0]

        rows.append(
            {
                "spare_code": spare_code,
                "spare_description": base_desc,
                "category": category,
                "equipment_tag": equip_tag,
                "department": department,
                "quantity_in_stock": qty,
                "unit_cost": unit_cost,
                "date_received": date_received,
                "last_used_date": last_used_date,
                "expiry_date": expiry_date,
                "criticality_flag": criticality_flag,
            }
        )

    df = pd.DataFrame(rows)
    # Reset index so spare_code is kept as a column (not index)
    df = df.reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Generator: PTW data
# ---------------------------------------------------------------------------

def generate_ptw(inventory_df: pd.DataFrame, n: int = NUM_PTW) -> pd.DataFrame:
    """
    Generate a synthetic PTW (Permit-to-Work) DataFrame with *n* rows.

    PTW spare_description_text is derived from inventory descriptions but
    deliberately made messy using :func:`make_messy_description`.

    Parameters
    ----------
    inventory_df : pd.DataFrame
        The inventory DataFrame (used to draw equipment tags and descriptions).
    n : int
        Number of PTW rows to generate.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: permit_id, equipment_tag, department,
        permit_start, permit_end, spare_description_text, quantity_used,
        matched_spare_code, remarks.
    """
    rows = []
    equipment_tags = inventory_df["equipment_tag"].unique().tolist()
    # Also allow some unknown tags (not in inventory) to simulate real gaps
    unknown_tags = [f"EQ-{str(i).zfill(3)}" for i in range(101, 121)]
    all_tags = equipment_tags + unknown_tags

    today = datetime.now()

    for i in range(n):
        # Permit ID: PTW-YYYY-XXXX
        year = random.choice([today.year - 1, today.year])
        permit_id = f"PTW-{year}-{random.randint(1000, 9999)}"

        # Equipment tag: usually from inventory, occasionally unknown
        equip_tag = (
            random.choice(equipment_tags)
            if random.random() < 0.85
            else random.choice(unknown_tags)
        )

        # Department: usually matches inventory, sometimes different
        department = random.choice(DEPARTMENTS)

        # Permit start datetime (within last 2 years)
        permit_start = random_datetime_in_last_n_years(2)
        # Permit end: 1-72 hours after start
        permit_end = permit_start + timedelta(hours=random.randint(1, 72))

        # Choose a spare description from inventory and make it messy
        inv_row = inventory_df.sample(1).iloc[0]
        clean_desc = inv_row["spare_description"]
        spare_desc_text = make_messy_description(clean_desc, seed=i)

        # Keep track of which inventory spare this was based on (for validation)
        matched_spare_code = inv_row["spare_code"]

        # Quantity used: sometimes None
        qty_used = (
            None if random.random() < 0.1 else random.randint(1, 20)
        )

        # Short remarks field: sometimes empty
        remarks = (
            ""
            if random.random() < 0.4
            else random.choice(REMARKS_TEMPLATES)
        )

        rows.append(
            {
                "permit_id": permit_id,
                "equipment_tag": equip_tag,
                "department": department,
                "permit_start": permit_start,
                "permit_end": permit_end,
                "spare_description_text": spare_desc_text,
                "quantity_used": qty_used,
                "matched_spare_code": matched_spare_code,
                "remarks": remarks,
            }
        )

    df = pd.DataFrame(rows)
    df = df.reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Anomaly injection
# ---------------------------------------------------------------------------

def inject_anomalies(
    inventory_df: pd.DataFrame,
    ptw_df: pd.DataFrame,
    n_each: int = NUM_ANOMALIES_EACH,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Inject exactly *n_each* anomalies of each type (A, B, C) into the PTW
    and inventory DataFrames.  Also builds a ground-truth anomaly log.

    Anomaly types
    -------------
    A – Quantity mismatch  : quantity_used in PTW > quantity_in_stock in inventory
    B – Spare not found    : spare_description_text does not match any inventory item
    C – Temporal anomaly   : date_received (inventory) is AFTER permit_start (PTW)

    Parameters
    ----------
    inventory_df : pd.DataFrame
        Inventory data (may be mutated for Type-C anomalies).
    ptw_df : pd.DataFrame
        PTW data (may be mutated for Type-A and Type-A anomalies).
    n_each : int
        Number of anomalies per type.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]
        (inventory_df_modified, ptw_df_modified, ground_truth_df)
    """
    inv = inventory_df.copy()
    ptw = ptw_df.copy()
    ground_truth = []
    anomaly_id = 1

    # Shuffle indices to pick random rows from each frame
    ptw_indices = ptw.index.tolist()
    random.shuffle(ptw_indices)

    # --- Type A: quantity_used > quantity_in_stock ---
    type_a_ptw_indices = ptw_indices[:n_each]
    for ptw_idx in type_a_ptw_indices:
        # Find the matched spare in inventory
        spare_code = ptw.at[ptw_idx, "matched_spare_code"]
        inv_matches = inv[inv["spare_code"] == spare_code]

        if inv_matches.empty:
            # If no match exists, pick a random inventory row
            inv_idx = random.choice(inv.index.tolist())
            spare_code = inv.at[inv_idx, "spare_code"]
            ptw.at[ptw_idx, "matched_spare_code"] = spare_code
        else:
            inv_idx = inv_matches.index[0]

        # Set stock to a low value, then set quantity_used higher
        low_stock = random.randint(1, 5)
        inv.at[inv_idx, "quantity_in_stock"] = low_stock

        excess_qty = low_stock + random.randint(1, 15)
        ptw.at[ptw_idx, "quantity_used"] = excess_qty

        ground_truth.append(
            {
                "anomaly_id": f"ANO-{anomaly_id:03d}",
                "type": "A",
                "description": "Quantity used in PTW exceeds stock in inventory",
                "ptw_permit_id": ptw.at[ptw_idx, "permit_id"],
                "inventory_spare_code": spare_code,
                "detail": (
                    f"quantity_used={excess_qty} > quantity_in_stock={low_stock}"
                ),
            }
        )
        anomaly_id += 1

    # --- Type B: spare not in inventory (bogus description) ---
    type_b_ptw_indices = ptw_indices[n_each : 2 * n_each]
    for i, ptw_idx in enumerate(type_b_ptw_indices):
        bogus_desc = BOGUS_DESCRIPTIONS[i % len(BOGUS_DESCRIPTIONS)]
        # Apply some messiness to the bogus description too
        ptw.at[ptw_idx, "spare_description_text"] = make_messy_description(
            bogus_desc, seed=1000 + i
        )
        # Clear the matched_spare_code so reconciliation has nothing to link to
        ptw.at[ptw_idx, "matched_spare_code"] = None

        ground_truth.append(
            {
                "anomaly_id": f"ANO-{anomaly_id:03d}",
                "type": "B",
                "description": "PTW spare description does not match any inventory item",
                "ptw_permit_id": ptw.at[ptw_idx, "permit_id"],
                "inventory_spare_code": "N/A",
                "detail": f"PTW description: '{bogus_desc}'",
            }
        )
        anomaly_id += 1

    # --- Type C: date_received in inventory AFTER ptw.permit_start ---
    type_c_ptw_indices = ptw_indices[2 * n_each : 3 * n_each]
    for ptw_idx in type_c_ptw_indices:
        spare_code = ptw.at[ptw_idx, "matched_spare_code"]
        inv_matches = inv[inv["spare_code"] == spare_code]

        if inv_matches.empty:
            inv_idx = random.choice(inv.index.tolist())
            spare_code = inv.at[inv_idx, "spare_code"]
            ptw.at[ptw_idx, "matched_spare_code"] = spare_code
        else:
            inv_idx = inv_matches.index[0]

        # Push date_received to be AFTER the PTW permit_start
        permit_start_date = ptw.at[ptw_idx, "permit_start"]
        if isinstance(permit_start_date, datetime):
            permit_start_date = permit_start_date.date()

        # Set date_received 1–60 days AFTER permit start
        new_received = permit_start_date + timedelta(days=random.randint(1, 60))
        inv.at[inv_idx, "date_received"] = new_received

        ground_truth.append(
            {
                "anomaly_id": f"ANO-{anomaly_id:03d}",
                "type": "C",
                "description": "Inventory item received AFTER PTW permit start (used before arrival)",
                "ptw_permit_id": ptw.at[ptw_idx, "permit_id"],
                "inventory_spare_code": spare_code,
                "detail": (
                    f"date_received={new_received} > permit_start={ptw.at[ptw_idx, 'permit_start'].date()}"
                ),
            }
        )
        anomaly_id += 1

    ground_truth_df = pd.DataFrame(ground_truth)
    return inv, ptw, ground_truth_df


# ---------------------------------------------------------------------------
# Save & summary
# ---------------------------------------------------------------------------

def save_data(
    inventory_df: pd.DataFrame,
    ptw_df: pd.DataFrame,
    ground_truth_df: pd.DataFrame,
) -> dict[str, Path]:
    """
    Persist the three DataFrames as CSV files in the data/ directory.

    Parameters
    ----------
    inventory_df : pd.DataFrame
        Cleaned (but anomaly-modified) inventory data.
    ptw_df : pd.DataFrame
        Messy PTW data with injected anomalies.
    ground_truth_df : pd.DataFrame
        Ground-truth anomaly log.

    Returns
    -------
    dict[str, Path]
        Mapping of dataset name to saved file path.
    """
    paths = {}

    inv_path = DATA_DIR / "inventory_sample.csv"
    inventory_df.to_csv(inv_path, index=False)
    paths["inventory"] = inv_path

    ptw_path = DATA_DIR / "ptw_sample.csv"
    ptw_df.to_csv(ptw_path, index=False)
    paths["ptw"] = ptw_path

    gt_path = DATA_DIR / "ground_truth_anomalies.csv"
    ground_truth_df.to_csv(gt_path, index=False)
    paths["ground_truth"] = gt_path

    return paths


def print_summary(
    inventory_df: pd.DataFrame,
    ptw_df: pd.DataFrame,
    ground_truth_df: pd.DataFrame,
    paths: dict,
) -> None:
    """
    Print a human-readable summary of what was generated.

    Parameters
    ----------
    inventory_df : pd.DataFrame
        Inventory DataFrame.
    ptw_df : pd.DataFrame
        PTW DataFrame.
    ground_truth_df : pd.DataFrame
        Ground-truth anomaly log.
    paths : dict
        File paths returned by :func:`save_data`.
    """
    sep = "=" * 60

    print(f"\n{sep}")
    print("  INVENTORY-PTW RECONCILIATION — DATA GENERATOR SUMMARY")
    print(sep)

    print(f"\n📦  Inventory rows generated  : {len(inventory_df)}")
    print(f"    Categories               : {inventory_df['category'].value_counts().to_dict()}")
    print(f"    Criticality split        : {inventory_df['criticality_flag'].value_counts().to_dict()}")
    print(f"    Null last_used_date      : {inventory_df['last_used_date'].isna().sum()}")
    print(f"    Null expiry_date         : {inventory_df['expiry_date'].isna().sum()}")

    print(f"\n📋  PTW rows generated        : {len(ptw_df)}")
    print(f"    Null quantity_used       : {ptw_df['quantity_used'].isna().sum()}")
    print(f"    Rows with remarks        : {(ptw_df['remarks'] != '').sum()}")

    print(f"\n🔴  Anomalies injected        : {len(ground_truth_df)}")
    for atype, grp in ground_truth_df.groupby("type"):
        print(f"    Type {atype}: {len(grp):2d} rows — {grp['description'].iloc[0]}")

    print(f"\n💾  Files saved:")
    for name, path in paths.items():
        size_kb = path.stat().st_size / 1024
        print(f"    {str(path):<55}  ({size_kb:.1f} KB)")

    print(f"\n{sep}\n")


# ---------------------------------------------------------------------------
# Multi-Month Dataset Generator (Section 16)
# ---------------------------------------------------------------------------

def generate_multi_month_datasets() -> Dict[str, Path]:
    """
    Generate Months 1, 2, and 3 for Inventory and PTW with exact planted cases:
    - 20 qty decreases, 10 increases, 5 new items, 3 removed items, 4 price changes,
      2 location transfers, 2 re-coded items, 1 UoM change, 1 duplicate key,
      1 Total row, codes with leading zeros, 1 item with missing price,
      an overlapping cumulative PTW file, 3 updated permits, 5 cancelled permits,
      2 late-entry permits.
    """
    rng = np.random.default_rng(12345)
    records_m1 = []

    # 1. Base inventory items for Month 1 (August 2026)
    # Include items with leading zeros
    base_codes = [f"{i:06d}" for i in range(101, 161)]  # 60 items with 6-digit codes
    for i, code in enumerate(base_codes):
        loc = f"BIN-{(i % 4) + 1}"
        desc = SPARE_TEMPLATES[i % len(SPARE_TEMPLATES)]
        qty = float(rng.integers(20, 80))
        price = float(rng.integers(50, 2500))
        records_m1.append({
            "spare_code": code,
            "spare_description": desc,
            "storage_location": loc,
            "quantity_in_stock": qty,
            "unit_cost": price,
            "uom": "NOS",
            "department": "Mechanical" if i % 2 == 0 else "Electrical",
            "equipment_tag": f"TAG-{(i % 5) + 1:02d}",
            "date_received": "2026-08-01",
            "last_used_date": "2026-08-15",
            "expiry_date": "2028-12-31",
            "category": "Mechanical" if i % 2 == 0 else "Electrical",
            "criticality_flag": "Medium",
        })

    inv_m1_df = pd.DataFrame(records_m1)

    # 2. Month 2 Inventory (September 2026) derived with planted cases:
    inv_m2_records = []
    ground_truth = []

    for idx, item in enumerate(records_m1):
        m2_item = item.copy()
        code = item["spare_code"]

        # Case: 3 removed items (skip items 0, 1, 2)
        if idx in (0, 1, 2):
            ground_truth.append({"case": "removed_item", "item": code, "details": "Dropped in M2"})
            continue

        # Case: 20 qty decreases (items 3 to 22)
        elif 3 <= idx <= 22:
            dec = float(rng.integers(2, 10))
            m2_item["quantity_in_stock"] = max(1.0, item["quantity_in_stock"] - dec)
            ground_truth.append({"case": "qty_decrease", "item": code, "delta": -dec})

        # Case: 10 qty increases (items 23 to 32)
        elif 23 <= idx <= 32:
            inc = float(rng.integers(5, 25))
            m2_item["quantity_in_stock"] = item["quantity_in_stock"] + inc
            ground_truth.append({"case": "qty_increase", "item": code, "delta": inc})

        # Case: 4 price changes (items 33 to 36)
        elif 33 <= idx <= 36:
            new_p = item["unit_cost"] * 1.15
            m2_item["unit_cost"] = round(new_p, 2)
            ground_truth.append({"case": "price_change", "item": code, "old_p": item["unit_cost"], "new_p": m2_item["unit_cost"]})

        # Case: 2 location transfers (items 37, 38)
        elif idx in (37, 38):
            # Split into two records with equal moves
            move_qty = 5.0
            m2_item["quantity_in_stock"] -= move_qty
            inv_m2_records.append(m2_item)

            transfer_row = m2_item.copy()
            transfer_row["storage_location"] = "BIN-99"
            transfer_row["quantity_in_stock"] = move_qty
            inv_m2_records.append(transfer_row)
            ground_truth.append({"case": "location_transfer", "item": code, "qty": move_qty})
            continue

        # Case: 2 re-coded items (items 39, 40)
        elif idx in (39, 40):
            new_code = f"REC-{code}"
            m2_item["spare_code"] = new_code
            ground_truth.append({"case": "recoded_item", "old_code": code, "new_code": new_code})

        # Case: 1 UoM change (item 41)
        elif idx == 41:
            m2_item["uom"] = "SET"
            ground_truth.append({"case": "uom_change", "item": code, "old_uom": "NOS", "new_uom": "SET"})

        # Case: 1 missing price (item 42)
        elif idx == 42:
            m2_item["unit_cost"] = None
            ground_truth.append({"case": "missing_price", "item": code, "details": "Price carried forward from baseline"})

        inv_m2_records.append(m2_item)

    # Case: 5 new items in Month 2
    for n in range(1, 6):
        new_c = f"NEW-00{n}"
        inv_m2_records.append({
            "spare_code": new_c,
            "spare_description": f"Brand new spare component {n}",
            "storage_location": "BIN-1",
            "quantity_in_stock": 25.0,
            "unit_cost": 450.0,
            "uom": "NOS",
            "department": "Mechanical",
            "equipment_tag": "TAG-01",
            "date_received": "2026-09-05",
            "last_used_date": "2026-09-10",
            "expiry_date": "2028-12-31",
            "category": "Mechanical",
            "criticality_flag": "High",
        })
        ground_truth.append({"case": "new_item", "item": new_c})

    # Case: 1 duplicate key in Month 2 (same code + location)
    dup_row = inv_m2_records[10].copy()
    dup_row["quantity_in_stock"] = 10.0
    dup_row["unit_cost"] = 120.0
    inv_m2_records.append(dup_row)
    ground_truth.append({"case": "duplicate_key", "item": dup_row["spare_code"]})

    # Case: 1 Total row in Month 2
    inv_m2_records.append({
        "spare_code": "TOTAL",
        "spare_description": "Grand Total Summary Row",
        "storage_location": "",
        "quantity_in_stock": 99999.0,
        "unit_cost": 0.0,
        "uom": "",
        "department": "",
        "equipment_tag": "",
        "date_received": "",
        "last_used_date": "",
        "expiry_date": "",
        "category": "",
        "criticality_flag": "",
    })
    ground_truth.append({"case": "total_row", "details": "Summary row to exclude"})

    inv_m2_df = pd.DataFrame(inv_m2_records)

    # Month 3: October 2026
    inv_m3_records = [r.copy() for r in inv_m2_records if r["spare_code"] != "TOTAL"]
    for r in inv_m3_records[:10]:
        r["quantity_in_stock"] = max(1.0, r["quantity_in_stock"] - 2.0)

    # ── Planted Cases for Savings & Monthly Report (Step 9) ───────────────────
    # 1. 6 additions with >6 months cover (should appear in opportunities)
    for i in range(6):
        code = f"OVERSTOCK-ADD-{i+1:02d}"
        inv_m3_records.append({
            "spare_code": code,
            "spare_description": f"Overstock Add Test Part {i+1}",
            "storage_location": "Bay-C",
            "quantity_in_stock": 35.0,
            "unit_cost": 2500.0,
            "uom": "NOS",
            "department": "Mechanical",
            "equipment_tag": f"EQ-OV-{i+1}",
            "date_received": "2026-10-05",
            "last_used_date": "2026-09-15",
            "expiry_date": "",
            "category": "Bearings",
            "criticality_flag": "Medium",
        })
        ground_truth.append({"case": "excess_cover_addition", "item": code, "cover": 12.0})

    # 2. 3 additions with no usage history in window (flags 'no usage history')
    for i in range(3):
        code = f"NO-HIST-ADD-{i+1:02d}"
        inv_m3_records.append({
            "spare_code": code,
            "spare_description": f"No History New Spare {i+1}",
            "storage_location": "Bay-D",
            "quantity_in_stock": 15.0,
            "unit_cost": 8000.0,
            "uom": "NOS",
            "department": "Electrical",
            "equipment_tag": f"EQ-NH-{i+1}",
            "date_received": "2026-10-10",
            "last_used_date": "",
            "expiry_date": "",
            "category": "Electrical",
            "criticality_flag": "Low",
        })
        ground_truth.append({"case": "no_usage_history_addition", "item": code})

    # 3. 2 additions of critical spares (confidence='review_needed')
    for i in range(2):
        code = f"CRIT-SPARE-ADD-{i+1:02d}"
        inv_m3_records.append({
            "spare_code": code,
            "spare_description": f"Critical Turbine Spare {i+1}",
            "storage_location": "Bay-A",
            "quantity_in_stock": 20.0,
            "unit_cost": 15000.0,
            "uom": "SET",
            "department": "Turbine",
            "equipment_tag": f"TRB-CR-{i+1}",
            "date_received": "2026-10-12",
            "last_used_date": "2026-08-20",
            "expiry_date": "",
            "category": "Turbine",
            "criticality_flag": "High",
        })
        ground_truth.append({"case": "critical_spare_addition", "item": code})

    # 4. 2 additions >Rs 2L (needing plant head sign-off)
    inv_m3_records.append({
        "spare_code": "HV-ADD-250K",
        "spare_description": "High Value Boiler Control Module",
        "storage_location": "Bay-E",
        "quantity_in_stock": 6.0,
        "unit_cost": 50000.0,
        "uom": "NOS",
        "department": "Control & Instrumentation",
        "equipment_tag": "BLR-CNTRL-01",
        "date_received": "2026-10-15",
        "last_used_date": "2026-07-10",
        "expiry_date": "",
        "category": "Instrumentation",
        "criticality_flag": "High",
    })
    ground_truth.append({"case": "high_value_addition", "item": "HV-ADD-250K"})

    inv_m3_records.append({
        "spare_code": "HV-ADD-240K",
        "spare_description": "High Value Generator Rotor Bearing Set",
        "storage_location": "Bay-A",
        "quantity_in_stock": 2.0,
        "unit_cost": 120000.0,
        "uom": "SET",
        "department": "Generator",
        "equipment_tag": "GEN-ROTOR-02",
        "date_received": "2026-10-18",
        "last_used_date": "",
        "expiry_date": "",
        "category": "Bearings",
        "criticality_flag": "High",
    })
    ground_truth.append({"case": "high_value_addition", "item": "HV-ADD-240K"})

    # 5. 1 item with price carried from previous month
    if inv_m3_records:
        inv_m3_records[5]["unit_cost"] = None
        ground_truth.append({"case": "carried_price", "item": inv_m3_records[5]["spare_code"]})

    inv_m3_df = pd.DataFrame(inv_m3_records)

    # 3. PTW Datasets: Month 1 & Overlapping Cumulative Month 2
    ptw_m1_records = []
    for p in range(1, 31):
        pid = f"PTW-AUG-{p:03d}"
        target_item = records_m1[p % len(records_m1)]
        ptw_m1_records.append({
            "permit_id": pid,
            "spare_description_text": f"Need {target_item['spare_code']} for maintenance",
            "quantity_used": 2.0,
            "equipment_tag": target_item["equipment_tag"],
            "department": target_item["department"],
            "permit_start": f"2026-08-{min(28, p):02d}",
            "permit_end": f"2026-08-{min(28, p):02d}",
            "status": "Closed",
            "remarks": "Completed without incident",
        })
    ptw_m1_df = pd.DataFrame(ptw_m1_records)

    # Cumulative Month 2 PTW (includes M1 permits + M2 new permits + planted cases)
    ptw_m2_records = []

    # Include existing permits (cumulative)
    for p_orig in ptw_m1_records:
        ptw_m2_records.append(p_orig.copy())

    # Case: 3 updated permits
    for i in (0, 1, 2):
        ptw_m2_records[i]["remarks"] = "Updated remark after audit review"
        ground_truth.append({"case": "updated_permit", "permit": ptw_m2_records[i]["permit_id"]})

    # Case: 5 cancelled permits
    for c in range(1, 6):
        cpid = f"PTW-SEP-CNL-{c:02d}"
        ptw_m2_records.append({
            "permit_id": cpid,
            "spare_description_text": "Cancelled job spare request",
            "quantity_used": 5.0,
            "equipment_tag": "TAG-01",
            "department": "Mechanical",
            "permit_start": f"2026-09-0{c}",
            "permit_end": f"2026-09-0{c}",
            "status": "Cancelled",
            "remarks": "Job cancelled due to rain",
        })
        ground_truth.append({"case": "cancelled_permit", "permit": cpid})

    # Case: 2 late-entry permits (dated in August, but first appearing in Sep file)
    for l in (1, 2):
        lpid = f"PTW-LATE-0{l}"
        ptw_m2_records.append({
            "permit_id": lpid,
            "spare_description_text": f"Late entry spare for {records_m1[l]['spare_code']}",
            "quantity_used": 1.0,
            "equipment_tag": records_m1[l]["equipment_tag"],
            "department": "Mechanical",
            "permit_start": "2026-08-25",
            "permit_end": "2026-08-26",
            "status": "Closed",
            "remarks": "Late log entry submitted in September",
        })
        ground_truth.append({"case": "late_entry_permit", "permit": lpid})

    # New active September permits
    for s in range(1, 21):
        spid = f"PTW-SEP-{s:03d}"
        target_item = records_m1[(s + 20) % len(records_m1)]
        ptw_m2_records.append({
            "permit_id": spid,
            "spare_description_text": f"Need {target_item['spare_code']} for routine overhaul",
            "quantity_used": 3.0,
            "equipment_tag": target_item["equipment_tag"],
            "department": target_item["department"],
            "permit_start": f"2026-09-{min(28, s):02d}",
            "permit_end": f"2026-09-{min(28, s):02d}",
            "status": "Closed",
            "remarks": "Executed routine replacement",
        })

    ptw_m2_df = pd.DataFrame(ptw_m2_records)

    # PTW Month 3 (October)
    ptw_m3_records = []
    for o in range(1, 25):
        opid = f"PTW-OCT-{o:03d}"
        target_item = records_m1[(o + 5) % len(records_m1)]
        ptw_m3_records.append({
            "permit_id": opid,
            "spare_description_text": f"Routine servicing spare {target_item['spare_code']}",
            "quantity_used": 2.0,
            "equipment_tag": target_item["equipment_tag"],
            "department": target_item["department"],
            "permit_start": f"2026-10-{min(28, o):02d}",
            "permit_end": f"2026-10-{min(28, o):02d}",
            "status": "Closed",
            "remarks": "Servicing completed",
        })
    ptw_m3_df = pd.DataFrame(ptw_m3_records)

    # Save all multi-month datasets
    paths = {
        "inv_m1": DATA_DIR / "inventory_month1.csv",
        "inv_m2": DATA_DIR / "inventory_month2.csv",
        "inv_m3": DATA_DIR / "inventory_month3.csv",
        "ptw_m1": DATA_DIR / "ptw_month1.csv",
        "ptw_m2": DATA_DIR / "ptw_month2.csv",
        "ptw_m3": DATA_DIR / "ptw_month3.csv",
        "gt_multi": DATA_DIR / "ground_truth_multimonth.csv",
    }

    inv_m1_df.to_csv(paths["inv_m1"], index=False)
    inv_m2_df.to_csv(paths["inv_m2"], index=False)
    inv_m3_df.to_csv(paths["inv_m3"], index=False)
    ptw_m1_df.to_csv(paths["ptw_m1"], index=False)
    ptw_m2_df.to_csv(paths["ptw_m2"], index=False)
    ptw_m3_df.to_csv(paths["ptw_m3"], index=False)
    pd.DataFrame(ground_truth).to_csv(paths["gt_multi"], index=False)

    print("✅  Multi-month synthetic datasets generated successfully.")
    return paths


def populate_savings_dummy_entries(engine=None) -> list:
    """
    Insert 5 planted savings entries across statuses into savings_entries table.
    Statuses include draft, verified, actioned, voided, and one with data_check='contradicted'.
    """
    from sqlalchemy import text
    from core.database import get_engine, initialize_database
    from core.settings import initialize_settings

    if engine is None:
        db_path = str(DATA_DIR / "reconciliation.db")
        engine = get_engine(db_path)
    initialize_database(engine)
    initialize_settings(engine)

    planted = [
        (
            "VLV-GT-001",
            "price_variance",
            "Oct 2026",
            "Negotiated bulk rebate with valve supplier",
            "Valves",
            "Gate valve 2 inch",
            "Boiler",
            "BLR-VLV-01",
            10.0,
            10.0,
            "NOS",
            4500.0,
            4500000,
            "draft",
            None,
            None,
            None,
            None,
            "ok",
            "Price variance confirmed vs PO history",
        ),
        (
            "BRG-6205",
            "overstock_avoidance",
            "Oct 2026",
            "Avoided purchase order after discovering surplus in Boiler sub-store",
            "Bearings",
            "Deep groove ball bearing 6205",
            "Boiler",
            "BLR-BRG-02",
            20.0,
            20.0,
            "NOS",
            6000.0,
            12000000,
            "verified",
            "tiwari",
            None,
            None,
            None,
            "ok",
            "Surplus verified in sub-store audit",
        ),
        (
            "PMP-MS-021",
            "alternative_part",
            "Oct 2026",
            "Substituted OEM mechanical seal with approved domestic equivalent",
            "Pumps",
            "Mechanical seal type 21",
            "Turbine",
            "TRB-PMP-01",
            5.0,
            5.0,
            "NOS",
            17000.0,
            8500000,
            "actioned",
            "tiwari",
            "tiwari",
            None,
            None,
            "ok",
            "Approved alternate installed on Feed Pump 2",
        ),
        (
            "GSK-SW-100",
            "specification_rationalization",
            "Oct 2026",
            "Cancelled duplicate PR log entry",
            "Gaskets",
            "Spiral wound gasket DN100",
            "Turbine",
            "TRB-GSK-03",
            25.0,
            25.0,
            "NOS",
            1000.0,
            2500000,
            "voided",
            None,
            None,
            "tiwari",
            "Duplicate requisition logged",
            "voided",
            "Voided by store manager",
        ),
        (
            "OVERSTOCK-ADD-01",
            "overstock_avoidance",
            "Oct 2026",
            "Avoided order of Overstock Add Test Part 1",
            "Bearings",
            "Overstock Add Test Part 1",
            "Mechanical",
            "EQ-OV-1",
            24.0,
            24.0,
            "NOS",
            2500.0,
            6000000,
            "draft",
            None,
            None,
            None,
            None,
            "contradicted",
            "Addition of 35 units logged in Oct 2026 despite claimed avoidance",
        ),
    ]

    entry_ids = []
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        for (
            code,
            stype,
            period,
            desc,
            cat,
            item_desc,
            dept,
            eq_tag,
            req_qty,
            avd_qty,
            uom,
            u_price,
            paise,
            status,
            vby,
            aby,
            void_by,
            v_reason,
            dcheck,
            ddetail,
        ) in planted:
            conn.execute(
                text("DELETE FROM savings_entries WHERE material_code = :c AND period = :p"),
                {"c": code, "p": period},
            )
            res = conn.execute(
                text(
                    """
                    INSERT INTO savings_entries (
                        period, action_date, item_key, material_code, description,
                        department, equipment_tag, type, requested_qty, avoided_qty,
                        uom, unit_price, price_source, amount_paise, order_value_paise,
                        status, data_check, data_check_period, created_by, created_at,
                        verified_by, verified_at, voided_by, voided_at, void_reason, note
                    ) VALUES (
                        :period, '2026-10-15', :material_code, :material_code, :description,
                        :department, :equipment_tag, :type, :requested_qty, :avoided_qty,
                        :uom, :unit_price, 'inventory_latest', :amount_paise, :amount_paise,
                        :status, :data_check, :period, 'tiwari', :created_at,
                        :verified_by, :verified_at, :voided_by, :voided_at, :void_reason, :note
                    )
                    """
                ),
                {
                    "period": period,
                    "material_code": code,
                    "description": item_desc,
                    "department": dept,
                    "equipment_tag": eq_tag,
                    "type": stype,
                    "requested_qty": req_qty,
                    "avoided_qty": avd_qty,
                    "uom": uom,
                    "unit_price": u_price,
                    "amount_paise": paise,
                    "status": status,
                    "data_check": dcheck,
                    "created_at": now,
                    "verified_by": vby,
                    "verified_at": now if vby else None,
                    "voided_by": void_by,
                    "voided_at": now if void_by else None,
                    "void_reason": v_reason,
                    "note": ddetail,
                },
            )
            entry_ids.append(res.lastrowid)

    print(f"✅  Planted {len(entry_ids)} sample savings log entries into database.")
    return entry_ids


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Orchestrate the full data generation pipeline:
      1. Generate single-month baseline datasets
      2. Generate multi-month test datasets (Months 1, 2, 3)
      3. Populate planted savings log cases into active DB
    """
    print("🚀  Starting synthetic data generation (seed=42)…")

    # Step 1: Generate clean inventory
    print("    [1/5] Generating inventory data…")
    inventory_df = generate_inventory(NUM_INVENTORY)

    # Step 2: Generate messy PTW data referencing inventory
    print("    [2/5] Generating PTW data with messy descriptions…")
    ptw_df = generate_ptw(inventory_df, NUM_PTW)

    # Step 3: Inject anomalies
    print("    [3/5] Injecting anomalies (10 × Type A, B, C)…")
    inventory_df, ptw_df, ground_truth_df = inject_anomalies(
        inventory_df, ptw_df, NUM_ANOMALIES_EACH
    )

    # Step 4: Save to CSV
    print("    [4/5] Saving CSV files to data/…")
    paths = save_data(inventory_df, ptw_df, ground_truth_df)

    # Step 5: Multi-month generation (Section 16 & 18)
    print("    [5/5] Generating multi-month test datasets…")
    multi_paths = generate_multi_month_datasets()

    # Step 6: Plant savings entries
    populate_savings_dummy_entries()

    # Print summary
    print_summary(inventory_df, ptw_df, ground_truth_df, paths)



if __name__ == "__main__":
    main()


