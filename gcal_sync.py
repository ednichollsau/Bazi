"""Opt-in legacy-calendar sync. Never imports unrelated events or deletes orphans.

This module is NOT a connector for the current Supabase booking system.
GCAL_SYNC_ENABLED must explicitly be true before either scheduled or manual sync.
Calendar edits require an agreed baseline; conflicting edits are reported, not guessed.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import hashlib
import logging
import os
import re

logger = logging.getLogger(__name__)
LONDON = ZoneInfo('Europe/London')
CALENDAR_ID = os.environ.get('GOOGLE_CALENDAR_ID', 'acu@ednicholls.com')
CLINIC_ID_RE = re.compile(r'\[clinic-id:(\d+)\]')


def _instant(value):
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        # Reject ambiguous/nonexistent clock-change times rather than choose silently.
        candidates = {dt.replace(tzinfo=LONDON, fold=fold).astimezone(timezone.utc)
                      for fold in (0, 1)
                      if dt.replace(tzinfo=LONDON, fold=fold).astimezone(timezone.utc)
                      .astimezone(LONDON).replace(tzinfo=None) == dt}
        if len(candidates) != 1:
            raise ValueError('Ambiguous or nonexistent London time; provide an explicit offset')
        return candidates.pop()
    return dt.astimezone(timezone.utc)


def _to_gcal_dt(value):
    return {'dateTime': _instant(value).astimezone(LONDON).isoformat(), 'timeZone': 'Europe/London'}


def _span(start, end):
    a, b = _instant(start), _instant(end)
    if b <= a:
        raise ValueError('Appointment must end after it starts')
    return (a.isoformat(), b.isoformat())


def _event_span(event):
    return _span(event['start']['dateTime'], event['end']['dateTime'])


def _baseline(event):
    props = event.get('extendedProperties', {}).get('private', {})
    if props.get('enaStart') and props.get('enaEnd'):
        return _span(props['enaStart'], props['enaEnd'])
    return None


def decide(clinic, event):
    """Pure reconciliation decision. Deletion is never inferred from absence."""
    if clinic['status'] in ('cancelled', 'no_show', 'completed'):
        return 'skip'
    current = _span(clinic['start_dt'], clinic['end_dt'])
    if event is None:
        return 'create'
    if event.get('status') == 'cancelled':
        return 'conflict'  # Calendar deletion cannot bypass clinic cancellation rules.
    remote = _event_span(event)
    baseline = _baseline(event)
    if current == remote:
        return 'unchanged' if baseline == current else 'baseline'
    if baseline is None:
        return 'conflict'  # Older events need an explicit initial reconciliation.
    if current == baseline:
        return 'pull'
    if remote == baseline:
        return 'push'
    return 'conflict'


def _properties(event, span):
    props = dict(event.get('extendedProperties', {}).get('private', {}))
    props.update(enaStart=span[0], enaEnd=span[1])
    return {'private': props}


def _event_id(cid):
    # Google accepts lowercase base32hex IDs; a hex hash is a valid subset.
    return hashlib.sha256(f'ena-legacy:{CALENDAR_ID}:{cid}'.encode()).hexdigest()


def _patch(events, event, body):
    request = events.patch(calendarId=CALENDAR_ID, eventId=event['id'], body=body, sendUpdates='none')
    if event.get('etag'):
        request.headers['If-Match'] = event['etag']
    return request.execute()


def _all_events(events):
    # No time window: moving an existing linked event outside a window must not duplicate it.
    result, page = [], None
    while True:
        args = dict(calendarId=CALENDAR_ID, maxResults=2500, showDeleted=False)
        if page:
            args['pageToken'] = page
        response = events.list(**args).execute()
        result.extend(response.get('items', []))
        page = response.get('nextPageToken')
        if not page:
            return result


def run_sync():
    if os.environ.get('GCAL_SYNC_ENABLED', '').lower() != 'true':
        return {'status': 'disabled'}
    from database import DATABASE_URL, get_conn
    from psycopg2.extras import RealDictCursor
    if not DATABASE_URL:
        return {'status': 'unconfigured'}
    service = get_gcal_service()
    if not service:
        return {'status': 'unconfigured'}
    conn = get_conn()
    counts = {'create': 0, 'push': 0, 'pull': 0, 'baseline': 0, 'conflict': 0, 'error': 0}
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(739126410)')
            if not cur.fetchone()[0]:
                return {'status': 'already_running'}
        conn.commit()
        events = service.events()
        linked = {}
        for event in _all_events(events):
            match = CLINIC_ID_RE.search(event.get('description') or '')
            if match:
                linked.setdefault(int(match.group(1)), []).append(event)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM appointments WHERE start_dt > NOW() AND status NOT IN ('cancelled','no_show','completed')")
            ids = [row[0] for row in cur.fetchall()]
        conn.commit()
        for cid in ids:
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    # Prevent a console edit racing the calendar reconciliation.
                    cur.execute('SELECT * FROM appointments WHERE id=%s FOR UPDATE', (cid,))
                    clinic = cur.fetchone()
                    if not clinic:
                        conn.rollback()
                        continue
                    matches = linked.get(cid, [])
                    if len(matches) > 1:
                        counts['conflict'] += 1
                        conn.rollback()
                        continue
                    event = matches[0] if matches else None
                    if event is None:
                        # Deterministic ID recovers a previous insert whose response was lost.
                        try:
                            event = events.get(calendarId=CALENDAR_ID, eventId=_event_id(cid)).execute()
                        except Exception as exc:
                            if getattr(getattr(exc, 'resp', None), 'status', None) != 404:
                                raise
                    action = decide(clinic, event)
                    if action in ('skip', 'unchanged'):
                        conn.commit()
                        continue
                    if action == 'conflict':
                        counts[action] += 1
                        logger.warning('Calendar sync conflict for legacy appointment %s; neither side changed', cid)
                        conn.rollback()
                        continue
                    span = _span(clinic['start_dt'], clinic['end_dt'])
                    if action == 'pull':
                        span = _event_span(event)
                        cur.execute('UPDATE appointments SET start_dt=%s, end_dt=%s WHERE id=%s', (*span, cid))
                        # Commit DB first. If the metadata update fails, next run sees equal times.
                        conn.commit()
                    body = {'extendedProperties': _properties(event or {}, span)}
                    if action in ('create', 'push'):
                        body.update(start=_to_gcal_dt(span[0]), end=_to_gcal_dt(span[1]))
                    if action == 'create':
                        body.update(id=_event_id(cid), summary='Clinic appointment', description=f'[clinic-id:{cid}]')
                        events.insert(calendarId=CALENDAR_ID, body=body, sendUpdates='none').execute()
                    else:
                        _patch(events, event, body)
                    conn.commit()
                    counts[action] += 1
            except Exception:
                conn.rollback()
                counts['error'] += 1
                logger.exception('Calendar sync failed for legacy appointment %s', cid)
        return {'status': 'complete', **counts}
    finally:
        # Closing the connection also releases the cross-process advisory lock.
        conn.close()


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
