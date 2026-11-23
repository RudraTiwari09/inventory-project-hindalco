"""
Unit tests for Milestone 3: Pipeline staging, atomic commit, backups, replace, and void.
"""

import os
from datetime import date
import pandas as pd
import pytest
from sqlalchemy import text

from core.database import get_engine, initialize_database
from core.settings import initialize_settings
from core.pipeline import (
    stage_inventory_upload,
    commit_and_publish,
    discard_staged_upload,
    void_latest_upload,
    backup_database,
    archive_raw_file,
)


@pytest.fixture
def pipe_db(tmp_path):
    db_path = tmp_path / "test_pipeline.db"
    engine = get_engine(str(db_path))
    initialize_database(engine)
    initialize_settings(engine)
    return engine


def test_stage_and_discard_inventory(pipe_db):
    df = pd.DataFrame({
        "material_code": ["00101", "00102"],
        "spare_description": ["Bearing A", "Seal B"],
        "quantity_in_stock": [10.0, 5.0],
        "unit_price": [150.0, 50.0],
    })
    raw_bytes = b"material_code,spare_description,quantity_in_stock,unit_price\n00101,Bearing A,10,150"

    uid = stage_inventory_upload(
        pipe_db,
        period="Oct 2026",
        as_of_date=date(2026, 10, 31),
        filename="inv_oct.csv",
        file_bytes=raw_bytes,
        file_hash="hash_oct_1",
        cleaned_df=df,
        rejected_rows=[],
        user="admin",
    )

    with pipe_db.connect() as conn:
        row = conn.execute(text("SELECT status FROM uploads WHERE id = :uid"), {"uid": uid}).fetchone()
        assert row[0] == "staged"
        item_count = conn.execute(text("SELECT COUNT(*) FROM inventory_items WHERE upload_id = :uid"), {"uid": uid}).fetchone()[0]
        assert item_count == 2

    # Discard
    discard_staged_upload(pipe_db, uid, user="admin")
    with pipe_db.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM uploads WHERE id = :uid"), {"uid": uid}).fetchone()[0] == 0
        assert conn.execute(text("SELECT COUNT(*) FROM inventory_items WHERE upload_id = :uid"), {"uid": uid}).fetchone()[0] == 0


def test_atomic_publish_and_backup(pipe_db):
    df = pd.DataFrame({
        "material_code": ["00101"],
        "spare_description": ["Bearing A"],
        "quantity_in_stock": [10.0],
        "unit_price": [100.0],
    })
    raw_bytes = b"col1,col2\n00101,10"

    uid = stage_inventory_upload(
        pipe_db,
        period="Oct 2026",
        as_of_date=date(2026, 10, 31),
        filename="inv_oct.csv",
        file_bytes=raw_bytes,
        file_hash="hash_oct_pub",
        cleaned_df=df,
        rejected_rows=[],
        user="admin",
    )

    ok, msg = commit_and_publish(pipe_db, uid, user="admin")
    assert ok is True
    assert "Successfully published" in msg

    with pipe_db.connect() as conn:
        row = conn.execute(text("SELECT status, version FROM uploads WHERE id = :uid"), {"uid": uid}).fetchone()
        assert row[0] == "active"
        assert row[1] == 1


def test_replace_and_void(pipe_db):
    # Upload v1
    df1 = pd.DataFrame({"material_code": ["00101"], "quantity_in_stock": [10.0]})
    uid1 = stage_inventory_upload(
        pipe_db, "Oct 2026", date(2026, 10, 31), "inv_v1.csv", b"raw1", "hash_v1", df1, [], "admin"
    )
    commit_and_publish(pipe_db, uid1, "admin")

    # Upload v2 for same period with Replace
    df2 = pd.DataFrame({"material_code": ["00101"], "quantity_in_stock": [15.0]})
    uid2 = stage_inventory_upload(
        pipe_db, "Oct 2026", date(2026, 10, 31), "inv_v2.csv", b"raw2", "hash_v2", df2, [], "admin"
    )
    commit_and_publish(pipe_db, uid2, "admin", replace_existing=True)

    with pipe_db.connect() as conn:
        v1_stat = conn.execute(text("SELECT status, version FROM uploads WHERE id = :uid"), {"uid": uid1}).fetchone()
        v2_stat = conn.execute(text("SELECT status, version FROM uploads WHERE id = :uid"), {"uid": uid2}).fetchone()
        assert v1_stat[0] == "superseded"
        assert v2_stat[0] == "active"
        assert v2_stat[1] == 2

    # Void v2 -> v1 should become active again
    ok_void, msg_void = void_latest_upload(pipe_db, uid2, "admin")
    assert ok_void is True

    with pipe_db.connect() as conn:
        v1_stat_after = conn.execute(text("SELECT status FROM uploads WHERE id = :uid"), {"uid": uid1}).fetchone()
        v2_stat_after = conn.execute(text("SELECT status FROM uploads WHERE id = :uid"), {"uid": uid2}).fetchone()
        assert v2_stat_after[0] == "voided"
        assert v1_stat_after[0] == "active"

