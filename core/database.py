"""
Database layer for the Inventory-PTW Reconciliation Dashboard.
Uses SQLAlchemy with SQLite for local storage.
Supports schema versioning, audit logging, snapshot uploads, and reconciliation.
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import pandas as pd
from sqlalchemy import (
    create_engine,
    text,
    inspect,
)

# Module-level engine cache: maps db_path -> SQLAlchemy engine
_engine_cache: dict = {}

# Set up module-level logger
logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 3


def get_engine(db_path: str = "data/reconciliation.db"):
    """
    Return a (cached) SQLAlchemy engine for the given SQLite database path.
    Creates parent directories if necessary.
    """
    db_path = os.path.abspath(db_path)

    if db_path in _engine_cache:
        return _engine_cache[db_path]

    parent_dir = os.path.dirname(db_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    url = f"sqlite:///{db_path}"
    # Use check_same_thread=False for Streamlit multi-threading with SQLite
    engine = create_engine(url, echo=False, future=True, connect_args={"check_same_thread": False})
    _engine_cache[db_path] = engine
    logger.info("Engine created for %s", db_path)
    return engine


def initialize_database(engine) -> None:
    """
    Create all required tables in the database if they do not already exist.
    Maintains schema_version table for migrations.
    """
    ddl_statements = [
        # ── schema_version ─────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL,
            notes       TEXT
        )
        """,

        # ── users ──────────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            full_name       TEXT NOT NULL,
            role            TEXT NOT NULL DEFAULT 'viewer',
            failed_attempts INTEGER DEFAULT 0,
            locked_until    TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
        """,

        # ── uploads ────────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS uploads (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset         TEXT NOT NULL,          -- 'inventory' or 'ptw'
            period          TEXT NOT NULL,          -- e.g. 'Oct 2026' or '2026-10'
            as_of_date      TEXT NOT NULL,          -- 'YYYY-MM-DD'
            file_name       TEXT NOT NULL,
            file_hash       TEXT NOT NULL,
            stored_path     TEXT,
            uploaded_by     TEXT NOT NULL,
            uploaded_at     TEXT NOT NULL,
            status          TEXT NOT NULL,          -- 'staged', 'active', 'superseded', 'voided'
            version         INTEGER NOT NULL DEFAULT 1,
            row_count       INTEGER DEFAULT 0,
            rejected_count  INTEGER DEFAULT 0,
            mapping_id      INTEGER,
            is_baseline     INTEGER DEFAULT 0,
            notes           TEXT
        )
        """,

        # ── inventory_items ────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS inventory_items (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id           INTEGER NOT NULL,
            item_key            TEXT NOT NULL,
            material_code       TEXT NOT NULL,
            location            TEXT,
            description         TEXT,
            uom                 TEXT,
            qty                 REAL NOT NULL,
            unit_price          REAL,
            price_carried       INTEGER DEFAULT 0,
            value               REAL,
            last_used_date      TEXT,
            last_received_date  TEXT,
            expiry_date         TEXT,
            department          TEXT,
            category            TEXT,
            criticality         TEXT,
            equipment_tag       TEXT,
            FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE
        )

        """,

        # ── inventory_diff ─────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS inventory_diff (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id           INTEGER NOT NULL,
            baseline_upload_id  INTEGER,
            item_key            TEXT NOT NULL,
            change_type         TEXT NOT NULL,
            qty_old             REAL,
            qty_new             REAL,
            qty_delta           REAL,
            price_old           REAL,
            price_new           REAL,
            qty_effect          REAL,
            price_effect        REAL,
            flags               TEXT,
            FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE
        )
        """,

        # ── ptw_permits ────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS ptw_permits (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id           INTEGER NOT NULL,
            permit_key          TEXT NOT NULL,
            permit_id           TEXT NOT NULL,
            department          TEXT,
            equipment_tag       TEXT,
            permit_start        TEXT,
            permit_end          TEXT,
            issue_date          TEXT,
            period              TEXT,
            status              TEXT,
            remarks             TEXT,
            row_hash            TEXT,
            first_seen_upload_id INTEGER,
            FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE
        )
        """,

        # ── ptw_lines ──────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS ptw_lines (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            permit_key          TEXT NOT NULL,
            upload_id           INTEGER NOT NULL,
            line_number         INTEGER DEFAULT 1,
            raw_description     TEXT,
            cleaned_description TEXT,
            quantity            REAL,
            uom                 TEXT,
            matched_item_key    TEXT,
            match_method        TEXT,
            match_confidence    REAL,
            status              TEXT,
            FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE
        )
        """,

        # ── ptw_permit_history ─────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS ptw_permit_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            permit_key  TEXT NOT NULL,
            upload_id   INTEGER NOT NULL,
            field_name  TEXT NOT NULL,
            old_value   TEXT,
            new_value   TEXT,
            changed_at  TEXT NOT NULL
        )
        """,

        # ── item_aliases ───────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS item_aliases (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            old_code    TEXT NOT NULL UNIQUE,
            new_code    TEXT NOT NULL,
            created_by  TEXT,
            created_at  TEXT,
            notes       TEXT
        )
        """,

        # ── user_confirmed_matches ─────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS user_confirmed_matches (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ptw_description     TEXT NOT NULL UNIQUE,
            inventory_item_key  TEXT NOT NULL,
            confirmed_by        TEXT,
            confirmed_at        TEXT,
            notes               TEXT
        )
        """,

        # ── gap_review ─────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS gap_review (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_key    TEXT NOT NULL,
            period      TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'Open',
            comment     TEXT,
            user        TEXT,
            time        TEXT
        )
        """,

        # ── rejected_rows ──────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS rejected_rows (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id   INTEGER,
            dataset     TEXT NOT NULL,
            row_number  INTEGER,
            raw_content TEXT,
            reason      TEXT
        )
        """,

        # ── settings ───────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            description TEXT,
            updated_at  TEXT,
            updated_by  TEXT
        )
        """,

        # ── audit_log ──────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type  TEXT NOT NULL,
            details     TEXT,
            username    TEXT,
            ip_address  TEXT,
            timestamp   TEXT NOT NULL
        )
        """,

        # ── mappings ───────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS mappings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset         TEXT NOT NULL,
            standard_field  TEXT NOT NULL,
            source_column   TEXT NOT NULL,
            created_at      TEXT,
            updated_at      TEXT
        )
        """,

        # ── Legacy tables for backwards compatibility ──────────────────────
        """
        CREATE TABLE IF NOT EXISTS inventory_cleaned (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            spare_code                TEXT,
            spare_description_cleaned TEXT,
            equipment_tag             TEXT,
            quantity_in_stock         REAL,
            unit_cost                 REAL,
            date_received             TEXT,
            last_used_date            TEXT,
            expiry_date               TEXT,
            category                  TEXT,
            department                TEXT,
            criticality_flag          TEXT,
            raw_data                  TEXT,
            upload_timestamp          TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ptw_cleaned (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            permit_id                 TEXT,
            spare_description_cleaned TEXT,
            quantity_used             REAL,
            equipment_tag             TEXT,
            department                TEXT,
            permit_start              TEXT,
            permit_end                TEXT,
            remarks                   TEXT,
            raw_data                  TEXT,
            upload_timestamp          TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS column_mappings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_name    TEXT,
            standard_field  TEXT,
            uploaded_column TEXT,
            created_at      TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS confirmed_matches (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            inventory_spare_code TEXT,
            ptw_permit_id        TEXT,
            match_confidence     REAL,
            confirmed_by_user    INTEGER,
            confirmed_at         TEXT,
            notes                TEXT
        )
        """,

        # ── savings_entries ────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS savings_entries (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            period              TEXT NOT NULL,
            action_date         TEXT NOT NULL,
            item_key            TEXT,
            material_code       TEXT,
            description         TEXT,
            department          TEXT,
            equipment_tag       TEXT,
            type                TEXT NOT NULL,
            requested_qty       REAL NOT NULL,
            avoided_qty         REAL NOT NULL,
            uom                 TEXT,
            unit_price          REAL NOT NULL,
            price_source        TEXT NOT NULL DEFAULT 'inventory_latest',
            price_overridden    INTEGER DEFAULT 0,
            amount_paise        INTEGER NOT NULL,
            order_value_paise   INTEGER,
            reference           TEXT,
            requested_by        TEXT,
            note                TEXT,
            source              TEXT NOT NULL DEFAULT 'manual',
            opportunity_id      INTEGER,
            status              TEXT NOT NULL DEFAULT 'actioned',
            data_check          TEXT NOT NULL DEFAULT 'pending',
            data_check_period   TEXT,
            created_by          TEXT NOT NULL,
            created_at          TEXT NOT NULL,
            verified_by         TEXT,
            verified_at         TEXT,
            void_reason         TEXT,
            voided_by           TEXT,
            voided_at           TEXT,
            version             INTEGER NOT NULL DEFAULT 1
        )
        """,

        # ── savings_opportunities ──────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS savings_opportunities (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            period                  TEXT NOT NULL,
            upload_id               INTEGER NOT NULL,
            item_key                TEXT NOT NULL,
            added_qty               REAL NOT NULL,
            baseline_qty            REAL,
            closing_qty             REAL,
            monthly_consumption_est REAL,
            target_qty              REAL,
            months_cover_after      REAL,
            avoidable_qty           REAL NOT NULL,
            unit_price              REAL,
            avoidable_value_paise   INTEGER NOT NULL DEFAULT 0,
            confidence              TEXT NOT NULL,
            flags                   TEXT,
            status                  TEXT NOT NULL DEFAULT 'open',
            dismiss_reason          TEXT,
            converted_entry_id      INTEGER
        )
        """,

        # ── excess_stock ───────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS excess_stock (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            period              TEXT NOT NULL,
            item_key            TEXT NOT NULL,
            qty                 REAL NOT NULL,
            target_qty          REAL NOT NULL,
            excess_qty          REAL NOT NULL,
            excess_value_paise  INTEGER NOT NULL
        )
        """,

        # ── report_runs ────────────────────────────────────────────────────
        """
        CREATE TABLE IF NOT EXISTS report_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            period          TEXT NOT NULL,
            version         INTEGER NOT NULL DEFAULT 1,
            generated_by    TEXT NOT NULL,
            generated_at    TEXT NOT NULL,
            fingerprint     TEXT NOT NULL,
            formats         TEXT NOT NULL,
            file_paths      TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'current',
            duration_s      REAL
        )
        """,
    ]

    indices = [
        "CREATE INDEX IF NOT EXISTS idx_uploads_ds_status ON uploads(dataset, status)",
        "CREATE INDEX IF NOT EXISTS idx_uploads_period ON uploads(period)",
        "CREATE INDEX IF NOT EXISTS idx_inv_items_upload ON inventory_items(upload_id)",
        "CREATE INDEX IF NOT EXISTS idx_inv_items_key ON inventory_items(item_key)",
        "CREATE INDEX IF NOT EXISTS idx_inv_diff_upload ON inventory_diff(upload_id)",
        "CREATE INDEX IF NOT EXISTS idx_ptw_permits_key ON ptw_permits(permit_key)",
        "CREATE INDEX IF NOT EXISTS idx_ptw_permits_upload ON ptw_permits(upload_id)",
        "CREATE INDEX IF NOT EXISTS idx_ptw_lines_upload ON ptw_lines(upload_id)",
        "CREATE INDEX IF NOT EXISTS idx_ptw_lines_item ON ptw_lines(matched_item_key)",
        "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_savings_entries_period ON savings_entries(period)",
        "CREATE INDEX IF NOT EXISTS idx_savings_entries_item ON savings_entries(item_key)",
        "CREATE INDEX IF NOT EXISTS idx_savings_entries_status ON savings_entries(status)",
        "CREATE INDEX IF NOT EXISTS idx_savings_opp_period ON savings_opportunities(period)",
        "CREATE INDEX IF NOT EXISTS idx_savings_opp_item ON savings_opportunities(item_key)",
        "CREATE INDEX IF NOT EXISTS idx_savings_opp_status ON savings_opportunities(status)",
        "CREATE INDEX IF NOT EXISTS idx_excess_stock_period ON excess_stock(period)",
        "CREATE INDEX IF NOT EXISTS idx_report_runs_period ON report_runs(period, version)",
    ]

    try:
        # DB Backup before migration if upgrading schema version
        try:
            from core.pipeline import backup_database
            backup_database()
        except Exception:
            pass

        with engine.connect() as conn:
            for stmt in ddl_statements:
                conn.execute(text(stmt))
            for idx in indices:
                conn.execute(text(idx))

            # Record schema version
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                text(
                    """
                    INSERT OR REPLACE INTO schema_version (version, applied_at, notes)
                    VALUES (:v, :a, :n)
                    """
                ),
                {"v": CURRENT_SCHEMA_VERSION, "a": now, "n": "Schema v3: Savings tracker, excess stock, and report runs"},
            )

            # Auto-migrate any columns added in later versions for existing databases
            inv_cols = [c[1] for c in conn.execute(text("PRAGMA table_info(inventory_items)")).fetchall()]
            if "equipment_tag" not in inv_cols and "id" in inv_cols:
                conn.execute(text("ALTER TABLE inventory_items ADD COLUMN equipment_tag TEXT"))

            conn.commit()
        logger.info("Database initialized successfully with schema v%d.", CURRENT_SCHEMA_VERSION)
    except Exception as exc:
        logger.error("Failed to initialize database: %s", exc)
        raise


def save_dataframe(
    engine,
    df: pd.DataFrame,
    table_name: str,
    if_exists: str = "replace",
) -> None:
    """
    Persist a pandas DataFrame to a SQLite table.
    Maintains compatibility with existing tests and scripts.
    """
    if df is None or df.empty:
        logger.warning("save_dataframe called with empty DataFrame for '%s'; skipping.", table_name)
        return

    try:
        df_to_save = df.copy()
        df_to_save["upload_timestamp"] = datetime.now(timezone.utc).isoformat()

        df_to_save.to_sql(
            name=table_name,
            con=engine,
            if_exists=if_exists,
            index=False,
        )
        logger.info("Saved %d rows to table '%s'.", len(df_to_save), table_name)
    except Exception as exc:
        logger.error("Failed to save DataFrame to '%s': %s", table_name, exc)
        raise


def load_dataframe(engine, table_name: str) -> Optional[pd.DataFrame]:
    """
    Load an entire SQLite table as a pandas DataFrame.
    Returns None if the table does not exist or is empty.
    """
    try:
        insp = inspect(engine)
        if table_name not in insp.get_table_names():
            logger.warning("Table '%s' does not exist in the database.", table_name)
            return None

        df = pd.read_sql_table(table_name, con=engine)
        if df.empty:
            logger.info("Table '%s' exists but contains no rows.", table_name)
            return None

        return df
    except Exception as exc:
        logger.error("Failed to load table '%s': %s", table_name, exc)
        return None


def save_column_mapping(engine, dataset_name: str, mapping_dict: dict) -> None:
    """
    Persist a column-name mapping for a given dataset in legacy column_mappings
    and mappings tables.
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        with engine.connect() as conn:
            conn.execute(
                text("DELETE FROM column_mappings WHERE dataset_name = :ds"),
                {"ds": dataset_name},
            )
            conn.execute(
                text("DELETE FROM mappings WHERE dataset = :ds"),
                {"ds": dataset_name},
            )

            for standard_field, uploaded_column in mapping_dict.items():
                conn.execute(
                    text(
                        """
                        INSERT INTO column_mappings
                            (dataset_name, standard_field, uploaded_column, created_at)
                        VALUES
                            (:ds, :sf, :uc, :ca)
                        """
                    ),
                    {"ds": dataset_name, "sf": standard_field, "uc": uploaded_column, "ca": now},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO mappings
                            (dataset, standard_field, source_column, created_at, updated_at)
                        VALUES
                            (:ds, :sf, :uc, :ca, :ca)
                        """
                    ),
                    {"ds": dataset_name, "sf": standard_field, "uc": uploaded_column, "ca": now},
                )
            conn.commit()
        logger.info("Saved %d column mappings for dataset '%s'.", len(mapping_dict), dataset_name)
    except Exception as exc:
        logger.error("Failed to save column mapping for '%s': %s", dataset_name, exc)
        raise


def load_column_mapping(engine, dataset_name: str) -> dict:
    """
    Retrieve the saved column mapping for a given dataset.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT standard_field, uploaded_column
                    FROM   column_mappings
                    WHERE  dataset_name = :ds
                    """
                ),
                {"ds": dataset_name},
            )
            rows = result.fetchall()

        mapping = {row[0]: row[1] for row in rows}
        return mapping
    except Exception as exc:
        logger.error("Failed to load column mapping for '%s': %s", dataset_name, exc)
        return {}


def get_table_info(engine) -> dict:
    """
    Return summary statistics for every table in the database.
    """
    result = {}
    try:
        insp = inspect(engine)
        table_names = insp.get_table_names()

        with engine.connect() as conn:
            for tname in table_names:
                info: dict = {"row_count": 0, "last_updated": None}
                try:
                    row = conn.execute(
                        text(f"SELECT COUNT(*) FROM {tname}")  # noqa: S608
                    ).fetchone()
                    info["row_count"] = row[0] if row else 0
                except Exception:
                    info["row_count"] = 0

                try:
                    col_names = [c["name"] for c in insp.get_columns(tname)]
                    if "upload_timestamp" in col_names:
                        row = conn.execute(
                            text(f"SELECT MAX(upload_timestamp) FROM {tname}")  # noqa: S608
                        ).fetchone()
                        info["last_updated"] = row[0] if row else None
                    elif "created_at" in col_names:
                        row = conn.execute(
                            text(f"SELECT MAX(created_at) FROM {tname}")  # noqa: S608
                        ).fetchone()
                        info["last_updated"] = row[0] if row else None
                    elif "uploaded_at" in col_names:
                        row = conn.execute(
                            text(f"SELECT MAX(uploaded_at) FROM {tname}")  # noqa: S608
                        ).fetchone()
                        info["last_updated"] = row[0] if row else None
                except Exception:
                    pass

                result[tname] = info

    except Exception as exc:
        logger.error("Failed to retrieve table info: %s", exc)

    return result
