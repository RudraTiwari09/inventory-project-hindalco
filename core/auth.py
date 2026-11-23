"""
Authentication and authorization module.
Handles bcrypt password hashing, account lockout (5 attempts / 5 mins),
session timeout management, and role-based permissions (admin / viewer).
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any

import bcrypt
from sqlalchemy import text

from core.audit import log_audit_event

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 5
SESSION_TIMEOUT_MINUTES = 30


def hash_password(plain_password: str) -> str:
    """
    Hash a plain text password using bcrypt with a secure salt.
    """
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against the stored bcrypt hash.
    """
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception as exc:
        logger.error("Password verification error: %s", exc)
        return False


def create_user(
    engine,
    username: str,
    password: str,
    full_name: str,
    role: str = "viewer",
    created_by: str = "system",
) -> Tuple[bool, str]:
    """
    Create a new user with bcrypt-hashed credentials.
    Roles: 'admin' or 'viewer'.
    """
    username = username.strip().lower()
    if not username:
        return False, "Username cannot be empty."
    if not password or len(password) < 4:
        return False, "Password must be at least 4 characters."
    if role not in ("admin", "viewer"):
        return False, "Role must be 'admin' or 'viewer'."

    pw_hash = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()

    try:
        with engine.connect() as conn:
            # Check existing
            existing = conn.execute(
                text("SELECT id FROM users WHERE username = :u"),
                {"u": username},
            ).fetchone()
            if existing:
                return False, f"User '{username}' already exists."

            conn.execute(
                text(
                    """
                    INSERT INTO users (username, password_hash, full_name, role, failed_attempts, locked_until, created_at, updated_at)
                    VALUES (:u, :p, :fn, :role, 0, NULL, :now, :now)
                    """
                ),
                {
                    "u": username,
                    "p": pw_hash,
                    "fn": full_name.strip() or username,
                    "role": role,
                    "now": now,
                },
            )
            conn.commit()

        log_audit_event(
            engine,
            event_type="user_created",
            details=f"User '{username}' created with role '{role}'",
            username=created_by,
        )
        return True, f"User '{username}' created successfully."
    except Exception as exc:
        logger.error("Failed to create user: %s", exc)
        return False, str(exc)


def authenticate_user(
    engine,
    username: str,
    password: str,
    ip_address: str = "127.0.0.1",
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Authenticate a user by username and password.
    Enforces 5-attempt lockout for 5 minutes.
    Returns: (success: bool, message: str, user_dict: dict | None)
    """
    username = username.strip().lower()
    now_utc = datetime.now(timezone.utc)

    if not username or not password:
        return False, "Invalid username or password.", None

    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, username, password_hash, full_name, role, failed_attempts, locked_until
                    FROM users
                    WHERE username = :u
                    """
                ),
                {"u": username},
            ).fetchone()

            if not row:
                # Do dummy bcrypt check to prevent timing attacks
                bcrypt.checkpw(b"dummy_password", b"$2b$12$KIXeN3f82E9b0m91H4uJauqR8VqH1z8kO8R1c5P8z0m91H4uJauqR")
                log_audit_event(
                    engine,
                    event_type="failed_login",
                    details=f"Login attempt for non-existent user '{username}'",
                    username=username,
                    ip_address=ip_address,
                )
                return False, "Invalid username or password.", None

            user_id, uname, pw_hash, full_name, role, failed_attempts, locked_until = row

            # Check lockout
            if locked_until:
                try:
                    lock_time = datetime.fromisoformat(locked_until)
                    if lock_time.tzinfo is None:
                        lock_time = lock_time.replace(tzinfo=timezone.utc)
                    if now_utc < lock_time:
                        minutes_left = max(1, int((lock_time - now_utc).total_seconds() / 60))
                        return (
                            False,
                            f"Account locked due to multiple failed attempts. Try again in {minutes_left} min.",
                            None,
                        )
                except Exception:
                    pass

            # Verify password
            if verify_password(password, pw_hash):
                # Reset failed attempts and clear lockout
                conn.execute(
                    text(
                        """
                        UPDATE users
                        SET failed_attempts = 0, locked_until = NULL, updated_at = :now
                        WHERE id = :uid
                        """
                    ),
                    {"now": now_utc.isoformat(), "uid": user_id},
                )
                conn.commit()

                log_audit_event(
                    engine,
                    event_type="login",
                    details=f"Successful login for '{uname}'",
                    username=uname,
                    ip_address=ip_address,
                )

                user_dict = {
                    "id": user_id,
                    "username": uname,
                    "full_name": full_name,
                    "role": role,
                    "authenticated_at": now_utc.isoformat(),
                }
                return True, "Login successful", user_dict

            # Invalid password
            new_failed = (failed_attempts or 0) + 1
            new_lock = None
            if new_failed >= MAX_FAILED_ATTEMPTS:
                new_lock = (now_utc + timedelta(minutes=LOCKOUT_DURATION_MINUTES)).isoformat()

            conn.execute(
                text(
                    """
                    UPDATE users
                    SET failed_attempts = :fa, locked_until = :lu, updated_at = :now
                    WHERE id = :uid
                    """
                ),
                {"fa": new_failed, "lu": new_lock, "now": now_utc.isoformat(), "uid": user_id},
            )
            conn.commit()

            log_audit_event(
                engine,
                event_type="failed_login",
                details=f"Failed login attempt ({new_failed}/{MAX_FAILED_ATTEMPTS}) for '{uname}'",
                username=uname,
                ip_address=ip_address,
            )

            if new_lock:
                return (
                    False,
                    f"Account locked due to multiple failed attempts. Try again in {LOCKOUT_DURATION_MINUTES} min.",
                    None,
                )

            return False, "Invalid username or password.", None

    except Exception as exc:
        logger.error("Authentication system error: %s", exc)
        return False, "Authentication service unavailable.", None


def check_session_timeout(last_activity_iso: Optional[str], timeout_minutes: int = SESSION_TIMEOUT_MINUTES) -> bool:
    """
    Returns True if session has timed out due to inactivity, False otherwise.
    """
    if not last_activity_iso:
        return True
    try:
        last_dt = datetime.fromisoformat(last_activity_iso)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        return (now_utc - last_dt) > timedelta(minutes=timeout_minutes)
    except Exception:
        return True


def is_admin(user: Optional[Dict[str, Any]]) -> bool:
    """
    Check if a user dictionary represents an active admin user.
    """
    return bool(user and user.get("role") == "admin")


def require_admin(user: Optional[Dict[str, Any]]) -> None:
    """
    Server-side permission guard. Raises PermissionError if not admin.
    """
    if not is_admin(user):
        raise PermissionError("Admin privileges required for this operation.")

