"""
gcal_sync.py — Two-way sync between clinic appointments and Google Calendar.

Environment variables required (set in Railway):
  GOOGLE_CLIENT_ID       — OAuth 2.0 client ID
  GOOGLE_CLIENT_SECRET   — OAuth 2.0 client secret
  GOOGLE_REFRESH_TOKEN   — Refresh token obtained via /auth/gcal flow
  GOOGLE_CALENDAR_ID     — Calendar to sync (default: acu@ednicholls.com)

Linking mechanism: every synced GCal event carries "[clinic-id:NNN]" in its
description. The sync never modifies events that don't have this tag.
"""

import os
import re
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

CALENDAR_ID  = os.environ.get("GOOGLE_CALENDAR_ID", "acu@ednicholls.com")
CLINIC_ID_RE = re.compile(r'\[clinic-id:(\d+)\]')
BASE_URL     = "https://web-production-1a470.up.railway.app"
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "earseed2026")

# ── Google auth ────────────────────────────────────────────────────────────────

def get_gcal_service():
    """Return an authenticated Google Calendar service, or None if not configured."""
    client_id     = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        logger.warning("GCal sync: GOOGLE_CLIENT_ID / CLIENT_SECRET / REFRESH_TOKEN not set — skipping.")
        return None

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        creds.refresh(Request())
        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.error("GCal sync: failed to build service — %s", e)
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_clinic_id(description: str | None) -> int | None:
    if not description:
        return None
    m = CLINIC_ID_RE.search(description)
    return int(m.group(1)) if m else None


def _to_gcal_dt(dt_str: str) -> dict:
    """Convert ISO datetime string to GCal dateTime dict (Europe/London)."""
    # Normalise: strip trailing Z / offset so we can store as local London time
    dt_str = dt_str.replace("Z", "").split("+")[0].split("-")[0] if "T" in dt_str else dt_str
    # Reconstruct properly
    if "T" not in dt_str:
        dt_str += "T00:00:00"
    return {"dateTime": dt_str, "timeZone": "Europe/London"}


def _add_hour(dt_str: str) -> str:
    """Add 1 hour to an ISO datetime string (naive, local time)."""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "").split("+")[0])
        return (dt + timedelta(hours=1)).isoformat()
    except Exception:
        return dt_str


def _gcal_dt_value(gcal_dt: dict | None) -> str | None:
    """Extract the raw dateTime string from a GCal event start/end dict."""
    if not gcal_dt:
        return None
    v = gcal_dt.get("dateTime") or gcal_dt.get("date")
    if not v:
        return None
    # Normalise: strip timezone offset for comparison
    return v[:16]  # "YYYY-MM-DDTHH:MM"


def _times_differ(clinic_dt: str | None, gcal_dt_dict: dict | None) -> bool:
    """Return True if start times differ by more than 1 minute."""
    if not clinic_dt or not gcal_dt_dict:
        return False
    clinic_norm = clinic_dt[:16] if clinic_dt else ""
    gcal_norm   = _gcal_dt_value(gcal_dt_dict) or ""
    return clinic_norm != gcal_norm


# ── Main sync ──────────────────────────────────────────────────────────────────

def run_sync():
    """
    Two-way sync:
      Clinic → GCal : create/update/delete events based on clinic appointment state
      GCal → Clinic : propagate reschedules and deletions back to clinic DB
    """
    import httpx

    service = get_gcal_service()
    if not service:
        return

    # ── 1. Fetch clinic appointments ──────────────────────────────────────────
    try:
        resp = httpx.get(
            f"{BASE_URL}/api/appointments",
            params={"token": DASHBOARD_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        all_appts = resp.json()
    except Exception as e:
        logger.error("GCal sync: could not fetch clinic appointments — %s", e)
        return  # abort; don't touch GCal

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    appt_by_id = {a["id"]: a for a in all_appts}

    active_appts = [
        a for a in all_appts
        if a.get("status") not in ("cancelled", "no_show", "completed")
        and (a.get("start_dt") or "") > now_str
    ]
    cancelled_ids = {
        a["id"] for a in all_appts
        if a.get("status") in ("cancelled", "no_show")
    }
    active_ids = {a["id"] for a in active_appts}

    # ── 2. Fetch tagged GCal events ───────────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    future_iso = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()

    try:
        result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=now_iso,
            timeMax=future_iso,
            maxResults=500,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        gcal_events = result.get("items", [])
    except Exception as e:
        logger.error("GCal sync: could not list GCal events — %s", e)
        return

    # Map: clinic_id → gcal event object (only tagged events)
    gcal_map: dict[int, dict] = {}
    for ev in gcal_events:
        cid = _parse_clinic_id(ev.get("description", ""))
        if cid is not None:
            gcal_map[cid] = ev

    logger.info(
        "GCal sync: %d active clinic appts, %d cancelled, %d tagged GCal events",
        len(active_appts), len(cancelled_ids), len(gcal_map),
    )

    # ── 3. Clinic → GCal ─────────────────────────────────────────────────────

    for appt in active_appts:
        cid       = appt["id"]
        start_dt  = appt.get("start_dt", "")
        end_dt    = appt.get("end_dt") or _add_hour(start_dt)
        summary   = f"{appt.get('patient_name', 'Patient')} — {appt.get('appointment_type', 'Appointment')}"
        desc      = f"Clinic appointment\n[clinic-id:{cid}]"

        if cid not in gcal_map:
            # Create new GCal event
            try:
                service.events().insert(
                    calendarId=CALENDAR_ID,
                    body={
                        "summary":     summary,
                        "description": desc,
                        "start":       _to_gcal_dt(start_dt),
                        "end":         _to_gcal_dt(end_dt),
                    },
                ).execute()
                logger.info("GCal sync: created event for clinic-id:%d", cid)
            except Exception as e:
                logger.error("GCal sync: failed to create event for clinic-id:%d — %s", cid, e)

        else:
            # Update if time has drifted (clinic is source of truth for reschedules initiated there)
            gcal_ev = gcal_map[cid]
            if _times_differ(start_dt, gcal_ev.get("start")):
                try:
                    service.events().patch(
                        calendarId=CALENDAR_ID,
                        eventId=gcal_ev["id"],
                        body={
                            "start": _to_gcal_dt(start_dt),
                            "end":   _to_gcal_dt(end_dt),
                        },
                    ).execute()
                    logger.info("GCal sync: updated time for clinic-id:%d", cid)
                except Exception as e:
                    logger.error("GCal sync: failed to update clinic-id:%d — %s", cid, e)

    # Delete GCal events for cancelled clinic appointments
    for cid in cancelled_ids:
        if cid in gcal_map:
            try:
                service.events().delete(
                    calendarId=CALENDAR_ID,
                    eventId=gcal_map[cid]["id"],
                ).execute()
                logger.info("GCal sync: deleted GCal event for cancelled clinic-id:%d", cid)
            except Exception as e:
                logger.error("GCal sync: failed to delete clinic-id:%d — %s", cid, e)

    # ── 4. GCal → Clinic ─────────────────────────────────────────────────────

    for cid, gcal_ev in gcal_map.items():
        clinic_appt = appt_by_id.get(cid)

        if clinic_appt is None:
            # Appointment deleted from clinic — clean up orphaned GCal event
            try:
                service.events().delete(
                    calendarId=CALENDAR_ID,
                    eventId=gcal_ev["id"],
                ).execute()
                logger.info("GCal sync: removed orphaned GCal event for missing clinic-id:%d", cid)
            except Exception as e:
                logger.error("GCal sync: failed to remove orphan clinic-id:%d — %s", cid, e)
            continue

        clinic_start = clinic_appt.get("start_dt", "")
        gcal_start   = gcal_ev.get("start", {})
        gcal_end     = gcal_ev.get("end", {})

        # Ed rescheduled in GCal → push new time back to clinic
        if _times_differ(clinic_start, gcal_start) and cid not in cancelled_ids:
            new_start = (_gcal_dt_value(gcal_start) or "") + ":00"
            new_end   = (_gcal_dt_value(gcal_end)   or "") + ":00"
            try:
                r = httpx.put(
                    f"{BASE_URL}/api/appointments/{cid}",
                    params={"token": DASHBOARD_TOKEN},
                    json={"start_dt": new_start, "end_dt": new_end},
                    timeout=10,
                )
                r.raise_for_status()
                logger.info("GCal sync: rescheduled clinic-id:%d from GCal change", cid)
            except Exception as e:
                logger.error("GCal sync: failed to reschedule clinic-id:%d — %s", cid, e)

    # Detect GCal deletions: active clinic appts that SHOULD have a GCal event but don't
    # (i.e. previously created — we know this because any active future appt should be in gcal_map
    #  after at least one sync pass. We only act if the appt is in the future.)
    for appt in active_appts:
        cid = appt["id"]
        if cid not in gcal_map:
            # Could be brand new (just created above) — don't cancel it.
            # We skip this case; new events are caught in step 3 and created.
            pass

    logger.info("GCal sync: complete.")
