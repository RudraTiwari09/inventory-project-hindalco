"""
Audit logging module.
Tracks all authentication, upload, publish, void, and configuration events.
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import text

logger = logging.getLogger(__name__)


def log_audit_event(
    engine,
    event_type: str,
    details: str,
    username: str = "system",
    ip_address: Optional[str] = "127.0.0.1",
) -> None:
    """
    Record an immutable event in the audit_log table.

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
    event_type : str
        e.g. 'login', 'failed_login', 'logout', 'upload_staged', 'upload_published',
             'upload_voided', 'match_confirmed', 'user_created', 'setting_changed'
    details : str
        Human-readable details or JSON payload
    username : str
    ip_address : str, optional
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO audit_log (event_type, details, username, ip_address, timestamp)
                    VALUES (:event_type, :details, :username, :ip_address, :timestamp)
                    """
                ),
                {
                    "event_type": event_type,
                    "details": details,
                    "username": username,
                    "ip_address": ip_address or "127.0.0.1",
                    "timestamp": now,
                },
            )
            conn.commit()
    except Exception as exc:
        logger.error("Failed to write to audit log: %s", exc)


def get_audit_trail(engine, limit: int = 100) -> list:
    """
    Fetch the most recent audit log entries.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT id, event_type, details, username, ip_address, timestamp
                    FROM audit_log
                    ORDER BY id DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
            rows = result.fetchall()
            return [
                {
                    "id": r[0],
                    "event_type": r[1],
                    "details": r[2],
                    "username": r[3],
                    "ip_address": r[4],
                    "timestamp": r[5],
                }
                for r in rows
            ]
    except Exception as exc:
        logger.error("Failed to load audit trail: %s", exc)
        return []

