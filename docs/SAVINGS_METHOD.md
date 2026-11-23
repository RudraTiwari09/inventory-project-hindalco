# Savings Tracking & Excess Stock Methodology

This document defines how cost avoidance opportunities, actioned savings, verified savings, and excess stock are calculated and tracked in the Recon Dashboard. The exact text and definitions below are shared across the application interface, methodology panels, and monthly reports.

## Core Definitions

1. **Opportunity**
   - Identified automatically by the system when a stock addition exceeds the target cover.
   - Represents potential procurement to review and avoid.
   - **An opportunity is NOT money saved.** It does not alter actual financial statements until formally reviewed and actioned.

2. **Actioned**
   - An authorized user (admin) has reviewed a purchase requisition, purchase order, or replenishment request and recorded an avoided or reduced order.
   - Avoided quantity and unit price are recorded, and the total value (in integer paise) is frozen at the time of logging.
   - Remains in "Actioned" status awaiting verification and data validation.

3. **Verified**
   - Confirmed by an authorized reviewer following automated data consistency checks (confirming stock did not subsequently rise by the avoided quantity).
   - Only verified entries represent audited cost avoidance and are counted toward official verified reporting totals.

4. **Excess Stock**
   - Inventory held above the defined target cover (`months_of_cover × average_monthly_consumption`).
   - Represents working capital exposure and holding cost risk.
   - **Excess stock is NEVER added to savings totals.** It is tracked separately as an operational exposure metric.

---

## Calculation Rules

1. **Monthly Consumption Estimate**
   - For each item, monthly consumption is calculated as:
     `max(matched PTW closed quantity, store reduction quantity from inventory diff excluding transfers)`
   - Only **Confirmed** (user-confirmed) and **Auto** (high-confidence part number match) PTW line matches are counted. **Probable matches are strictly excluded.**
   - The monthly consumption rate is the arithmetic average across the last `history_window_months` (default: 12) published periods in which the item was present in inventory.
   - Requires at least `min_history_months` (default: 3) published periods of history. Items with fewer periods are marked **"Not enough history"** and receive no monetary opportunity valuation.

2. **Target Stock Level**
   - `Target Stock = months_of_cover × average_monthly_consumption`
   - Default target cover is 6 months (configurable by admin).

3. **Avoidable Quantity**
   - Evaluated for all stock additions (change types: `Qty increase` and `New item`).
   - Transfers between bins, price-only adjustments, unit-of-measure changes, and re-codes are strictly excluded.
   - `avoidable_qty = min(added_qty, max(0, closing_qty - target_qty))`

4. **Opportunity Valuation**
   - `avoidable_value = avoidable_qty × min(baseline_unit_price, new_unit_price)`
   - Arithmetic is computed using integer paise to avoid floating-point drift.
   - Items with carried or missing unit prices are designated as **"Unvalued"** and included at Rs 0 in monetary totals.

5. **Confidence Classification & Exclusions**
   - **High Confidence**: At least 6 published periods of history and positive observed consumption.
   - **Medium Confidence**: 3 to 5 published periods of history and positive observed consumption.
   - **Low Confidence**: Published history meets the 3-month threshold, but zero consumption was logged (e.g. strategic commissioning or insurance spares).
   - **Critical Spares Exclusion**: Items flagged as critical in inventory records are excluded from savings opportunities with the note *"Critical spare"*, ensuring operational reliability is never compromised.
   - Summaries always present High, Medium, and Low confidence opportunities separately.

6. **High-Value Threshold**
   - Any addition with an avoidable value of at least `plant_head_threshold` (default: Rs 2,00,000) is flagged as **"Above Rs 2L"** requiring plant head review.

7. **Excess Stock Tracking**
   - For all non-critical catalog items with at least 3 months of history:
     `excess_qty = max(0, current_qty - target_qty)`
     `excess_value = excess_qty × unit_price`
   - Total excess stock value is persisted per published period to generate historical trend analytics.

8. **Lifecycle & Status Preservation**
   - Opportunities are generated upon publication of a new monthly inventory snapshot.
   - Recomputing opportunities (e.g., when matches are confirmed or an upload is replaced) preserves existing user decisions (`converted` or `dismissed` states with recorded reasons) for matching `(period, item_key)`.

