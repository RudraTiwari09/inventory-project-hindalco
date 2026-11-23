"""
Unit tests for Milestone 1: Data layer, Users & Auth, Audit log, and Settings.
"""

import os
import pytest
from sqlalchemy import text
from core.database import get_engine, initialize_database, get_table_info
from core.auth import (
    hash_password,
    verify_password,
    create_user,
    authenticate_user,
    check_session_timeout,
    is_admin,
    require_admin,
)
from core.audit import log_audit_event, get_audit_trail
from core.settings import (
    initialize_settings,
    get_setting,
    get_setting_float,
    set_setting,
    get_all_settings,
)


@pytest.fixture
def test_engine(tmp_path):
    db_file = tmp_path / "test_m1.db"
    engine = get_engine(str(db_file))
    initialize_database(engine)
    initialize_settings(engine)
    return engine


def test_schema_initialization(test_engine):
    info = get_table_info(test_engine)
    expected_tables = [
        "schema_version",
        "users",
        "uploads",
        "inventory_items",
        "inventory_diff",
        "ptw_permits",
        "ptw_lines",
        "ptw_permit_history",
        "item_aliases",
        "user_confirmed_matches",
        "gap_review",
        "rejected_rows",
        "settings",
        "audit_log",
        "mappings",
    ]
    for table in expected_tables:
        assert table in info, f"Missing table: {table}"


def test_password_hashing():
    pw = "SecretPlantPass123!"
    h = hash_password(pw)
    assert h != pw
    assert verify_password(pw, h) is True
    assert verify_password("WrongPassword", h) is False


def test_user_creation_and_auth(test_engine):
    ok, msg = create_user(test_engine, "john", "pass123", "John Doe", role="admin")
    assert ok is True

    # Duplicate check
    ok_dup, msg_dup = create_user(test_engine, "john", "pass123", "John Doe")
    assert ok_dup is False
    assert "already exists" in msg_dup

    # Valid auth
    success, msg, user = authenticate_user(test_engine, "john", "pass123")
    assert success is True
    assert user["username"] == "john"
    assert user["role"] == "admin"
    assert is_admin(user) is True

    # Invalid password
    bad_success, bad_msg, bad_user = authenticate_user(test_engine, "john", "wrongpass")
    assert bad_success is False
    assert bad_user is None
    assert bad_msg == "Invalid username or password."


def test_account_lockout_after_5_attempts(test_engine):
    create_user(test_engine, "lockme", "correctpass", "Lock Target", role="viewer")

    # 4 bad attempts
    for i in range(4):
        ok, msg, _ = authenticate_user(test_engine, "lockme", f"wrong{i}")
        assert ok is False
        assert "Invalid username or password." in msg

    # 5th bad attempt triggers lockout
    ok5, msg5, _ = authenticate_user(test_engine, "lockme", "wrong5")
    assert ok5 is False
    assert "Account locked" in msg5

    # Even with correct password, still locked
    ok_lock, msg_lock, _ = authenticate_user(test_engine, "lockme", "correctpass")
    assert ok_lock is False
    assert "Account locked" in msg_lock


def test_session_timeout():
    from datetime import datetime, timezone, timedelta
    recent = datetime.now(timezone.utc).isoformat()
    assert check_session_timeout(recent, timeout_minutes=30) is False

    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    assert check_session_timeout(old, timeout_minutes=30) is True
    assert check_session_timeout(None) is True


def test_viewer_permission_guard():
    admin_user = {"username": "adm", "role": "admin"}
    viewer_user = {"username": "viw", "role": "viewer"}

    assert is_admin(admin_user) is True
    assert is_admin(viewer_user) is False

    require_admin(admin_user)  # Should not raise
    with pytest.raises(PermissionError):
        require_admin(viewer_user)


def test_audit_logging(test_engine):
    log_audit_event(test_engine, "test_event", "Some test details", username="tester")
    trail = get_audit_trail(test_engine, limit=10)
    assert len(trail) >= 1
    assert trail[0]["event_type"] == "test_event"
    assert trail[0]["username"] == "tester"


def test_settings_get_set(test_engine):
    assert get_setting_float(test_engine, "overstock_months_threshold") == 6.0
    set_setting(test_engine, "overstock_months_threshold", "8.5", updated_by="admin")
    assert get_setting_float(test_engine, "overstock_months_threshold") == 8.5

    all_s = get_all_settings(test_engine)
    assert "overstock_months_threshold" in all_s
    assert all_s["overstock_months_threshold"]["value"] == "8.5"

