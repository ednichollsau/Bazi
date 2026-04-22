"""
database.py — PostgreSQL storage layer for Ed Nicholls Acupuncture.

Tables
------
patients          : core patient records
appointment_types : configurable appointment types (Initial, Follow-up)
availability      : weekly recurring schedule
blocked_times     : one-off blocks (holidays, personal time)
appointments      : booked sessions
treatment_notes   : SOAP notes per appointment
submissions       : reading + protocol records (linked to patients)

All functions are safe to call when DATABASE_URL is unset (they log a warning
and return empty/None rather than raising).
"""

import json
import logging
import os
from datetime import date

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

_RAW_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = _RAW_URL.replace("postgres://", "postgresql://", 1)


# ── Connection ─────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


# ── Schema initialisation ──────────────────────────────────────────────────────

def init_db():
    """Create/migrate all tables. Called once on server startup."""
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set — database features disabled.")
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:

                # ── Patients ───────────────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS patients (
                        id          SERIAL PRIMARY KEY,
                        name        TEXT NOT NULL,
                        email       TEXT,
                        phone       TEXT,
                        year        INTEGER,
                        month       INTEGER,
                        day         INTEGER,
                        handedness  TEXT NOT NULL DEFAULT 'right',
                        notes       TEXT NOT NULL DEFAULT '',
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)

                # ── Appointment types ──────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS appointment_types (
                        id          SERIAL PRIMARY KEY,
                        name        TEXT NOT NULL UNIQUE,
                        duration    INTEGER NOT NULL DEFAULT 45,
                        description TEXT,
                        active      BOOLEAN NOT NULL DEFAULT TRUE
                    )
                """)

                # Seed default types if table is empty
                cur.execute("SELECT COUNT(*) FROM appointment_types")
                if cur.fetchone()[0] == 0:
                    cur.execute("""
                        INSERT INTO appointment_types (name, duration, description)
                        VALUES
                            ('Initial Consultation', 45, 'First appointment — full assessment and treatment'),
                            ('Follow-up', 45, 'Ongoing treatment session')
                    """)

                # ── Weekly availability ────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS availability (
                        id          SERIAL PRIMARY KEY,
                        day_of_week INTEGER NOT NULL,
                        start_time  TIME NOT NULL,
                        end_time    TIME NOT NULL
                    )
                """)

                # ── Blocked times ──────────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS blocked_times (
                        id          SERIAL PRIMARY KEY,
                        start_dt    TIMESTAMPTZ NOT NULL,
                        end_dt      TIMESTAMPTZ NOT NULL,
                        reason      TEXT,
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)

                # ── Appointments ───────────────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS appointments (
                        id                   SERIAL PRIMARY KEY,
                        patient_id           INTEGER REFERENCES patients(id),
                        appointment_type_id  INTEGER REFERENCES appointment_types(id),
                        start_dt             TIMESTAMPTZ NOT NULL,
                        end_dt               TIMESTAMPTZ NOT NULL,
                        status               TEXT NOT NULL DEFAULT 'confirmed',
                        notes                TEXT NOT NULL DEFAULT '',
                        created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)

                # ── Treatment notes (SOAP) ─────────────────────────────────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS treatment_notes (
                        id              SERIAL PRIMARY KEY,
                        appointment_id  INTEGER REFERENCES appointments(id),
                        patient_id      INTEGER REFERENCES patients(id),
                        subjective      TEXT NOT NULL DEFAULT '',
                        objective       TEXT NOT NULL DEFAULT '',
                        assessment      TEXT NOT NULL DEFAULT '',
                        plan            TEXT NOT NULL DEFAULT '',
                        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)

                # ── Submissions (existing — add patient_id if missing) ─────
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS submissions (
                        id           SERIAL PRIMARY KEY,
                        patient_id   INTEGER REFERENCES patients(id),
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

                # Migrate: add patient_id column to submissions if it doesn't exist
                cur.execute("""
                    ALTER TABLE submissions
                    ADD COLUMN IF NOT EXISTS patient_id INTEGER REFERENCES patients(id)
                """)

            conn.commit()
        logger.info("Database initialised.")
    except Exception as e:
        logger.error("Database init error: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# PATIENTS
# ══════════════════════════════════════════════════════════════════════════════

def create_patient(record: dict) -> int | None:
    """Insert a new patient. Returns new id or None on failure."""
    if not DATABASE_URL:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO patients (name, email, phone, year, month, day, handedness, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    record.get("name", ""),
                    record.get("email", ""),
                    record.get("phone", ""),
                    record.get("year"),
                    record.get("month"),
                    record.get("day"),
                    record.get("handedness", "right"),
                    record.get("notes", ""),
                ))
                row_id = cur.fetchone()[0]
            conn.commit()
        return row_id
    except Exception as e:
        logger.error("create_patient error: %s", e)
        return None


def get_patient(patient_id: int) -> dict | None:
    if not DATABASE_URL:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
                row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("get_patient(%s) error: %s", patient_id, e)
        return None


def get_patient_by_email(email: str) -> dict | None:
    if not DATABASE_URL:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM patients WHERE LOWER(email) = LOWER(%s) LIMIT 1",
                    (email,)
                )
                row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("get_patient_by_email error: %s", e)
        return None


def find_or_create_patient(name: str, email: str, year: int, month: int,
                            day: int, handedness: str = "right") -> int | None:
    """Return existing patient id matched by email, or create a new record."""
    existing = get_patient_by_email(email)
    if existing:
        return existing["id"]
    return create_patient({
        "name": name, "email": email,
        "year": year, "month": month, "day": day,
        "handedness": handedness,
    })


def list_patients(limit: int = 500) -> list[dict]:
    if not DATABASE_URL:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT p.*,
                           COUNT(DISTINCT a.id)  AS appointment_count,
                           MAX(a.start_dt)       AS last_appointment
                    FROM   patients p
                    LEFT JOIN appointments a ON a.patient_id = p.id
                    GROUP BY p.id
                    ORDER BY p.name
                    LIMIT %s
                """, (limit,))
                rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("last_appointment"):
                d["last_appointment"] = d["last_appointment"].isoformat()
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            result.append(d)
        return result
    except Exception as e:
        logger.error("list_patients error: %s", e)
        return []


def update_patient(patient_id: int, record: dict) -> bool:
    if not DATABASE_URL:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE patients
                    SET name       = %s,
                        email      = %s,
                        phone      = %s,
                        year       = %s,
                        month      = %s,
                        day        = %s,
                        handedness = %s,
                        notes      = %s
                    WHERE id = %s
                """, (
                    record.get("name"),
                    record.get("email"),
                    record.get("phone"),
                    record.get("year"),
                    record.get("month"),
                    record.get("day"),
                    record.get("handedness", "right"),
                    record.get("notes", ""),
                    patient_id,
                ))
            conn.commit()
        return True
    except Exception as e:
        logger.error("update_patient(%s) error: %s", patient_id, e)
        return False


def update_patient_notes(patient_id: int, notes: str) -> bool:
    if not DATABASE_URL:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE patients SET notes = %s WHERE id = %s",
                    (notes, patient_id)
                )
            conn.commit()
        return True
    except Exception as e:
        logger.error("update_patient_notes(%s) error: %s", patient_id, e)
        return False


def get_patient_history(patient_id: int) -> dict:
    """Return a patient's full history: appointments + submissions."""
    if not DATABASE_URL:
        return {}
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT a.*, t.name AS appointment_type,
                           n.id AS note_id
                    FROM   appointments a
                    LEFT JOIN appointment_types t ON t.id = a.appointment_type_id
                    LEFT JOIN treatment_notes n   ON n.appointment_id = a.id
                    WHERE  a.patient_id = %s
                    ORDER  BY a.start_dt DESC
                """, (patient_id,))
                appointments = [dict(r) for r in cur.fetchall()]

                cur.execute("""
                    SELECT id, principle, day_master, deficient, excess,
                           reading_text, protocol, created_at
                    FROM   submissions
                    WHERE  patient_id = %s
                    ORDER  BY created_at DESC
                """, (patient_id,))
                submissions = [dict(r) for r in cur.fetchall()]

        for a in appointments:
            if a.get("start_dt"):
                a["start_dt"] = a["start_dt"].isoformat()
            if a.get("end_dt"):
                a["end_dt"] = a["end_dt"].isoformat()
            if a.get("created_at"):
                a["created_at"] = a["created_at"].isoformat()
        for s in submissions:
            if s.get("created_at"):
                s["created_at"] = s["created_at"].isoformat()

        return {"appointments": appointments, "submissions": submissions}
    except Exception as e:
        logger.error("get_patient_history(%s) error: %s", patient_id, e)
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# APPOINTMENT TYPES
# ══════════════════════════════════════════════════════════════════════════════

def list_appointment_types() -> list[dict]:
    if not DATABASE_URL:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM appointment_types WHERE active = TRUE ORDER BY id"
                )
                rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("list_appointment_types error: %s", e)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# AVAILABILITY
# ══════════════════════════════════════════════════════════════════════════════

def list_availability() -> list[dict]:
    if not DATABASE_URL:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM availability ORDER BY day_of_week, start_time")
                rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("start_time"):
                d["start_time"] = str(d["start_time"])
            if d.get("end_time"):
                d["end_time"] = str(d["end_time"])
            result.append(d)
        return result
    except Exception as e:
        logger.error("list_availability error: %s", e)
        return []


def set_availability(slots: list[dict]) -> bool:
    """Replace entire availability schedule with new slots."""
    if not DATABASE_URL:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM availability")
                for slot in slots:
                    cur.execute("""
                        INSERT INTO availability (day_of_week, start_time, end_time)
                        VALUES (%s, %s, %s)
                    """, (slot["day_of_week"], slot["start_time"], slot["end_time"]))
            conn.commit()
        return True
    except Exception as e:
        logger.error("set_availability error: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# BLOCKED TIMES
# ══════════════════════════════════════════════════════════════════════════════

def list_blocked_times() -> list[dict]:
    if not DATABASE_URL:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM blocked_times ORDER BY start_dt")
                rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("start_dt"):
                d["start_dt"] = d["start_dt"].isoformat()
            if d.get("end_dt"):
                d["end_dt"] = d["end_dt"].isoformat()
            if d.get("created_at"):
                d["created_at"] = d["created_at"].isoformat()
            result.append(d)
        return result
    except Exception as e:
        logger.error("list_blocked_times error: %s", e)
        return []


def add_blocked_time(record: dict) -> int | None:
    if not DATABASE_URL:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO blocked_times (start_dt, end_dt, reason)
                    VALUES (%s, %s, %s)
                    RETURNING id
                """, (record["start_dt"], record["end_dt"], record.get("reason", "")))
                row_id = cur.fetchone()[0]
            conn.commit()
        return row_id
    except Exception as e:
        logger.error("add_blocked_time error: %s", e)
        return None


def delete_blocked_time(block_id: int) -> bool:
    if not DATABASE_URL:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM blocked_times WHERE id = %s", (block_id,))
            conn.commit()
        return True
    except Exception as e:
        logger.error("delete_blocked_time(%s) error: %s", block_id, e)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# APPOINTMENTS
# ══════════════════════════════════════════════════════════════════════════════

def create_appointment(record: dict) -> int | None:
    if not DATABASE_URL:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO appointments
                        (patient_id, appointment_type_id, start_dt, end_dt, status, notes)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    record["patient_id"],
                    record["appointment_type_id"],
                    record["start_dt"],
                    record["end_dt"],
                    record.get("status", "confirmed"),
                    record.get("notes", ""),
                ))
                row_id = cur.fetchone()[0]
            conn.commit()
        return row_id
    except Exception as e:
        logger.error("create_appointment error: %s", e)
        return None


def get_appointment(appt_id: int) -> dict | None:
    if not DATABASE_URL:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT a.*,
                           p.name  AS patient_name,
                           p.email AS patient_email,
                           p.phone AS patient_phone,
                           t.name  AS appointment_type,
                           t.duration,
                           n.id    AS note_id
                    FROM   appointments a
                    LEFT JOIN patients p          ON p.id = a.patient_id
                    LEFT JOIN appointment_types t ON t.id = a.appointment_type_id
                    LEFT JOIN treatment_notes n   ON n.appointment_id = a.id
                    WHERE  a.id = %s
                """, (appt_id,))
                row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        for f in ("start_dt", "end_dt", "created_at"):
            if d.get(f):
                d[f] = d[f].isoformat()
        return d
    except Exception as e:
        logger.error("get_appointment(%s) error: %s", appt_id, e)
        return None


def list_appointments(date_str: str | None = None) -> list[dict]:
    """
    Return appointments. If date_str ('YYYY-MM-DD') given, filter to that day.
    Otherwise returns upcoming appointments (today onwards, non-cancelled).
    """
    if not DATABASE_URL:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if date_str:
                    cur.execute("""
                        SELECT a.*,
                               p.name  AS patient_name,
                               p.email AS patient_email,
                               t.name  AS appointment_type,
                               n.id    AS note_id
                        FROM   appointments a
                        LEFT JOIN patients p          ON p.id = a.patient_id
                        LEFT JOIN appointment_types t ON t.id = a.appointment_type_id
                        LEFT JOIN treatment_notes n   ON n.appointment_id = a.id
                        WHERE  (a.start_dt AT TIME ZONE 'Europe/London')::date = %s
                        ORDER  BY a.start_dt
                    """, (date_str,))
                else:
                    cur.execute("""
                        SELECT a.*,
                               p.name  AS patient_name,
                               p.email AS patient_email,
                               t.name  AS appointment_type,
                               n.id    AS note_id
                        FROM   appointments a
                        LEFT JOIN patients p          ON p.id = a.patient_id
                        LEFT JOIN appointment_types t ON t.id = a.appointment_type_id
                        LEFT JOIN treatment_notes n   ON n.appointment_id = a.id
                        WHERE  a.start_dt >= CURRENT_DATE
                          AND  a.status NOT IN ('cancelled')
                        ORDER  BY a.start_dt
                        LIMIT  200
                    """)
                rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            for f in ("start_dt", "end_dt", "created_at"):
                if d.get(f):
                    d[f] = d[f].isoformat()
            result.append(d)
        return result
    except Exception as e:
        logger.error("list_appointments error: %s", e)
        return []


def list_today_appointments() -> list[dict]:
    """Return today's appointments in Europe/London time."""
    today = date.today().isoformat()
    return list_appointments(date_str=today)


def update_appointment_status(appt_id: int, status: str) -> bool:
    if not DATABASE_URL:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE appointments SET status = %s WHERE id = %s",
                    (status, appt_id)
                )
            conn.commit()
        return True
    except Exception as e:
        logger.error("update_appointment_status(%s) error: %s", appt_id, e)
        return False


def update_appointment(appt_id: int, record: dict) -> bool:
    if not DATABASE_URL:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE appointments
                    SET patient_id          = %s,
                        appointment_type_id = %s,
                        start_dt            = %s,
                        end_dt              = %s,
                        status              = %s,
                        notes               = %s
                    WHERE id = %s
                """, (
                    record["patient_id"],
                    record["appointment_type_id"],
                    record["start_dt"],
                    record["end_dt"],
                    record.get("status", "confirmed"),
                    record.get("notes", ""),
                    appt_id,
                ))
            conn.commit()
        return True
    except Exception as e:
        logger.error("update_appointment(%s) error: %s", appt_id, e)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# TREATMENT NOTES
# ══════════════════════════════════════════════════════════════════════════════

def get_treatment_note(appointment_id: int) -> dict | None:
    if not DATABASE_URL:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM treatment_notes WHERE appointment_id = %s",
                    (appointment_id,)
                )
                row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        for f in ("created_at", "updated_at"):
            if d.get(f):
                d[f] = d[f].isoformat()
        return d
    except Exception as e:
        logger.error("get_treatment_note(%s) error: %s", appointment_id, e)
        return None


def save_treatment_note(record: dict) -> int | None:
    """Insert or update a SOAP note for an appointment."""
    if not DATABASE_URL:
        return None
    existing = get_treatment_note(record["appointment_id"])
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if existing:
                    cur.execute("""
                        UPDATE treatment_notes
                        SET subjective = %s,
                            objective  = %s,
                            assessment = %s,
                            plan       = %s,
                            updated_at = NOW()
                        WHERE appointment_id = %s
                        RETURNING id
                    """, (
                        record.get("subjective", ""),
                        record.get("objective", ""),
                        record.get("assessment", ""),
                        record.get("plan", ""),
                        record["appointment_id"],
                    ))
                    row_id = cur.fetchone()[0]
                else:
                    cur.execute("""
                        INSERT INTO treatment_notes
                            (appointment_id, patient_id, subjective, objective, assessment, plan)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        record["appointment_id"],
                        record.get("patient_id"),
                        record.get("subjective", ""),
                        record.get("objective", ""),
                        record.get("assessment", ""),
                        record.get("plan", ""),
                    ))
                    row_id = cur.fetchone()[0]
            conn.commit()
        return row_id
    except Exception as e:
        logger.error("save_treatment_note error: %s", e)
        return None


def list_documentation_queue() -> list[dict]:
    """
    Return completed appointments with no treatment note or an empty note.
    """
    if not DATABASE_URL:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT a.*,
                           p.name  AS patient_name,
                           t.name  AS appointment_type,
                           n.id    AS note_id,
                           CASE
                               WHEN n.id IS NULL THEN 'missing'
                               WHEN n.subjective = '' AND n.objective = ''
                                    AND n.assessment = '' AND n.plan = '' THEN 'empty'
                               ELSE 'draft'
                           END AS note_status
                    FROM   appointments a
                    LEFT JOIN patients p          ON p.id = a.patient_id
                    LEFT JOIN appointment_types t ON t.id = a.appointment_type_id
                    LEFT JOIN treatment_notes n   ON n.appointment_id = a.id
                    WHERE  a.status = 'completed'
                      AND  (n.id IS NULL
                            OR (n.subjective = '' AND n.objective = ''
                                AND n.assessment = '' AND n.plan = ''))
                    ORDER  BY a.start_dt DESC
                    LIMIT  50
                """)
                rows = cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            for f in ("start_dt", "end_dt", "created_at"):
                if d.get(f):
                    d[f] = d[f].isoformat()
            result.append(d)
        return result
    except Exception as e:
        logger.error("list_documentation_queue error: %s", e)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# SUBMISSIONS (preserved + extended with patient_id)
# ══════════════════════════════════════════════════════════════════════════════

def save_submission(record: dict) -> int | None:
    if not DATABASE_URL:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO submissions
                        (patient_id, name, email, year, month, day, hour, handedness,
                         constitution, pillars, principle, day_master,
                         deficient, excess, reading_text, protocol)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s,
                         %s, %s, %s, %s,
                         %s, %s, %s, %s)
                    RETURNING id
                """, (
                    record.get("patient_id"),
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


def list_submissions(limit: int = 500) -> list[dict]:
    if not DATABASE_URL:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, patient_id, name, email, year, month, day, hour,
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
    if not DATABASE_URL:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM submissions WHERE id = %s", (sub_id,))
                row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("get_submission(%s) error: %s", sub_id, e)
        return None


def update_notes(sub_id: int, notes: str) -> bool:
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


# ══════════════════════════════════════════════════════════════════════════════
# DUPLICATE CHECKS (preserved)
# ══════════════════════════════════════════════════════════════════════════════

def email_exists(email: str) -> bool:
    if not DATABASE_URL:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM submissions WHERE LOWER(email) = LOWER(%s) LIMIT 1",
                    (email,)
                )
                return cur.fetchone() is not None
    except Exception as e:
        logger.error("email_exists error: %s", e)
        return False


def submission_exists(email: str, year: int, month: int, day: int) -> bool:
    if not DATABASE_URL:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT 1 FROM submissions
                       WHERE LOWER(email) = LOWER(%s)
                         AND year = %s AND month = %s AND day = %s
                       LIMIT 1""",
                    (email, year, month, day)
                )
                return cur.fetchone() is not None
    except Exception as e:
        logger.error("submission_exists error: %s", e)
        return False
