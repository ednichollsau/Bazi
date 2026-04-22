"""
database.py — PostgreSQL storage layer for the Ear Seed Protocol app.

Tables
------
submissions  : one row per /generate call — patient details, constitution,
               reading text, protocol JSON, practitioner notes.

All functions are safe to call when DATABASE_URL is unset (they log a warning
and return empty/None rather than raising).
"""

import json
import logging
import os

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# Railway provides DATABASE_URL with a postgres:// scheme; psycopg2 needs postgresql://
_RAW_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = _RAW_URL.replace("postgres://", "postgresql://", 1)


# ── Connection ─────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


# ── Schema initialisation ──────────────────────────────────────────────────────

def init_db():
    """Create tables if they don't exist. Called once on server startup."""
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set — database features disabled.")
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS submissions (
                        id           SERIAL PRIMARY KEY,
                        name         TEXT,
                        email        TEXT,
                        year         INTEGER,
                        month        INTEGER,
                        day          INTEGER,
                        hour         INTEGER,
                        handedness   TEXT,
                        constitution JSONB,
                        pillars      JSONB,
                        principle    TEXT,
                        day_master   TEXT,
                        deficient    TEXT,
                        excess       TEXT,
                        reading_text TEXT,
                        protocol     JSONB,
                        notes        TEXT NOT NULL DEFAULT '',
                        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
            conn.commit()
        logger.info("Database initialised.")
    except Exception as e:
        logger.error("Database init error: %s", e)


# ── Write ──────────────────────────────────────────────────────────────────────

def save_submission(record: dict) -> int | None:
    """
    Insert one submission. Returns the new row id, or None on failure.

    Expected keys in record:
        name, email, year, month, day, hour, handedness,
        constitution (dict), pillars (dict), principle (str),
        day_master (str), deficient (list[str]), excess (list[str]),
        reading_text (str), protocol (dict)
    """
    if not DATABASE_URL:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO submissions
                        (name, email, year, month, day, hour, handedness,
                         constitution, pillars, principle, day_master,
                         deficient, excess, reading_text, protocol)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s,
                         %s, %s, %s, %s,
                         %s, %s, %s, %s)
                    RETURNING id
                """, (
                    record.get("name", ""),
                    record.get("email", ""),
                    record["year"], record["month"], record["day"],
                    record.get("hour"),
                    record.get("handedness", "right"),
                    json.dumps(record["constitution"]),
                    json.dumps(record["pillars"]),
                    record.get("principle", ""),
                    record.get("day_master", ""),
                    ", ".join(record.get("deficient", [])),
                    ", ".join(record.get("excess", [])),
                    record.get("reading_text", ""),
                    json.dumps(record.get("protocol", {})),
                ))
                row_id = cur.fetchone()[0]
            conn.commit()
        logger.info("Saved submission id=%s for %s", row_id, record.get("name"))
        return row_id
    except Exception as e:
        logger.error("save_submission error: %s", e)
        return None


# ── Read ───────────────────────────────────────────────────────────────────────

def list_submissions(limit: int = 500) -> list[dict]:
    """Return recent submissions (summary fields only) for the dashboard list."""
    if not DATABASE_URL:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, email, year, month, day, hour,
                           handedness, principle, day_master,
                           deficient, excess, notes,
                           created_at AT TIME ZONE 'UTC' AS created_at
                    FROM   submissions
                    ORDER  BY created_at DESC
                    LIMIT  %s
                """, (limit,))
                rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("list_submissions error: %s", e)
        return []


def get_submission(sub_id: int) -> dict | None:
    """Return the full submission record including reading text and protocol."""
    if not DATABASE_URL:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM submissions WHERE id = %s", (sub_id,)
                )
                row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("get_submission(%s) error: %s", sub_id, e)
        return None


# ── Update ─────────────────────────────────────────────────────────────────────

def update_notes(sub_id: int, notes: str) -> bool:
    """Overwrite the practitioner notes for a submission."""
    if not DATABASE_URL:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE submissions SET notes = %s WHERE id = %s",
                    (notes, sub_id)
                )
            conn.commit()
        return True
    except Exception as e:
        logger.error("update_notes(%s) error: %s", sub_id, e)
        return False
