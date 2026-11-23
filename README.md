# Recon Dashboard — Captive Power Plant (CPP) Inventory × PTW Reconciliation

A web application designed for industrial plants to reconcile monthly **Inventory** (storage department stock listings) against **Permit to Work (PTW)** (operations department maintenance event logs).

> **Security Note for Real Plant Data**: For real plant operations data, host this application exclusively within the secure internal plant network (LAN/intranet) with IT clearance. No external network connections are made with plant data.

---

## 1. System Overview

- **Snapshot-based Inventory Diffing**: Storage snapshots represent monthly stock listings. Changes are computed by comparing snapshot $N$ with baseline snapshot $N-1$.
- **Exact Financial Reconciliation Identity**:
  $$\text{Opening Value} + \text{Added} - \text{Reduced} + \text{Revaluation} = \text{Closing Value} \quad (\pm\text{Rs } 1)$$
- **PTW Event Record Merging**: PTWs are event records merged by normalized permit key (`permit_id`). Changed records are tracked with full field history in `ptw_permit_history`.
- **3-Tier Matching Algorithm**:
  1. Regex part code extraction ($100\%$ confidence, `code_regex`)
  2. Confirmed user matches ($100\%$ confidence, `confirmed`)
  3. RapidFuzz on description with equipment tag matching bonus ($+10$)
- **Variance Window Analysis**: Window is strictly `(baseline_as_of_date, new_as_of_date]`. Only closed permits within the window are matched against store reductions.
- **Security & RBAC**: `bcrypt` password hashing, account lockout ($5$ attempts / $5$ minutes), $30$-minute inactivity timeout, and server-side role enforcement (admin vs viewer).

---

## 2. Quickstart & Run Instructions

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 2: Create Administrator & Viewer Users
Use the command-line interface script:
```bash
# Create Admin User
python3 scripts/create_user.py --username admin --password "admin123" --name "Plant Admin" --role admin

# Create Viewer User
python3 scripts/create_user.py --username viewer --password "viewer123" --name "Plant Viewer" --role viewer
```

### Step 3: Generate Multi-Month Synthetic Data
Generates Month 1 (Aug 2026), Month 2 (Sep 2026), and Month 3 (Oct 2026) datasets with planted edge cases:
```bash
python3 core/generate_dummy_data.py
```

### Step 4: Run the Complete Test Suite (77 Automated Tests)
```bash
python3 -m pytest tests/ -v
```

### Step 5: Launch the Streamlit Web Application
```bash
streamlit run app/main.py
```
Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## 3. Manual Test Walkthrough Script

Follow these steps in the UI to verify the system end-to-end:

1. **Login Screen**:
   - Navigate to `http://localhost:8501`. Verify full-viewport dark teal background with glass card and green `LET'S GOOO` button.
   - Test failed login: enter `admin` with wrong password. Verify single error line: `"Invalid username or password."`
   - Test lockout: enter wrong password $5$ times. Verify `"Account locked due to multiple failed attempts"` message.
   - Enter `admin` / `admin123`. Click `LET'S GOOO` (or press Enter).
2. **Landing Screen**:
   - Verify `"Welcome, Plant Admin"` heading with `"Upload new data"` and `"View previous analysis"` cards.
   - Click `"Start upload"` to enter the Upload screen.
3. **Upload Month 1 (Baseline)**:
   - On LEFT partition: select `"Upload new data"`, upload `data/inventory_month1.csv`. Set as-of date to `2026-08-31`. Status chip shows `"Ready"`.
   - On RIGHT partition: select `"Upload new data"`, upload `data/ptw_month1.csv`. Set as-of date to `2026-08-31`. Status chip shows `"Ready"`.
   - Click enabled `"PROCEED TO REVIEW"` button.
   - Verify `"Baseline created"` summary in Review screen. Check acknowledge box and click `"Confirm & publish"`.
4. **Upload Month 2**:
   - In sidebar, click `"Update data"`.
   - Upload `data/inventory_month2.csv` (`2026-09-30`) on left, `data/ptw_month2.csv` (`2026-09-30`) on right.
   - Notice: Total rows filtered out, negative quantities flagged, transfer detected.
   - Click `"PROCEED TO REVIEW"`.
   - Verify waterfall chart, KPI rows, and green `"Reconciled"` chip.
   - Check acknowledge box and click `"Confirm & publish"`.
5. **Idempotent Duplicate Upload**:
   - Try uploading `data/inventory_month2.csv` again for the same period.
   - Verify error banner: `"Identical file hash already committed for this dataset."`
6. **Wrong Slot Detection**:
   - Try uploading `data/ptw_month2.csv` into the Inventory partition.
   - Verify error: `"This file contains permit columns and appears to be a PTW file."`
7. **Replace & Void (Admin)**:
   - In sidebar, open `"Upload history"`.
   - View versioned upload registry table.
   - Select latest active upload and type `"VOID"` to execute rollback.
   - Verify previous version is restored to active status.

---

## 4. Key Design Decisions

See [docs/DECISIONS.md](docs/DECISIONS.md) for full architectural records:
- **D1**: Python `Decimal` arithmetic for financial totals (tolerance Rs 1).
- **D2**: `bcrypt` hashing with salt rounds = 12, account lockout after 5 attempts.
- **D3**: Relational schema with migrations via `schema_version`.
- **D4**: Item keys format `material_code|storage_location` preserving text leading zeros.
- **D5**: Temporal baseline detection by data `as_of_date`.
- **D6**: Transfer exclusion from Added and Reduced monetary flows.
- **D7**: Automatic cumulative vs monthly detection for PTW files.
- **D8**: 3-tier matching (regex code, confirmed matches, RapidFuzz with equipment bonus).
- **D9**: Variance window `(baseline_as_of, new_as_of]` with closed status filter.
- **D10**: Atomic transaction pipeline with automatic SQLite database backups in `data/backups/`.

---

## 5. Known Limitations

1. **Monthly Granularity**: Designed for monthly plant snapshots; intra-month daily issue/receipt transactional registers are not captured.
2. **Transfer Inference**: Location transfers are inferred from matching equal-and-opposite quantity deltas within the same material code during the window.
3. **Fuzzy String Thresholds**: Unmatched items with text similarity below 70% require manual confirmation in the Reconciliation review queue.
