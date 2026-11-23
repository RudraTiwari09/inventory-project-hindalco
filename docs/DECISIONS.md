# Architectural & Design Decisions

This document records key assumptions, architectural patterns, and design choices made during the implementation of the Inventory x PTW Reconciliation Dashboard.

## D1: Decimal Arithmetic for Monetary Values
- **Decision**: All financial calculations (item valuation, price effect, quantity effect, closing totals, reconciliation checks) use Python's `Decimal` type rounded to 2 decimal places (or integer paise internally) to eliminate IEEE-754 floating-point inaccuracies.
- **Rationale**: Financial reconciliation requires exact identity: `Opening + Added - Reduced + Revaluation = Closing` within a tolerance of Rs 1. Floating-point arithmetic introduces drift.

## D2: Authentication and Password Security
- **Decision**: User authentication uses `bcrypt` for password hashing with account lockout (5 consecutive failed attempts lock the account for 5 minutes). Session state tracks `last_activity_time` with a 30-minute inactivity timeout.
- **Rationale**: Complies with Section 14 requirements. Plain-text passwords are never logged or stored. Role enforcement (admin vs viewer) is verified server-side on all data-mutating endpoints.

## D3: Database Architecture and Backwards Compatibility
- **Decision**: Retain helper functions in `core/database.py` (`get_engine`, `save_dataframe`, `load_dataframe`, `save_column_mapping`, `load_column_mapping`, `get_table_info`) while extending the schema with the 14 new relational tables specified in Section 15.
- **Rationale**: Ensures existing tests (`test_cleaning.py`) and legacy access patterns continue to pass without regression.

## D4: Item Key Definition
- **Decision**: Item key defaults to `f"{material_code}|{storage_location}"` when storage location is provided, or `material_code` if storage location is absent. Codes are strictly preserved as TEXT (leading zeros retained).
- **Rationale**: Captive power plant inventory tracks items by bin/location in warehouses; transfers occur between locations of the same material code.

## D5: Baseline Snapshot Selection
- **Decision**: Baseline snapshot for diff calculation is identified by the latest active snapshot of that dataset with an `as_of_date` strictly earlier than the current upload's `as_of_date`.
- **Rationale**: Files may arrive out of order (e.g. backfills). Comparing by data date rather than upload timestamp ensures temporal validity.

## D6: Transfer Detection and Exclusion from Value Flows
- **Decision**: When two item keys share the same `material_code` across distinct storage locations and exhibit equal and opposite quantity deltas within the same monthly window, they are classified as `Transfer`.
- **Rationale**: Plant transfers between bins do not represent additions (procurement) or reductions (consumption). Excluded from Added and Reduced monetary and quantity totals to prevent distortion.

## D7: Cumulative vs Monthly PTW Detection
- **Decision**: When a PTW upload is processed, the system compares its unique permit keys against already stored keys. If > 50% of keys already exist in the database, the file is classified as `Cumulative`, otherwise `Monthly`.
- **Rationale**: Operations departments frequently export year-to-date cumulative logs rather than isolated monthly delta files.

## D8: 3-Tier Matching Algorithm
- **Decision**: Matching between PTW text lines and inventory items operates in strict hierarchical tiers:
  1. Regex extraction of known inventory material codes from text (100% confidence, 'code_regex').
  2. User confirmed mappings from `user_confirmed_matches` (100% confidence, 'confirmed').
  3. RapidFuzz token sort ratio on normalized descriptions with a +10 bonus for matching equipment tags. Scores >= 90 are Auto, 70-89 are Probable, < 70 are Unmatched.
- **Rationale**: Prioritizes explicit part numbers and human-confirmed associations before falling back to statistical string similarity.

## D9: Comparison Window Logic
- **Decision**: The comparison window for variance analysis is `(baseline_as_of_date, new_as_of_date]` rather than calendar month boundaries. Only permits whose assigned start date falls strictly inside this window and status is `Closed` are counted as usage.
- **Rationale**: Storage inventory snapshots are taken on specific cutoff dates that may differ from calendar month ends.

## D10: Staging Pipeline and Double-Submit Prevention
- **Decision**: New uploads are initially staged with `status='staged'`. Double clicks are prevented via session state disabling. Raw files are archived read-only in `data/raw/{dataset}/{period}_{hash8}.ext` and database backups are created in `data/backups/` (retaining 12 copies). Commits occur in a single atomic transaction.
- **Rationale**: Prevents partial corruptions, enforces data immutability, and provides complete disaster recovery and rollback capability.

## D11: Savings Tracker and Excess Stock Separation
- **Decision**: `savings_entries` tracks realized cost avoidance in integer paise, frozen at the time of entry. Opportunities (`savings_opportunities`) are potential avoidance candidates and are never counted as savings until actioned. Excess stock (`excess_stock`) represents working capital exposure above target cover and is strictly isolated from savings.
- **Rationale**: Conflating potential opportunities or existing excess holding with realized cash/cost avoidance creates false accounting. Financial audit requires immutable frozen values and distinct classifications.

## D12: Non-Destructive Schema Versioning and Migration
- **Decision**: Schema upgrades from v2 to v3 introduce `savings_entries`, `savings_opportunities`, `excess_stock`, and `report_runs` without dropping or modifying legacy tables. A database backup is captured automatically to `data/backups/` before any migration executes.
- **Rationale**: Guarantees zero data loss and ensures continuous backwards compatibility with existing reconciliation and diff test suites.

## D13: Financial Year and Single Source of Truth Reporting
- **Decision**: Financial years are defined from April 1 to March 31 by default (`fy_start_month = 4`), labelled as "FY YYYY-YY". All report calculations and visualizations derive exclusively from `core/reporting/metrics.py:build_report_data()` and `core/savings.py`.
- **Rationale**: Prevents conflicting numbers across UI tabs, Excel workbooks, PDF documents, and presentations.

## D14: Multi-Format Report Exporters and Offline Isolation
- **Decision**: All report exporters (Excel via `xlsxwriter`, PDF via `reportlab` + `matplotlib` Agg, PowerPoint via `python-pptx`, and ZIP via `zipfile`) operate strictly offline without external network calls or LLM dependencies.
- **Rationale**: Meets industrial security requirements for captive power plants where operational data cannot leave air-gapped or restricted internal networks.

## D15: Cryptographic Report Fingerprinting and Idempotence
- **Decision**: Report generation derives a 16-character SHA-256 fingerprint from the tuple of inventory upload ID/version, PTW upload ID/version, baseline ID, latest match confirmation timestamp, latest savings modification timestamp, and configuration thresholds.
- **Rationale**: Enables instant cache validation in `report_runs`. The UI detects whether data changed since the last run and flags out-of-date reports without re-running heavy export jobs.

## D16: Partial-Failure Resilience
- **Decision**: In the multi-step report generator, format generation steps are wrapped in isolated try-except blocks. If one format fails, surviving formats are still bundled into the download package and ZIP archive, and warnings are surfaced to the user.
- **Rationale**: Prevents minor formatting or dependency issues in one export format from blocking management access to the entire reporting package.

## D17: Physical Sign-Off and Meeting Readiness
- **Decision**: Executive PDF and PowerPoint decks embed empty owner/status brackets (`[ ________ ]`, `[ To Review ]`), physical checklist checkboxes `[ ]`, and signature lines for Store In-Charge, Maintenance Head, and Plant Head.
- **Rationale**: Monthly reconciliation requires formal executive sign-off in monthly plant committee reviews before audit filing.


