"""
api_server.py  —  Four Pillars · Elemental Constitution API  v4.0
Merged: Ba Zi reading + Ear Seed Protocol + PostgreSQL database + Practitioner dashboard
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import date, datetime, timezone
import anthropic
import httpx
import os
import re
import math
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from bazi_calculator import (
    get_four_pillars,
    get_element_counts,
    interpret_constitution,
    spread_score,
    is_balanced,
    STATE_RANK,
)
from prompt_builder import SYSTEM_PROMPT, build_user_message
from treatment_protocol import get_protocol, AURICULAR_POINTS
from database import (
    init_db, save_submission, list_submissions, get_submission, update_notes,
    email_exists, submission_exists,
    # patients
    create_patient, get_patient, get_patient_by_email, find_or_create_patient,
    list_patients, update_patient, update_patient_notes, get_patient_history,
    # appointments
    create_appointment, get_appointment, list_appointments, list_today_appointments,
    update_appointment_status, update_appointment,
    # appointment types
    list_appointment_types,
    # availability
    list_availability, set_availability,
    # blocked times
    list_blocked_times, add_blocked_time, delete_blocked_time,
    # treatment notes
    get_treatment_note, save_treatment_note, list_documentation_queue,
    # patient notes
    create_patient_note, list_patient_notes, update_patient_note, delete_patient_note,
    # treatment zones
    save_treatment_zones, list_treatment_zones, delete_treatment_zone_record,
)

# ── App ────────────────────────────────────────────────────

app = FastAPI(title="Four Pillars · Elemental Constitution API", version="4.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    init_db()
    import asyncio
    asyncio.create_task(_gcal_sync_loop())


async def _gcal_sync_loop():
    """Run GCal two-way sync once an hour, forever."""
    import asyncio
    from gcal_sync import run_sync
    while True:
        try:
            await asyncio.to_thread(run_sync)
        except Exception as e:
            logger.error("GCal background sync error: %s", e)
        await asyncio.sleep(3600)  # 1 hour


# ── Auth helper ────────────────────────────────────────────

DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
ADMIN_EMAIL     = os.environ.get("ADMIN_EMAIL", "acu@ednicholls.com")
ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD", "")

def _check_token(request: Request) -> bool:
    # Accept token from URL param OR Authorization: Bearer header
    token = request.query_params.get("token", "")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    return bool(DASHBOARD_TOKEN) and token == DASHBOARD_TOKEN


# ── Google Calendar OAuth + sync endpoints ─────────────────────────────────────

_GCAL_SCOPES     = ["https://www.googleapis.com/auth/calendar"]
_GCAL_REDIRECT   = os.environ.get("GCAL_REDIRECT_URI", "https://ednichollsconsole.up.railway.app/auth/gcal/callback")

@app.get("/auth/gcal")
def auth_gcal_start(request: Request):
    """Step 1: redirect browser to Google's OAuth consent screen."""
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    if not client_id:
        return JSONResponse({"error": "GOOGLE_CLIENT_ID not set in Railway environment."}, status_code=500)
    from urllib.parse import urlencode
    params = urlencode({
        "client_id":     client_id,
        "redirect_uri":  _GCAL_REDIRECT,
        "response_type": "code",
        "scope":         " ".join(_GCAL_SCOPES),
        "access_type":   "offline",
        "prompt":        "consent",  # force refresh_token to be returned
    })
    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@app.get("/auth/gcal/callback")
async def auth_gcal_callback(request: Request, code: str = "", error: str = ""):
    """Step 2: exchange auth code for tokens and display the refresh token."""
    if error:
        return HTMLResponse(f"<h2>OAuth error: {error}</h2>")
    if not code:
        return HTMLResponse("<h2>No code returned.</h2>")

    client_id     = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code":          code,
                "client_id":     client_id,
                "client_secret": client_secret,
                "redirect_uri":  _GCAL_REDIRECT,
                "grant_type":    "authorization_code",
            },
        )
    data = resp.json()
    refresh_token = data.get("refresh_token", "")
    if not refresh_token:
        return HTMLResponse(f"<h2>No refresh token returned.</h2><pre>{data}</pre>")

    return HTMLResponse(f"""
<!doctype html><html><head><meta charset=utf-8>
<title>Google Calendar Connected</title>
<style>body{{font-family:system-ui;max-width:600px;margin:60px auto;padding:0 20px;}}
code{{background:#f0f0f0;padding:12px;display:block;word-break:break-all;border-radius:8px;margin:12px 0;}}
</style></head><body>
<h2>✅ Google Calendar authorised</h2>
<p>Copy the refresh token below and add it to Railway as <strong>GOOGLE_REFRESH_TOKEN</strong>:</p>
<code>{refresh_token}</code>
<p>Once set, redeploy Railway and the hourly sync will start automatically.</p>
</body></html>""")


@app.post("/api/gcal/sync")
async def api_gcal_sync(request: Request):
    """Manually trigger an immediate GCal sync."""
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    import asyncio
    from gcal_sync import run_sync
    asyncio.create_task(asyncio.to_thread(run_sync))
    return {"ok": True, "message": "Sync started in background."}


# ── Auth ───────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str

@app.post("/api/auth/login")
def auth_login(req: LoginRequest):
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD env var not set on server.")
    email_ok    = req.email.strip().lower() == ADMIN_EMAIL.lower()
    password_ok = req.password == ADMIN_PASSWORD
    if email_ok and password_ok:
        return {"token": DASHBOARD_TOKEN}
    raise HTTPException(status_code=401, detail="Invalid email or password.")


# ── Lookup tables ──────────────────────────────────────────

ELEM_HEX = {
    "Wood":  "#6B8F6B",
    "Fire":  "#B85C4A",
    "Earth": "#C4943A",
    "Metal": "#7D8C8A",
    "Water": "#5B7FA3",
}

STEM_ELEM = {
    "甲": "Wood",  "乙": "Wood",
    "丙": "Fire",  "丁": "Fire",
    "戊": "Earth", "己": "Earth",
    "庚": "Metal", "辛": "Metal",
    "壬": "Water", "癸": "Water",
}

STEM_PIN = {
    "甲": "Jiǎ", "乙": "Yǐ",  "丙": "Bǐng", "丁": "Dīng", "戊": "Wù",
    "己": "Jǐ",  "庚": "Gēng","辛": "Xīn",  "壬": "Rén",  "癸": "Guǐ",
}

BRANCH_PIN = {
    "子": "Zǐ",  "丑": "Chǒu","寅": "Yín", "卯": "Mǎo", "辰": "Chén",
    "巳": "Sì",  "午": "Wǔ",  "未": "Wèi", "申": "Shēn","酉": "Yǒu",
    "戌": "Xū",  "亥": "Hài",
}

PILLAR_LABEL = {
    "Year":  "Your roots & early life",
    "Month": "Your career & outer world",
    "Day":   "Your inner self",
    "Hour":  "Your dreams & legacy",
}

STATE_DESC = {
    "Absent":   "not present",
    "Low":      "gently present",
    "Balanced": "in good flow",
    "Excess":   "very dominant",
}

STATE_PCT = {
    "Absent": 5, "Low": 30, "Balanced": 60, "Excess": 100,
}

BRANCH_ANIMAL = {
    "子": "Rat",  "丑": "Ox",    "寅": "Tiger",   "卯": "Rabbit",
    "辰": "Dragon","巳": "Snake", "午": "Horse",   "未": "Goat",
    "申": "Monkey","酉": "Rooster","戌": "Dog",    "亥": "Pig",
}

ANIMAL_TRAIT = {
    "Rat":     "resourceful, quick-witted and endlessly adaptive",
    "Ox":      "steadfast, patient and quietly powerful",
    "Tiger":   "courageous, dynamic and fiercely independent",
    "Rabbit":  "perceptive, diplomatic and deeply intuitive",
    "Dragon":  "magnetic, bold and driven by vision",
    "Snake":   "wise, discerning and naturally strategic",
    "Horse":   "free-spirited, expressive and driven by passion",
    "Goat":    "gentle, creative and deeply empathic",
    "Monkey":  "inventive, versatile and endlessly curious",
    "Rooster": "observant, precise and confidently direct",
    "Dog":     "loyal, principled and trustworthy to the core",
    "Pig":     "generous, sincere and quietly determined",
}

ELEM_QUALITY = {
    "Wood":  "growth, vision and the courage to begin",
    "Fire":  "passion, warmth and the power to illuminate",
    "Earth": "stability, nurture and the wisdom to endure",
    "Metal": "clarity, precision and the will to refine",
    "Water": "depth, flow and the intelligence to adapt",
}

# Element-specific banner themes for email
ZODIAC_THEME_EMAIL = {
    "Wood":  {"bg": "#182414", "text": "#F0EBE0", "accent": "#7DAA7D"},
    "Fire":  {"bg": "#27120E", "text": "#FFF0E8", "accent": "#CC7060"},
    "Earth": {"bg": "#231A09", "text": "#FFF5E0", "accent": "#D4A840"},
    "Metal": {"bg": "#181F24", "text": "#EFF2F4", "accent": "#8AAAB0"},
    "Water": {"bg": "#0E1825", "text": "#EAF0F8", "accent": "#6A96BE"},
}

# Element-specific descriptions per pillar position
PILLAR_ELEM_DESC = {
    "Year": {
        "Wood":  "Wood in the Year position introduces a quality of growth and creative reaching into your origins — a lineage touched by movement, learning, or the instinct to begin again. One generative thread running through the foundation.",
        "Fire":  "Fire in the Year position lends warmth and intensity to the story you came from — early life touched by passion, visibility, or change. A vivid note in the background of the chart.",
        "Earth": "Earth in the Year position adds a quality of stability and endurance to your roots — an ancestry shaped by nourishment, reliability, and quiet holding. A steadying thread early in the picture.",
        "Metal": "Metal in the Year position introduces precision and high standards as context for your formation — a background shaped by order, discernment, or a demand for refinement. One shaping layer among several.",
        "Water": "Water in the Year position carries depth and adaptability through your origins — a lineage drawn to intuition, wisdom, or continual movement. A quiet current running beneath the surface of the chart.",
    },
    "Month": {
        "Wood":  "Wood in the Month position lends a quality of growth and initiation to how you engage with the outer world. There is a drive to expand and build — most alive professionally when something new is taking root.",
        "Fire":  "Fire in the Month position brings a quality of intensity and expressiveness to your career — a pull toward visibility or creative output. One active thread in how you meet the world.",
        "Earth": "Earth in the Month position contributes steadiness and reliability to your professional self — a capacity to sustain, organise, and hold things together. A quality of patient structural strength in this layer.",
        "Metal": "Metal in the Month position adds a refining quality to your outer world — a professional pull toward clarity, precision, and getting things exactly right. One discerning current in the chart.",
        "Water": "Water in the Month position introduces depth and adaptability to how you navigate the outer world — a fluid, intuitive reading of environments. One thread in the way you move through complexity.",
    },
    "Day": {
        "Wood":  "Wood as Day Master places a quality of growth and expansiveness at the centre of the chart — a natural orientation toward beginning, reaching, and becoming. This element shapes the core, though it is always in dialogue with everything around it.",
        "Fire":  "Fire as Day Master lends warmth, expressiveness, and a quality of illumination to the centre of the picture. How you give and receive runs through this element — one defining note within a larger chord.",
        "Earth": "Earth as Day Master contributes steadiness and a capacity to nourish as central qualities — a natural holding quality in relationships and in self. This grounds the constitution, though the whole picture is more complex.",
        "Metal": "Metal as Day Master brings a quality of clarity and refinement to the centre of the chart — a discerning, precise quality in how you engage with self and others. One thread that runs through everything, not the whole of it.",
        "Water": "Water as Day Master lends depth and intuitive adaptability as central qualities — a feeling way of moving through the inner world. A deep current in the chart, always in relationship with the other elements.",
    },
    "Hour": {
        "Wood":  "Wood in the Hour position suggests a quality of growth and vision in the private, aspirational layer — a pull toward something living and expansive. The subtlest pillar, and often the most quietly insistent.",
        "Fire":  "Fire in the Hour position hints at a desire for impact or illumination in the background of ambition — a wish to inspire or to be remembered warmly. A note held gently in the deeper reaches of the chart.",
        "Earth": "Earth in the Hour position suggests a quality of nourishment and endurance in private aspiration — a wish to have built something that sustains. A grounding note in the more interior layers.",
        "Metal": "Metal in the Hour position introduces a quality of refinement and mastery into the aspirational layer — a pull toward precision and lasting quality. Subtle, but present throughout.",
        "Water": "Water in the Hour position carries depth and philosophical breadth into the most private layer of the chart — aspirations that are felt more than spoken, expansive and often hard to articulate.",
    },
}

TIP_META = {
    "NOURISH": {"label": "Nourish",  "col": "#6B8F6B"},
    "MOVE":    {"label": "Move",     "col": "#B85C4A"},
    "REST":    {"label": "Rest",     "col": "#5B7FA3"},
    "MIND":    {"label": "Mind",     "col": "#7D8C8A"},
    "SEASONS": {"label": "Seasons",  "col": "#C4943A"},
}

# 2026 丙午 Fire Horse year energy per element
YEAR_ENERGY_EMAIL = {
    "Wood":  {"pct": 42,  "tag": "Drawn upon",  "note": "Feeds the Fire — may feel depleted"},
    "Fire":  {"pct": 100, "tag": "Amplified",   "note": "Double Fire year — this element surges"},
    "Earth": {"pct": 68,  "tag": "Rising",      "note": "Born from Fire; gaining momentum"},
    "Metal": {"pct": 18,  "tag": "Challenged",  "note": "Fire melts Metal — under pressure"},
    "Water": {"pct": 12,  "tag": "Constrained", "note": "Opposes Fire; quietened this year"},
}

# Tip icon categories — keyword scoring for top 3 featured tips
TIP_ICONS_EMAIL = [
    {"key": "NOURISH", "col": "#6B8F6B", "label": "Nourish",
     "tagline": "Feed the season — eat with your element",
     "words": ["eat","food","green","herb","nourish","diet","sour","sprout","leafy","vegetable","fruit","flavour","flavor","meal","cook"],
     "svg": '<svg width="44" height="44" viewBox="0 0 44 44" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M22 8C22 8 12 14 12 24C12 29.5 16.5 34 22 34C27.5 34 32 29.5 32 24C32 14 22 8 22 8Z" stroke="COL" stroke-width="1.8" stroke-linejoin="round"/><path d="M22 8V34" stroke="COL" stroke-width="1.2" stroke-linecap="round"/><path d="M16 18C18 19 20 21 22 20" stroke="COL" stroke-width="1.2" stroke-linecap="round"/><path d="M28 18C26 19 24 21 22 20" stroke="COL" stroke-width="1.2" stroke-linecap="round"/></svg>'},
    {"key": "MOVE",    "col": "#B85C4A", "label": "Move",
     "tagline": "Gentle, expansive movement daily",
     "words": ["move","walk","yoga","stretch","tai chi","qigong","exercise","body","tendon","physical","dance","swim","run","morning"],
     "svg": '<svg width="44" height="44" viewBox="0 0 44 44" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="22" cy="10" r="3.5" stroke="COL" stroke-width="1.8"/><path d="M22 13.5L18 22L13 28" stroke="COL" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M22 13.5L26 22L31 28" stroke="COL" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M18 20L14 36" stroke="COL" stroke-width="1.8" stroke-linecap="round"/><path d="M26 20L30 36" stroke="COL" stroke-width="1.8" stroke-linecap="round"/></svg>'},
    {"key": "FOREST",  "col": "#4A6B5A", "label": "Nature",
     "tagline": "Time among trees restores what city life takes",
     "words": ["tree","forest","nature","outside","outdoor","green space","walk","park","garden","earth","soil","ground","roots"],
     "svg": '<svg width="44" height="44" viewBox="0 0 44 44" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M22 8L10 24H18L14 36H30L26 24H34L22 8Z" stroke="COL" stroke-width="1.8" stroke-linejoin="round"/></svg>'},
    {"key": "WRITE",   "col": "#7D8C8A", "label": "Journal",
     "tagline": "Unfiltered writing — let the mind empty onto the page",
     "words": ["journal","write","writing","record","note","express","word","diary","reflect","pen","paper","unfiltered"],
     "svg": '<svg width="44" height="44" viewBox="0 0 44 44" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="10" y="8" width="20" height="26" rx="2" stroke="COL" stroke-width="1.8"/><path d="M14 16H26M14 21H26M14 26H22" stroke="COL" stroke-width="1.4" stroke-linecap="round"/><path d="M30 10L36 16L28 32L22 32L22 26L30 10Z" stroke="COL" stroke-width="1.6" stroke-linejoin="round"/></svg>'},
    {"key": "BREATHE", "col": "#5B7FA3", "label": "Breathe",
     "tagline": "Breath is medicine — pause and breathe slowly",
     "words": ["breath","breathe","breathing","inhale","exhale","pause","slow","lung","air","sigh","exhaust"],
     "svg": '<svg width="44" height="44" viewBox="0 0 44 44" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M8 22C8 22 14 14 22 14C30 14 36 22 36 22C36 22 30 30 22 30C14 30 8 22 8 22Z" stroke="COL" stroke-width="1.8" stroke-linejoin="round"/><circle cx="22" cy="22" r="4" stroke="COL" stroke-width="1.6"/></svg>'},
    {"key": "WATER",   "col": "#5B7FA3", "label": "Hydrate",
     "tagline": "Fluids support every system — drink warm water",
     "words": ["water","fluid","hydrat","drink","swim","bath","flow","kidney","bladder","warm water"],
     "svg": '<svg width="44" height="44" viewBox="0 0 44 44" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M22 8C22 8 12 20 12 28C12 33.5 16.5 38 22 38C27.5 38 32 33.5 32 28C32 20 22 8 22 8Z" stroke="COL" stroke-width="1.8" stroke-linejoin="round"/><path d="M16 29C17 31.5 19.5 33 22 33" stroke="COL" stroke-width="1.4" stroke-linecap="round"/></svg>'},
    {"key": "REST",    "col": "#8A7456", "label": "Rest",
     "tagline": "Rest is not weakness — it is where you regenerate",
     "words": ["rest","sleep","restore","quiet","slow","nap","recover","night","bed","tired","exhaust"],
     "svg": '<svg width="44" height="44" viewBox="0 0 44 44" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M28 14C28 20.6 22.6 26 16 26C14.7 26 13.4 25.8 12.2 25.4C14.2 31.4 19.8 35.6 26.5 35.6C34.2 35.6 40.5 29.3 40.5 21.6C40.5 14.8 35.8 9 29.5 8C28.5 9.8 28 11.8 28 14Z" stroke="COL" stroke-width="1.8" stroke-linejoin="round"/></svg>'},
    {"key": "GROUND",  "col": "#C4943A", "label": "Ground",
     "tagline": "Root daily rituals anchor you through turbulent years",
     "words": ["ground","root","anchor","ritual","routine","stable","earth","centre","center","base","foundation"],
     "svg": '<svg width="44" height="44" viewBox="0 0 44 44" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M22 10V28" stroke="COL" stroke-width="1.8" stroke-linecap="round"/><path d="M22 28C18 22 12 20 10 22" stroke="COL" stroke-width="1.5" stroke-linecap="round"/><path d="M22 28C26 22 32 20 34 22" stroke="COL" stroke-width="1.5" stroke-linecap="round"/><path d="M22 28C20 24 16 24 14 26" stroke="COL" stroke-width="1.3" stroke-linecap="round"/><path d="M22 28C24 24 28 24 30 26" stroke="COL" stroke-width="1.3" stroke-linecap="round"/><line x1="10" y1="36" x2="34" y2="36" stroke="COL" stroke-width="1.8" stroke-linecap="round"/></svg>'},
]

# ── Request / Response ─────────────────────────────────────

class ReadingRequest(BaseModel):
    name:       Optional[str] = Field(default="Friend")
    email:      str
    year:       int  = Field(..., ge=1900, le=2100)
    month:      int  = Field(..., ge=1,    le=12)
    day:        int  = Field(..., ge=1,    le=31)
    hour:       Optional[int] = Field(default=None)
    handedness: Optional[str] = Field(default="right")

    @validator("year")
    def not_future(cls, v):
        if v > date.today().year:
            raise ValueError("Birth year cannot be in the future.")
        return v

class ReadingResponse(BaseModel):
    success:      bool
    message:      str
    name:         Optional[str]  = None
    pillars_data: Optional[dict] = None
    constitution: Optional[dict] = None
    reading_text: Optional[str]  = None

# ── Health ─────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "Four Pillars · Elemental Constitution API"}

# ── Email helpers ──────────────────────────────────────────

def _pillar_cards_html(pillars: dict) -> str:
    labels = [l for l in ["Year", "Month", "Day", "Hour"] if l in pillars]
    col_w  = 100 // len(labels)
    cells  = ""
    for lbl in labels:
        stem, branch = pillars[lbl]
        elem    = STEM_ELEM.get(stem, "")
        col     = ELEM_HEX.get(elem, "#8B6F5C")
        pin     = STEM_PIN.get(stem, stem) + "\u2013" + BRANCH_PIN.get(branch, branch)
        sub     = PILLAR_LABEL.get(lbl, "")
        is_day  = lbl == "Day"
        day_tag = ""
        if is_day:
            day_tag = (
                '<p style="margin:8px 0 0;font-family:Raleway,Arial,sans-serif;'
                'font-size:7px;font-weight:700;letter-spacing:0.15em;background:#3D5A4C;'
                'color:#FAF3E4;padding:3px 7px;display:inline-block;">DAY MASTER</p>'
            )
        cells += (
            '<td width="' + str(col_w) + '%" style="padding:0 5px;vertical-align:top;">'
            '<table width="100%" cellpadding="0" cellspacing="0" style="'
            'background:#FAF3E4;border:1px solid #E0D5C1;border-top:3px solid ' + col + ';">'
            '<tr><td style="padding:16px 10px 14px;text-align:center;">'
            '<p style="margin:0 0 6px;font-family:Raleway,Arial,sans-serif;font-size:8px;'
            'font-weight:700;letter-spacing:0.22em;text-transform:uppercase;color:#8B6F5C;">' + lbl + '</p>'
            '<p style="margin:0 0 8px;font-size:28px;line-height:1.15;color:#2C1A0E;font-family:serif;">'
            + stem + '<br>' + branch + '</p>'
            '<p style="margin:0 0 4px;font-family:Raleway,Arial,sans-serif;font-size:11px;'
            'font-style:italic;color:#8B6F5C;">' + pin + '</p>'
            '<p style="margin:0 0 6px;font-family:Raleway,Arial,sans-serif;font-size:8px;'
            'font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:' + col + ';">'
            '&#9679; ' + elem + '</p>'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:9px;'
            'color:#A08470;letter-spacing:0.05em;">' + sub + '</p>'
            + day_tag +
            '</td></tr></table></td>'
        )
    return '<table width="100%" cellpadding="0" cellspacing="0"><tr>' + cells + '</tr></table>'


def _element_bars_html(constitution: dict) -> str:
    ORDER  = ["Wood", "Fire", "Earth", "Metal", "Water"]
    rows   = ""
    for elem in ORDER:
        state = constitution.get(elem, "Balanced")
        col   = ELEM_HEX.get(elem, "#8B6F5C")
        pct   = STATE_PCT.get(state, 55)
        desc  = STATE_DESC.get(state, "")
        empty = 100 - pct
        rows += (
            '<tr>'
            '<td width="60" style="padding:7px 12px 7px 0;vertical-align:middle;">'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:9px;'
            'font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:' + col + ';">'
            + elem + '</p></td>'
            '<td style="padding:7px 0;vertical-align:middle;">'
            '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            '<td width="' + str(pct) + '%" style="height:6px;background:' + col + ';'
            'border-radius:3px 0 0 3px;" bgcolor="' + col + '">&nbsp;</td>'
            '<td style="height:6px;background:#E0D5C1;border-radius:0 3px 3px 0;" bgcolor="#E0D5C1">&nbsp;</td>'
            '</tr></table></td>'
            '<td width="90" style="padding:7px 0 7px 14px;vertical-align:middle;text-align:right;">'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:11px;'
            'font-style:italic;color:#8B6F5C;">' + state + '</p>'
            '<p style="margin:2px 0 0;font-family:Raleway,Arial,sans-serif;font-size:9px;'
            'color:#A08470;">' + desc + '</p>'
            '</td></tr>'
        )
    return '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">' + rows + '</table>'


def _zodiac_banner_html(pillars: dict) -> str:
    year_pillar = pillars.get("Year")
    if not year_pillar:
        return ""
    stem, branch = year_pillar[0], year_pillar[1]
    elem   = STEM_ELEM.get(stem, "")
    animal = BRANCH_ANIMAL.get(branch, "")
    if not elem or not animal:
        return ""
    theme    = ZODIAC_THEME_EMAIL.get(elem, ZODIAC_THEME_EMAIL["Wood"])
    stem_pin = STEM_PIN.get(stem, stem)
    br_pin   = BRANCH_PIN.get(branch, branch)
    trait    = (
        "The " + elem + " " + animal + " carries the energy of "
        + ELEM_QUALITY.get(elem, elem.lower())
        + ", expressed through a "
        + ANIMAL_TRAIT.get(animal, "deeply individual")
        + " spirit."
    )
    return (
        '<tr><td style="background:' + theme["bg"] + ';padding:40px 48px 36px;text-align:center;">'
        '<p style="margin:0 0 12px;font-family:Raleway,Arial,sans-serif;font-size:8px;'
        'letter-spacing:0.32em;text-transform:uppercase;color:' + theme["accent"] + ';">'
        'Your Year Sign</p>'
        '<h2 style="margin:0 0 10px;font-family:Raleway,Arial,sans-serif;font-size:36px;'
        'font-weight:300;letter-spacing:0.2em;text-transform:uppercase;color:' + theme["text"] + ';line-height:1.1;">'
        + elem + ' ' + animal + '</h2>'
        '<p style="margin:0 0 16px;font-family:Raleway,Arial,sans-serif;font-size:15px;'
        'letter-spacing:0.18em;color:' + theme["accent"] + ';">'
        + stem + branch + ' &nbsp;&middot;&nbsp; ' + stem_pin + ' ' + br_pin + '</p>'
        '<div style="width:32px;height:1px;background:' + theme["accent"] + ';opacity:0.4;margin:0 auto 16px;"></div>'
        '<p style="margin:0 auto;font-family:Raleway,Arial,sans-serif;font-size:13px;'
        'font-style:italic;font-weight:300;color:' + theme["text"] + ';opacity:0.85;'
        'max-width:420px;line-height:1.8;">' + trait + '</p>'
        '</td></tr>'
    )


def _pillar_prose_html(pillars: dict, BRL: str) -> str:
    order = ["Year", "Month", "Day", "Hour"]
    blocks = ""
    for key in order:
        p = pillars.get(key)
        if not p:
            continue
        stem = p[0]
        elem = STEM_ELEM.get(stem, "")
        col  = ELEM_HEX.get(elem, "#8B6F5C")
        pin  = STEM_PIN.get(stem, stem)
        desc = PILLAR_ELEM_DESC.get(key, {}).get(elem, "")
        is_day = key == "Day"
        dm_badge = (
            '<span style="display:inline-block;font-family:Raleway,Arial,sans-serif;'
            'font-size:7px;font-weight:700;letter-spacing:0.15em;background:#4A6B5A;'
            'color:#FAF3E4;padding:2px 7px;vertical-align:middle;margin-left:8px;">'
            'DAY MASTER</span>'
        ) if is_day else ""
        blocks += (
            '<tr>'
            '<td width="3" style="background:' + col + ';border-radius:2px;" bgcolor="' + col + '">&nbsp;</td>'
            '<td width="14">&nbsp;</td>'
            '<td style="padding:6px 0 16px;">'
            '<p style="margin:0 0 4px;font-family:Raleway,Arial,sans-serif;font-size:9px;'
            'font-weight:700;letter-spacing:0.2em;text-transform:uppercase;color:' + col + ';">'
            + key + ' &nbsp;' + stem + '&nbsp; ' + pin
            + dm_badge
            + '</p>'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:13px;'
            'font-weight:300;font-style:italic;line-height:1.75;color:#6B5740;">'
            + desc
            + '</p>'
            '</td>'
            '</tr>'
        )
    return (
        '<tr><td style="padding:20px 0 0;" colspan="3">'
        '<table width="100%" cellpadding="0" cellspacing="6">'
        + blocks +
        '</table></td></tr>'
    )


def _year_chart_email_html(constitution: dict) -> str:
    ORDER = ["Wood", "Fire", "Earth", "Metal", "Water"]
    legend = (
        '<tr><td style="padding:0 0 16px;">'
        '<table cellpadding="0" cellspacing="0"><tr>'
        '<td style="padding-right:20px;">'
        '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:8px;'
        'letter-spacing:0.1em;text-transform:uppercase;color:#8A7456;">'
        '<span style="display:inline-block;width:16px;height:5px;background:#4D5D53;'
        'border-radius:2px;vertical-align:middle;margin-right:5px;"></span>'
        'Your constitution</p></td>'
        '<td>'
        '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:8px;'
        'letter-spacing:0.1em;text-transform:uppercase;color:#8A7456;">'
        '<span style="display:inline-block;width:16px;height:3px;background:#C4703A;'
        'border-radius:2px;vertical-align:middle;margin-right:5px;"></span>'
        '2026 year energy</p></td>'
        '</tr></table>'
        '<div style="width:100%;height:1px;background:#D8CCBA;margin-top:10px;"></div>'
        '</td></tr>'
    )
    rows = ""
    for elem in ORDER:
        state     = constitution.get(elem, "Balanced")
        col       = ELEM_HEX.get(elem, "#8B6F5C")
        const_pct = STATE_PCT.get(state, 55)
        ye        = YEAR_ENERGY_EMAIL.get(elem, {"pct": 50, "tag": "", "note": ""})
        yr_pct    = ye["pct"]
        yr_tag    = ye["tag"]
        yr_note   = ye["note"]
        rows += (
            '<tr><td style="padding:0 0 18px;">'
            '<table width="100%" cellpadding="0" cellspacing="0">'
            '<tr><td colspan="3" style="padding:0 0 5px;">'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:9px;'
            'font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:' + col + ';">'
            + elem + '</p></td></tr>'
            '<tr>'
            '<td width="110" style="vertical-align:middle;padding-right:10px;">'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:7px;'
            'letter-spacing:0.12em;text-transform:uppercase;font-weight:600;'
            'color:#4D5D53;white-space:nowrap;">Your constitution</p></td>'
            '<td style="vertical-align:middle;">'
            '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            '<td width="' + str(const_pct) + '%" height="8" style="background:' + col + ';'
            'border-radius:4px 0 0 4px;" bgcolor="' + col + '">&nbsp;</td>'
            '<td height="8" style="background:#E0D5C1;border-radius:0 4px 4px 0;" bgcolor="#E0D5C1">&nbsp;</td>'
            '</tr></table></td>'
            '<td width="70" style="vertical-align:middle;text-align:right;padding-left:10px;">'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:8px;'
            'letter-spacing:0.14em;text-transform:uppercase;font-weight:700;color:' + col + ';">'
            + state + '</p></td>'
            '</tr>'
            '<tr>'
            '<td width="110" style="vertical-align:middle;padding:4px 10px 0 0;">'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:7px;'
            'letter-spacing:0.12em;text-transform:uppercase;font-weight:600;'
            'color:#C4703A;white-space:nowrap;">2026 year energy</p></td>'
            '<td style="vertical-align:middle;padding-top:4px;">'
            '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            '<td width="' + str(yr_pct) + '%" height="5" style="background:#C4703A;'
            'border-radius:3px 0 0 3px;" bgcolor="#C4703A">&nbsp;</td>'
            '<td height="5" style="background:#EAD8CC;border-radius:0 3px 3px 0;" bgcolor="#EAD8CC">&nbsp;</td>'
            '</tr></table></td>'
            '<td width="70" style="vertical-align:middle;text-align:right;padding-left:10px;padding-top:4px;">'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:8px;'
            'font-style:italic;color:#8A7456;">' + yr_tag + '</p></td>'
            '</tr>'
            '<tr><td colspan="3" style="padding:3px 0 0;">'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:10px;'
            'font-style:italic;color:#8A7456;">' + yr_note + '</p>'
            '</td></tr>'
            '</table></td></tr>'
        )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0">'
        + legend + rows +
        '</table>'
    )


def _score_tips(text: str) -> list:
    tl = text.lower()
    scored = []
    for tip in TIP_ICONS_EMAIL:
        score = sum(1 for w in tip["words"] if w in tl)
        scored.append((score, tip))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:3]]


def _featured_tips_email_html(reading_text: str) -> str:
    tips = _score_tips(reading_text)
    if not tips:
        return ""
    cells = ""
    for tip in tips:
        col = tip["col"]
        svg = tip["svg"].replace("COL", col)
        cells += (
            '<td width="33%" style="padding:0 16px;text-align:center;vertical-align:top;">'
            + svg
            + '<p style="margin:8px 0 4px;font-family:Raleway,Arial,sans-serif;font-size:8px;'
            'font-weight:700;letter-spacing:0.22em;text-transform:uppercase;color:' + col + ';">'
            + tip["label"] + '</p>'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:11px;'
            'font-style:italic;font-weight:300;color:#6B5740;line-height:1.5;">'
            + tip["tagline"] + '</p>'
            '</td>'
        )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        + cells +
        '</tr></table>'
    )


def _tip_icon_svg(tag: str, col: str) -> str:
    fill = 'fill="' + col + '"'
    paths = {
        "NOURISH": '<path ' + fill + ' d="M17 8C8 10 5.9 16.2 3.8 22l1.4.6c1.8-4.6 2.8-5.6 6.8-5.6 4 0 7-2 7-7 0-1-.5-3-2-4z"/>',
        "MOVE":    '<circle ' + fill + ' cx="13.5" cy="4.5" r="2.5"/><path ' + fill + ' d="M10 8.9L7 23h2l2-8 2 2V23h2v-7.5l-2-2 .6-3C15 12 17 13 19 13v-2c-2 0-3.5-1-4.3-2.4l-1-1.6c-.4-.6-1-1-1.7-1L6 8V13h2V9.6l2-.7z"/>',
        "REST":    '<path ' + fill + ' d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
        "MIND":    '<path ' + fill + ' d="M12 2a5 5 0 1 1 0 10A5 5 0 0 1 12 2zm0 12c5.3 0 8 2.7 8 4v2H4v-2c0-1.3 2.7-4 8-4z"/>',
        "SEASONS": '<circle ' + fill + ' cx="12" cy="12" r="4"/><path ' + fill + ' d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
    }
    path = paths.get(tag, paths["MIND"])
    return '<svg width="22" height="22" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">' + path + '</svg>'


def _parse_reading_v2(text: str) -> tuple:
    GRN  = "#3D5A4C"
    BR   = "#2C1A0E"
    CR   = "#F0E6D3"

    body_html       = ""
    tips_html       = ""
    conclusion_html = ""

    parts = re.split(r'\n(#{1,3} [^\n]+)\n', "\n" + text.strip())

    current_heading = None

    for part in parts:
        part_stripped = part.strip()
        if not part_stripped:
            continue

        heading_match = re.match(r'^(#{1,3}) (.+)$', part_stripped)
        if heading_match:
            current_heading = heading_match.group(2).strip()
            continue

        if current_heading is None:
            continue

        heading_lower = current_heading.lower()

        if "tip" in heading_lower or "wellness" in heading_lower:
            lines = part_stripped.split("\n")
            tip_lines = []
            remainder_lines = []
            for line in lines:
                if re.match(r'^\[(\w+)\]', line.strip()):
                    tip_lines.append(line.strip())
                elif tip_lines and line.strip():
                    remainder_lines.append(line.strip())
            if tip_lines:
                tips_html = _build_tips_html(tip_lines)
            if remainder_lines:
                conclusion_text = " ".join(remainder_lines)
                conclusion_html = _render_conclusion_html(conclusion_text, GRN, BR, CR)

        elif part_stripped and current_heading:
            body_html += _render_section_html(current_heading, part_stripped, GRN, BR)

    if not conclusion_html:
        chunks = re.split(r'\n\n+', text.strip())
        last = chunks[-1].strip()
        if last and not re.match(r'^\[', last) and not re.match(r'^#', last):
            conclusion_html = _render_conclusion_html(last, GRN, BR, CR)

    return body_html, tips_html, conclusion_html


def _render_section_html(heading: str, content: str, GRN: str, BR: str) -> str:
    heading_html = (
        '<tr><td style="padding:28px 0 12px;">'
        '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:10px;'
        'font-weight:700;letter-spacing:0.25em;text-transform:uppercase;color:' + GRN + ';">'
        + heading + '</p>'
        '<div style="width:24px;height:1px;background:' + GRN + ';margin-top:8px;opacity:0.6;"></div>'
        '</td></tr>'
    )
    paras = [p.strip() for p in re.split(r'\n\n+', content) if p.strip()]
    para_html = ""
    for p in paras:
        text = p.replace("\n", "<br>")
        para_html += (
            '<tr><td style="padding:0 0 14px;">'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:15px;'
            'line-height:1.85;color:' + BR + ';">' + text + '</p>'
            '</td></tr>'
        )
    return heading_html + para_html


def _build_tips_html(tip_lines: list) -> str:
    html = '<tr><td style="padding:28px 0 0;"><table width="100%" cellpadding="0" cellspacing="0">'
    for line in tip_lines:
        m = re.match(r'^\[(\w+)\]\s*(.+)', line, re.DOTALL)
        if not m:
            continue
        tag  = m.group(1).upper()
        text = m.group(2).strip().replace("\n", " ")
        meta = TIP_META.get(tag, TIP_META["MIND"])
        col  = meta["col"]
        lbl  = meta["label"]
        icon = _tip_icon_svg(tag, col)
        html += (
            '<tr><td style="padding:0 0 10px;">'
            '<table width="100%" cellpadding="0" cellspacing="0" style="'
            'background:#FAF3E4;border-left:3px solid ' + col + ';">'
            '<tr>'
            '<td width="48" style="padding:16px 0 16px 16px;vertical-align:top;">'
            + icon +
            '</td>'
            '<td style="padding:14px 18px 14px 12px;vertical-align:top;">'
            '<p style="margin:0 0 4px;font-family:Raleway,Arial,sans-serif;font-size:8px;'
            'font-weight:700;letter-spacing:0.2em;text-transform:uppercase;color:' + col + ';">'
            + lbl + '</p>'
            '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:14px;'
            'line-height:1.7;color:#2C1A0E;">' + text + '</p>'
            '</td></tr></table></td></tr>'
        )
    html += '</table></td></tr>'
    return html


def _render_conclusion_html(text: str, GRN: str, BR: str, CR: str) -> str:
    text = text.replace("\n", "<br>")
    return (
        '<tr><td style="padding:28px 0 0;">'
        '<div style="background:' + CR + ';border-left:2px solid ' + GRN + ';padding:24px 28px;">'
        '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:15px;'
        'font-style:italic;line-height:1.9;color:' + BR + ';">' + text + '</p>'
        '</div></td></tr>'
    )


def _protocol_overview_html(principle_obj) -> str:
    """
    Build the ear seed treatment overview section for the email.
    Shows the treatment principle and a brief patient-friendly description.
    The full technical protocol (point list, ear, metal) is visible only
    in the practitioner dashboard.
    """
    CR  = "#FAF3E4"
    BR  = "#2A1F10"
    GRN = "#4D5D53"
    BRL = "#8A7456"
    BDR = "#D8CCBA"

    # Build a short, readable summary of the imbalance for the patient
    deficient = principle_obj.deficient  # list of element names
    excess    = principle_obj.excess
    principle = principle_obj.principle  # e.g. "Nourish Water · Tonify Metal"

    parts = []
    if deficient:
        d_str = " and ".join(deficient)
        parts.append(
            f"Your {d_str} element{'s' if len(deficient) > 1 else ''} "
            f"{'are' if len(deficient) > 1 else 'is'} below strength — "
            f"the treatment prioritises nourishing {'these foundations' if len(deficient) > 1 else 'this foundation'}."
        )
    if excess:
        e_str = " and ".join(excess)
        parts.append(
            f"Your {e_str} element{'s' if len(excess) > 1 else ''} "
            f"{'are' if len(excess) > 1 else 'is'} dominant — "
            f"the treatment helps regulate and restore flow."
        )
    if not deficient and not excess:
        parts.append(
            "Your constitution is well balanced — the protocol supports homeostasis "
            "and the harmonious flow already present in your chart."
        )

    patient_desc = " ".join(parts)

    return (
        '<tr><td style="padding:40px 48px 36px;background:' + CR + ';border-bottom:1px solid ' + BDR + ';">'
        '<p style="margin:0 0 4px;font-family:Raleway,Arial,sans-serif;font-size:9px;'
        'letter-spacing:0.28em;text-transform:uppercase;color:' + BRL + ';">Treatment</p>'
        '<p style="margin:0 0 8px;font-family:Raleway,Arial,sans-serif;font-size:20px;'
        'font-weight:300;letter-spacing:0.14em;text-transform:uppercase;color:' + BR + ';">'
        'Your Ear Seed Protocol</p>'
        '<div style="width:28px;height:1px;background:' + GRN + ';opacity:0.6;margin-bottom:20px;"></div>'

        # Principle pill
        '<p style="margin:0 0 18px;font-family:Raleway,Arial,sans-serif;font-size:11px;'
        'font-weight:700;letter-spacing:0.22em;text-transform:uppercase;color:' + GRN + ';">'
        + principle + '</p>'

        # Patient-friendly description
        '<p style="margin:0 0 20px;font-family:Raleway,Arial,sans-serif;font-size:14px;'
        'font-weight:300;line-height:1.8;color:#6B5740;">'
        + patient_desc + '</p>'

        # What ear seeds are — one sentence for the uninitiated
        '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:12px;'
        'font-weight:300;line-height:1.7;color:' + BRL + ';font-style:italic;">'
        'Ear seeds are tiny pellets placed on specific auricular points to support your body\'s own '
        'regulatory processes — non-invasive, gentle, and worn for several days. '
        'Your practitioner will apply them at your next visit.</p>'

        '</td></tr>'
    )


def _protocol_fallback_html() -> str:
    """Fallback ear seed section shown when protocol generation is unavailable."""
    CR  = "#FAF3E4"
    BR  = "#2A1F10"
    GRN = "#4D5D53"
    BRL = "#8A7456"
    BDR = "#D8CCBA"
    return (
        '<tr><td style="padding:40px 48px 36px;background:' + CR + ';border-bottom:1px solid ' + BDR + ';">'
        '<p style="margin:0 0 4px;font-family:Raleway,Arial,sans-serif;font-size:9px;'
        'letter-spacing:0.28em;text-transform:uppercase;color:' + BRL + ';">Treatment</p>'
        '<p style="margin:0 0 8px;font-family:Raleway,Arial,sans-serif;font-size:20px;'
        'font-weight:300;letter-spacing:0.14em;text-transform:uppercase;color:' + BR + ';">'
        'Your Ear Seed Protocol</p>'
        '<div style="width:28px;height:1px;background:' + GRN + ';opacity:0.6;margin-bottom:20px;"></div>'
        '<p style="margin:0 0 18px;font-family:Raleway,Arial,sans-serif;font-size:14px;'
        'font-weight:300;line-height:1.8;color:#6B5740;">'
        'Based on your elemental constitution, a personalised auricular ear seed protocol '
        'will be prepared for you at your next treatment.</p>'
        '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:12px;'
        'font-weight:300;line-height:1.7;color:' + BRL + ';font-style:italic;">'
        'Ear seeds are tiny pellets placed on specific auricular points to support your body\'s own '
        'regulatory processes — non-invasive, gentle, and worn for several days. '
        'Your practitioner will apply them at your next visit.</p>'
        '</td></tr>'
    )


def _build_email(
    name: str,
    pillars: dict,
    constitution: dict,
    reading_text: str,
    principle_obj=None,
) -> str:
    zodiac_row     = _zodiac_banner_html(pillars)
    pillar_tbl     = _pillar_cards_html(pillars)
    pillar_prose   = _pillar_prose_html(pillars, "#8A7456")
    year_chart     = _year_chart_email_html(constitution)
    featured_tips  = _featured_tips_email_html(reading_text)
    body_html, tips_html, conclusion_html = _parse_reading_v2(reading_text)
    protocol_section = _protocol_overview_html(principle_obj) if principle_obj else _protocol_fallback_html()

    CR  = "#FAF3E4"
    CRA = "#EDE5D0"
    CRB = "#F0E8D8"
    BR  = "#2A1F10"
    GRN = "#4D5D53"
    BRL = "#8A7456"
    BDR = "#D8CCBA"

    return (
        "<!DOCTYPE html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Your Elemental Constitution Reading, " + name + "</title>"
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Raleway:ital,wght@0,300;0,400;0,500;0,600;1,300;1,400&display=swap" rel="stylesheet">'
        "</head>"
        '<body style="margin:0;padding:0;background-color:' + CRB + ';">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:' + CRB + ';">'
        '<tr><td align="center" style="padding:40px 16px;">'
        '<table role="presentation" width="640" cellpadding="0" cellspacing="0" '
        'style="background-color:' + CR + ';max-width:640px;width:100%;">'

        # ── HEADER ────────────────────────────────────────────
        '<tr><td style="padding:50px 48px 38px;text-align:center;border-bottom:1px solid ' + BDR + ';">'
        '<p style="margin:0 0 14px;font-family:Raleway,Arial,sans-serif;font-size:8px;'
        'letter-spacing:0.32em;text-transform:uppercase;color:' + GRN + ';">'
        'Chinese Astrological Analysis &nbsp;&middot;&nbsp; Bespoke Ear Seed Protocol</p>'
        '<h1 style="margin:0 0 12px;font-family:Raleway,Arial,sans-serif;'
        'font-size:32px;font-weight:600;letter-spacing:0.18em;text-transform:uppercase;color:' + BR + ';line-height:1.2;">'
        'Your Elemental Constitution Reading'
        '</h1>'
        '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:14px;'
        'font-weight:300;letter-spacing:0.06em;color:' + BRL + ';">'
        'Prepared for ' + name + '</p>'
        '</td></tr>'

        # ── ZODIAC IDENTITY BANNER ─────────────────────────────
        + zodiac_row +

        # ── FOUR PILLARS ──────────────────────────────────────
        '<tr><td style="padding:40px 48px 32px;background:' + CR + ';border-bottom:1px solid ' + BDR + ';">'
        '<p style="margin:0 0 4px;font-family:Raleway,Arial,sans-serif;font-size:9px;'
        'letter-spacing:0.28em;text-transform:uppercase;color:' + BRL + ';">Your Chart</p>'
        '<p style="margin:0 0 8px;font-family:Raleway,Arial,sans-serif;font-size:20px;'
        'font-weight:300;letter-spacing:0.14em;text-transform:uppercase;color:' + BR + ';">'
        'Your Four Pillars</p>'
        '<div style="width:28px;height:1px;background:' + GRN + ';opacity:0.6;margin-bottom:20px;"></div>'
        '<p style="margin:0 0 22px;font-family:Raleway,Arial,sans-serif;font-size:13px;'
        'font-style:italic;font-weight:300;line-height:1.7;color:#6B5740;">'
        'The Four Pillars are drawn from the year, month, day and hour of your birth — '
        'each one a window into a different layer of who you are.</p>'
        + pillar_tbl +
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        + pillar_prose +
        '</table>'
        '</td></tr>'

        # ── YOUR YEAR AHEAD ───────────────────────────────────
        '<tr><td style="padding:40px 48px 32px;background:' + CR + ';border-bottom:1px solid ' + BDR + ';">'
        '<p style="margin:0 0 4px;font-family:Raleway,Arial,sans-serif;font-size:9px;'
        'letter-spacing:0.28em;text-transform:uppercase;color:' + BRL + ';">2026 · 丙午</p>'
        '<p style="margin:0 0 8px;font-family:Raleway,Arial,sans-serif;font-size:20px;'
        'font-weight:300;letter-spacing:0.14em;text-transform:uppercase;color:' + BR + ';">'
        'Your Year Ahead</p>'
        '<div style="width:28px;height:1px;background:' + GRN + ';opacity:0.6;margin-bottom:20px;"></div>'
        '<p style="margin:0 0 22px;font-family:Raleway,Arial,sans-serif;font-size:13px;'
        'font-style:italic;font-weight:300;line-height:1.7;color:#6B5740;">'
        'How the Fire Horse year of 2026 amplifies, challenges, or nourishes each element in your constitution.</p>'
        + year_chart +
        '</td></tr>'

        # ── YOUR READING ──────────────────────────────────────
        '<tr><td style="padding:40px 48px 32px;background:' + CRA + ';border-bottom:1px solid ' + BDR + ';">'
        '<p style="margin:0 0 4px;font-family:Raleway,Arial,sans-serif;font-size:9px;'
        'letter-spacing:0.28em;text-transform:uppercase;color:' + BRL + ';">Your Reading</p>'
        '<p style="margin:0 0 8px;font-family:Raleway,Arial,sans-serif;font-size:20px;'
        'font-weight:300;letter-spacing:0.14em;text-transform:uppercase;color:' + BR + ';">'
        + name +
        '</p>'
        '<div style="width:28px;height:1px;background:' + GRN + ';opacity:0.6;margin-bottom:20px;"></div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        + body_html
        + tips_html
        + conclusion_html
        + '</table>'
        + ('<tr><td style="padding:32px 0 8px;"><div style="width:100%;height:1px;background:' + BDR + ';"></div></td></tr>'
           if featured_tips else "")
        + ('<tr><td style="padding:24px 0 0;text-align:center;">' + featured_tips + '</td></tr>'
           if featured_tips else "")
        + '</td></tr>'

        # ── EAR SEED PROTOCOL OVERVIEW ─────────────────────────
        + protocol_section

        # ── BOOK CTA ──────────────────────────────────────────
        + '<tr><td style="padding:44px 48px;background:' + CRA + ';text-align:center;border-top:1px solid ' + BDR + ';">'
        + '<p style="margin:0 0 20px;font-family:Raleway,Arial,sans-serif;font-size:15px;'
          'font-style:italic;font-weight:300;color:#6B5740;line-height:1.7;">'
          'Ready to go deeper? Book a treatment and bring your reading to life.</p>'
          '<a href="https://www.ednicholls.com/appointments" style="display:inline-block;'
          'font-family:Raleway,Arial,sans-serif;font-size:11px;font-weight:500;'
          'letter-spacing:0.18em;text-transform:uppercase;color:#F5F0E6;'
          'background:#4D5D53;padding:15px 40px;border-radius:32px;text-decoration:none;">'
          'Book a Treatment</a>'
          '</td></tr>'

        # ── FOOTER ────────────────────────────────────────────
        + '<tr><td style="padding:28px 48px;border-top:1px solid ' + BDR + ';text-align:center;">'
        + '<p style="margin:0 0 6px;font-family:Raleway,Arial,sans-serif;font-size:9px;'
          'letter-spacing:0.2em;text-transform:uppercase;color:' + BRL + ';">'
          'Ed Nicholls Acupuncture &nbsp;&middot;&nbsp; ednicholls.com</p>'
          '<p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:9px;color:' + BRL + ';">'
          'This reading is offered as a complementary wellness guide, not a substitute for medical advice.</p>'
          '</td></tr>'

        + '</table>'
          '</td></tr>'
          '</table>'
          '</body>'
          '</html>'
    )


# ── Google Sheets logger ───────────────────────────────────

def _log_to_sheets(name: str, email: str) -> None:
    url = os.environ.get("GOOGLE_SHEET_URL")
    if not url:
        return
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with httpx.Client(timeout=10) as client:
            client.post(url, json={"name": name, "email": email, "timestamp": timestamp})
        logger.info("Logged to Google Sheets OK")
    except Exception as e:
        logger.warning(f"Google Sheets log failed (non-fatal): {e}")


# ── Core endpoint ──────────────────────────────────────────

@app.post("/reading", response_model=ReadingResponse)
def get_reading(data: ReadingRequest):

    # 1. Validate hour
    hour_known = data.hour is not None
    if hour_known and not (0 <= data.hour <= 23):
        raise HTTPException(status_code=422, detail="Hour must be between 0 and 23.")
    calc_hour = data.hour if hour_known else 12

    # 2. Four Pillars
    try:
        pillars = get_four_pillars(data.year, data.month, data.day, calc_hour)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Pillar calculation error: {e}")

    if not hour_known:
        pillars.pop("Hour", None)

    # 3. Five Element analysis
    counts       = get_element_counts(pillars)
    constitution = interpret_constitution(counts)
    spread       = spread_score(constitution)
    balanced     = is_balanced(constitution)
    sorted_elems = sorted(constitution.items(), key=lambda x: STATE_RANK[x[1]])
    weakest      = sorted_elems[0][0]
    strongest    = sorted_elems[-1][0]

    # 4. Ear seed protocol
    handedness = "left" if str(data.handedness or "right").lower().startswith("l") else "right"
    try:
        principle_obj, protocol = get_protocol(pillars, constitution, handedness)
    except Exception as e:
        logger.error("Protocol error: %s", e)
        principle_obj, protocol = None, None

    # 5. Build Claude prompt
    user_message = build_user_message(
        name         = data.name,
        pillars      = pillars,
        constitution = constitution,
        spread       = spread,
        is_balanced  = balanced,
        weakest      = weakest,
        strongest    = strongest,
        hour_known   = hour_known,
    )

    # 6. Call Claude
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured.")

    logger.info("Calling Claude for %s...", data.name)
    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model      = "claude-opus-4-6",
            max_tokens = 1600,
            system     = SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": user_message}],
        )
        reading_text = message.content[0].text
        logger.info("Claude response received OK")
    except Exception as e:
        logger.error("Claude API error: %s", e)
        raise HTTPException(status_code=502, detail=f"Claude API error: {e}")

    # 7. Build email (includes ear seed protocol overview section)
    html = _build_email(data.name, pillars, constitution, reading_text, principle_obj)

    # 8. Send via Resend
    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        raise HTTPException(status_code=500, detail="RESEND_API_KEY not configured.")

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": "Bearer " + resend_key,
                    "Content-Type":  "application/json",
                    "User-Agent":    "resend-python/2.0",
                    "Accept":        "application/json",
                },
                json={
                    "from":    "Ed Nicholls Acupuncture <readings@readings.ednicholls.com>",
                    "to":      [data.email],
                    "subject": "Your Elemental Constitution Reading, " + data.name,
                    "html":    html,
                },
            )
        if resp.status_code >= 400:
            logger.error("Resend error %s: %s", resp.status_code, resp.text)
            raise HTTPException(
                status_code=502,
                detail="Email send error (" + str(resp.status_code) + "): " + resp.text[:300],
            )
        logger.info("Email sent OK: %s", resp.json())
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resend unexpected error: %s", e)
        raise HTTPException(status_code=502, detail=f"Email send error: {e}")

    # 9. Log to Google Sheets — only if this email is new (non-fatal)
    if not email_exists(data.email):
        _log_to_sheets(data.name, data.email)

    # 9b. Find or create patient record
    patient_id = None
    try:
        patient_id = find_or_create_patient(
            name       = data.name or "",
            email      = data.email,
            year       = data.year,
            month      = data.month,
            day        = data.day,
            handedness = handedness,
        )
    except Exception as e:
        logger.warning("find_or_create_patient failed (non-fatal): %s", e)

    # 10. Save to database — skip if identical email + DOB already recorded
    if principle_obj and protocol and not submission_exists(data.email, data.year, data.month, data.day):
        points_out = []
        for p in protocol.points:
            db = AURICULAR_POINTS.get(p.name, {})
            points_out.append({
                "name":              p.name,
                "ear":               p.ear,
                "metal":             p.metal,
                "intent":            p.intent,
                "action":            p.action,
                "point_type":        p.point_type,
                "note":              p.note,
                "body_point_tonify": db.get("body_point_tonify", ""),
                "body_point_sedate": db.get("body_point_sedate", ""),
            })
        protocol_data = {
            "points":     points_out,
            "left_ear":   protocol.left_ear,
            "right_ear":  protocol.right_ear,
            "bilateral":  protocol.bilateral,
            "handedness": protocol.handedness,
        }
        try:
            save_submission({
                "patient_id":   patient_id,
                "name":         data.name or "",
                "email":        data.email or "",
                "year":         data.year,
                "month":        data.month,
                "day":          data.day,
                "hour":         data.hour,
                "handedness":   handedness,
                "constitution": constitution,
                "pillars":      {k: list(v) for k, v in pillars.items()},
                "principle":    principle_obj.principle,
                "day_master":   principle_obj.day_master,
                "deficient":    principle_obj.deficient,
                "excess":       principle_obj.excess,
                "reading_text": reading_text,
                "protocol":     protocol_data,
            })
        except Exception as e:
            logger.error("Failed to save submission: %s", e)

    return ReadingResponse(
        success      = True,
        message      = "Your reading has been sent to " + data.email,
        name         = data.name,
        pillars_data = {k: list(v) for k, v in pillars.items()},
        constitution = constitution,
        reading_text = reading_text,
    )


# ── Practitioner dashboard API ─────────────────────────────

@app.get("/api/patients")
def api_patients(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    rows = list_submissions()
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
    return JSONResponse(rows)


@app.get("/api/patients/{sub_id}")
def api_patient_detail(sub_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    row = get_submission(sub_id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found.")
    if row.get("created_at"):
        row["created_at"] = row["created_at"].isoformat()
    return JSONResponse(row)


@app.post("/api/patients/{sub_id}/notes")
async def api_save_notes(sub_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    body = await request.json()
    ok = update_notes(sub_id, body.get("notes", ""))
    if not ok:
        raise HTTPException(status_code=500, detail="Could not save notes.")
    return {"ok": True}


# ── Patients (new patient-centric endpoints) ───────────────

@app.get("/api/v2/patients")
def api_list_patients(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    rows = list_patients()
    return JSONResponse(rows)


@app.post("/api/v2/patients")
async def api_create_patient(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    body = await request.json()
    pid = create_patient(body)
    if not pid:
        raise HTTPException(status_code=500, detail="Could not create patient.")
    return JSONResponse({"id": pid})


@app.get("/api/v2/patients/{patient_id}")
def api_get_patient(patient_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    row = get_patient(patient_id)
    if not row:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if row.get("created_at"):
        row["created_at"] = row["created_at"].isoformat()
    return JSONResponse(row)


@app.put("/api/v2/patients/{patient_id}")
async def api_update_patient(patient_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    body = await request.json()
    ok = update_patient(patient_id, body)
    if not ok:
        raise HTTPException(status_code=500, detail="Could not update patient.")
    return {"ok": True}


@app.get("/api/v2/patients/{patient_id}/history")
def api_patient_history(patient_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    history = get_patient_history(patient_id)
    return JSONResponse(history)


# ── Appointments ───────────────────────────────────────────

@app.get("/api/appointments")
def api_list_appointments(request: Request, date: Optional[str] = None):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    rows = list_appointments(date_str=date)
    return JSONResponse(rows)


@app.get("/api/appointments/today")
def api_today_appointments(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    rows = list_today_appointments()
    return JSONResponse(rows)


@app.post("/api/appointments")
async def api_create_appointment(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    body = await request.json()
    appt_id = create_appointment(body)
    if not appt_id:
        raise HTTPException(status_code=500, detail="Could not create appointment.")
    return JSONResponse({"id": appt_id})


@app.get("/api/appointments/{appt_id}")
def api_get_appointment(appt_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    row = get_appointment(appt_id)
    if not row:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return JSONResponse(row)


@app.put("/api/appointments/{appt_id}")
async def api_update_appointment(appt_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    body = await request.json()
    ok = update_appointment(appt_id, body)
    if not ok:
        raise HTTPException(status_code=500, detail="Could not update appointment.")
    return {"ok": True}


@app.patch("/api/appointments/{appt_id}/status")
async def api_appointment_status(appt_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    body = await request.json()
    status = body.get("status", "")
    valid = {"confirmed", "checked_in", "in_treatment", "completed", "cancelled", "no_show"}
    if status not in valid:
        raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {', '.join(valid)}")
    ok = update_appointment_status(appt_id, status)
    if not ok:
        raise HTTPException(status_code=500, detail="Could not update status.")
    return {"ok": True}


# ── Appointment types ──────────────────────────────────────

@app.get("/api/appointment-types")
def api_appointment_types(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    return JSONResponse(list_appointment_types())


# ── Availability ───────────────────────────────────────────

@app.get("/api/availability")
def api_get_availability(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    return JSONResponse(list_availability())


@app.put("/api/availability")
async def api_set_availability(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    body = await request.json()
    slots = body.get("slots", [])
    ok = set_availability(slots)
    if not ok:
        raise HTTPException(status_code=500, detail="Could not save availability.")
    return {"ok": True}


# ── Blocked times ──────────────────────────────────────────

@app.get("/api/blocked-times")
def api_list_blocked_times(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    return JSONResponse(list_blocked_times())


@app.post("/api/blocked-times")
async def api_add_blocked_time(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    body = await request.json()
    bid = add_blocked_time(body)
    if not bid:
        raise HTTPException(status_code=500, detail="Could not add blocked time.")
    return JSONResponse({"id": bid})


@app.delete("/api/blocked-times/{block_id}")
def api_delete_blocked_time(block_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    ok = delete_blocked_time(block_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Could not delete blocked time.")
    return {"ok": True}


# ── Treatment notes ────────────────────────────────────────

@app.get("/api/treatment-notes/{appt_id}")
def api_get_treatment_note(appt_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    note = get_treatment_note(appt_id)
    if not note:
        return JSONResponse({})
    return JSONResponse(note)


@app.post("/api/treatment-notes")
async def api_save_treatment_note(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    body = await request.json()
    nid = save_treatment_note(body)
    if not nid:
        raise HTTPException(status_code=500, detail="Could not save note.")
    return JSONResponse({"id": nid})


# ── Documentation queue ────────────────────────────────────

@app.get("/api/documentation-queue")
def api_documentation_queue(request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    return JSONResponse(list_documentation_queue())


# ── Patient notes ──────────────────────────────────────────

class PatientNoteIn(BaseModel):
    note_date: str
    content: str
    zones: list = []

class PatientNoteUpdate(BaseModel):
    content: str
    zones: list = None

@app.get("/api/v2/patients/{patient_id}/notes")
def api_list_patient_notes(patient_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    return JSONResponse(list_patient_notes(patient_id))

@app.post("/api/v2/patients/{patient_id}/notes")
def api_create_patient_note(patient_id: int, body: PatientNoteIn, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    note = create_patient_note(patient_id, body.note_date, body.content, body.zones)
    if not note:
        raise HTTPException(status_code=500, detail="Could not save note.")
    return JSONResponse(note)

@app.put("/api/v2/patient-notes/{note_id}")
def api_update_patient_note(note_id: int, body: PatientNoteUpdate, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    ok = update_patient_note(note_id, body.content)
    if not ok:
        raise HTTPException(status_code=500, detail="Could not update note.")
    return JSONResponse({"ok": True})

@app.delete("/api/v2/patient-notes/{note_id}")
def api_delete_patient_note(note_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    delete_patient_note(note_id)
    return JSONResponse({"ok": True})


# ── Treatment zone records ──────────────────────────────────

class TreatmentZonesIn(BaseModel):
    record_date: str
    zones: list
    notes: str = ""

@app.get("/api/v2/patients/{patient_id}/zones")
def api_list_zones(patient_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    return JSONResponse(list_treatment_zones(patient_id))

@app.post("/api/v2/patients/{patient_id}/zones")
def api_save_zones(patient_id: int, body: TreatmentZonesIn, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    rec = save_treatment_zones(patient_id, body.record_date, body.zones, body.notes)
    if not rec:
        raise HTTPException(status_code=500, detail="Could not save zones.")
    return JSONResponse(rec)

@app.delete("/api/v2/treatment-zones/{record_id}")
def api_delete_zone_record(record_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    delete_treatment_zone_record(record_id)
    return JSONResponse({"ok": True})


# ── Static assets ──────────────────────────────────────────
# Icons are embedded as base64 so Railway does not need the PNG files on disk.
import base64 as _b64

_ICON_192_B64 = "iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAIAAADdvvtQAAA0A0lEQVR4nO2dZ0AT2Rr3ZwgkhA7Si0rvvYMIiqKAImJd7L13Xb269+7uu83d1bX3jqhYsIsoYKV3BBEBEZXeIUAgbd4PuS8vm5k5TDruze/jmXYm+c/MOc95CowgCCRDhqDISbsDMr5uZAKSIRQyAckQCpmAZAiFTEAyhEImIBlCIROQDKGQCUiGUMgEJEMoZAKSIRQyAckQCpmAZAiFTEAyhEImIBlCIROQDKGQl3YHhgtMFrOLRuuidXX39vT29rJYLG47B+HIwXJkMpmsoABBkLy8/AhNLSWqspqqqlT7O1z4XxQQvY9eWV31ueZzTX1dU0tTc2tLe0d7J62Lr5MoyCvo6uhoaWiZGBqZGBob6RuajhytrqYupj4PW+D/BY9EJpP5qeZzxcfKiqrK8qrKmvpaDocjjgvpjNC2NLWwNLOwMre0NrMkk8niuMqw4p8soJa2lpzCvKz83HcVZfQ+uoSvTlZQsLe2c3dydXd2M9I3lPDVJcY/TUAIgpRXVeQW5ecU5lV9+ijt7vwXQz0DH3dvPw9vSzMLGIal3R1R8s8RUENzY/KrZy/SXzW1NEu7L7joauuO8x87zj/QUM9A2n0RDV+9gFgsVnpu5rPUlwUlhV/RvTjZOk4YO26Ml5+8/Nc9j/mKBUTvoz9KTnzw9FF7Z4fAJ1FUVNTX0dPX1dPX0dMZoU0hU5SoShQKWUGBrKykTFEgQxDUz2T09HT3MxhMJrO7t5vBYDS1tjQ0NTY2N9Y3NTAYDIGvrqmuETFpSuj4ECWqksAnkS5fpYDaOztuPbyT8vp5L72XrwNhGDbSN7SxsLKxsB5pbKKvoyf8xLu9o72hubH6y+fyD+VlleV1jfX8nkFdVS3QN2DmlOka6hpCdkbyfGUC6ujqePA04cHTR339/QQPIZFINhbWdlY21uZWNhZWqiriNQB20rreV74vqyx/W/7ufWU58Z9XUVExcvLUKRPCvi4T5VcjIAaDEf/o7u2Ee/0MQtKhKlK9XD08nd3dnFyUlZTF3T1M2js7MvOyM/Ozi9+VsNlsIoeoqqjOnTYzLHgyiUQSd/dEwlcgIARBUrPTL92IJTK9IpFIHs5uY33GeLl4DB87Hq2nOzs/JyMvK7+4kIiSjA2MFs9Z4OXqIYG+CclwF1BDc+Phs8dKykqH3HOEplaQ39iw4EnaWtoS6JhgtHe0J75IepzyhMjKiaeL+5pFK4bz7UDDWUAIgjx9mXzu2qW+vj7wnnZWNtNDIzxdPL4WGx2DyXydmZqQklhZXQXek0KmzI2cNT00Qk5umPpNDFMBNTY3HT57rLjsLXg3D2e3WVOibCytJdMrkVNW8f7Gg/i8NwXg3Rys7bet2TRCU0syveKL4SighJTEizdiwS8eR1uHeVFzbC1tJNYr8VFWWX71zvWit28A+yhRqasXrAjyHyuxXhFkeAmITqcfvXjydWYaYB8jfcOl3yzycHYT1UWbW1tq62tb2lpb29s6aZ0dXZ1cf6DB668UiqKKsrKKsoqKkrKWhqaOto6uto6+rr6qsoqoupFblH8hLqamvhawz8TA4FULlnM9k4YJw0hAtQ11vxz6o6auBm8HqiJ1dsSMiJBwYcz/CIJ8qaspr6qsqKqo+Pihtr6WuEkJzQhNrVHGo0abjLI2t7SxsBLSEshmsx8lP7565wbAd8B8lOm/Nu7Q1dYV5kIiZLgIKLsg98DpIz29PXg7uDm6rlm0QldbR7Dzt7S1FhQXFpUWF5W+6aLRBO3mEBjqGTjZObg7uTrZOSlSKIKdpL2j/XTs+fTcTLwd1FRVd6zd6mznKGg3RYn0BYQgSNzdm3H3buL1REVZecW8pUF+gnz+W9pa0nIy07IzyqsqJHmnCvIKTnYOY7z9fdy8lKhUAc7wOivtVMxZWk835lYSibT0m4VTJ4YL100RIGUBsdns07HnHj97ireDtbnVttWb9HT4e2OzWKzM/OynL5LfvCuR7g2SyWQvF4+QwGAnO0d+rQztnR1Hzh0HzNFmhEcumBkt3Rm+NAXEYDB+O/In3g8kJyc3d9qsWVOj+PqBOrs6HyQlPH2Z0tnVKaJuigYjfcOQoAkhgRP4eiEhCBL/6M6V29fxfHDH+QduXLZWiuseUhMQvY/+68E/it4VY25VVVHdtnqTq4Mz8RM2tTTff/Lw6csUgotlcnJySlSqgrxCP4PB76r+wBlUVVTZbFZff/9AFAcYqiJ1QsC4GeGRmhqaxC9UUla678QBPK8VTxf3Heu2KJIViZ9QhEhHQH39/T/t/7X4Pbad0GyU6b82bCc+0eikdd24H5/4/OmQ/yIMww429t5unraWNiMNjQcWy/oZ/V/qakvflxaUFBWUFAF+ExsLKzcnV3srOxNDIzVVNe5XCUGQusb6io8fMvOy8oryGUwmuBsUMiV8QujMKZHEV3lb2lp/Obi36nM15lZXe+c9m3dKZe1PCgKi99F/PrAXz8rs4uC8c902gu95BoNxN/HBncf3eulD+MzLyckFB4yLCps2pC9pfWPDg6SExOdPB696wjAc4O0/O2KGiaEx+PAuGu1u4v1HyY+HtA6oKqvMnDI9fGKogjwhu05ff//+kwezC3Ixt7o6OO/csF1JUZABuzBIWkAMJvOnv34rKsW2uoYEBq9euILgF72krPTEpdNgyxuX0SajNixdY2FqTryf1V8+HbtwsryqEoIgdTX1rSs3uPDzPW1sbjpy/kTxu5Ih9zTQ0185f5mbowuR03I4nFOXzyY+T8Lc6univnvjtxIeD0lUQCwW64/jBzLzsjC3zpwStWDmN0TO00nrOn/14ouM10R29vPw2bxyPYXMt1WGzWbHxl+r+Phh26qNfA1ZuCAIEnPzyu2Ee0R2HuszZnn0YiLukQiCXL51Nf7RXcytgb4BW1ZukOS8TKICOnX57KPkRMxNM8IjF86aR+QkhSVFh84eb+toI7LzeP/AjcvXSXGV/u7j+xeuXyayp7qa+tpFK33cvYjsHP/obszNK5ibIidPXfrNIj66KBySE1D8o7uXbsRibpoXNXd2xIwhz8BgMC7eiE1ISSTYZw9nN8m/0tFcunHldsJdgjtPDAxeHr2EiBX7QVLCuasXMX+Kpd8sipw8la9OCoyE3nXZBbmXb13F3LRw1jwi6mlpa9n92/ePkh8TVI+GusaGpWukrh4IghbOinawsSO4c9LLlG0/7PxSi7sgOMDUiWELZ0Zjbrp0I7YQuLYvQiQhoLrG+gOnj2CawqLCps0IjxzyDG9Ki7d8/23Fx0riF10xb8kwCXKAYXjDsrXEl9Br6mu//Xl3Bs5IcTBR4ZFzps1Et7PZ7D+O7a9tqOOvowIhdgHR6fRfD/+BuUoa5BtAZNzzKDnxh/2/8LUCamVm4e/py0cvxYy+jt6koInE9++l038/up/IADx6+hzMM3f39Px6+I9+IRwNCCJ2AZ28fPZzzRd0u6Otw/pla8DDWwRBrt25cTr2HMGQhgHCJ4YNN/fW6WHT+JocIQhy6Ubs0Qsnh7z3VQuWYZoYvtTWHL1wkr9e8o94BfT0ZcrztJfodhND4z2bvgUb0Nhs9pHzJ+Lu3eT3okpUqp+HD79HiZsRmlqOtg78HpX0MuXP4wfAFnYSibRz3daRRiboTS8zXie9TOH3onwhRgHVNdafvXoB3a5Epf5rww4q0GbKZrP/PHEg5fVzAa7rYGM/rHz2BvB29RTgqIy8rN+O7AMvjyhRlb7bvEtFGWNh5Ny1i43NTQJclyDiEhCHw/nr1GG0XzN3RGlkAMqXw2az/zp1OCN36FEkJrYWw9TH3sLUTLADc4vyfj30O5MF0pCeju7mFRvQH+5eOv3A6cNiSqgFiU9AD5ISyj9UoNtnTY0Cf184HM6hs8dSs9MFvrSejp7Ax4oVE0OMrwxBCkqK9p88BNaBp4v7tElT0O2l5WUPkxIEvjQYsQioqaX5yu04dLuFqfncabPAx56/duklsTUKPIbJ7B0NVVFRmKF9Rm7WkXPHwWawhbPmYUaqxN6Oa2lrEfjSAMQioJMxZ9AfL0UKZdvqTWDL3oOkhAdie1akDgzDCsJlA3qW9vL6vVuAHUgk0paVG9CG7L6+vtOx58Wx6iB6Ab3OTMstyke3L5m7COxKkV2Qc/7aJeE7wGAKnrBHrPTSe4d0FRqSuHs3wTZGPR3d+TMw1qQz87Ix/xchEbGA+vr6MNcOne2dJgVNABxY21CHZ63ml3r+M/RIhuZWEXxEEAQ5eOZo9ZdPgH2mTAyzsbBCt5+/domg5yRxRCyg+IS76G+tgrzCqvnLAJ9/BpO578TBIZ3C1FXV7KxsnO2dnO2djA2M8L6GRDyEpEJZZTlmOwzD+jp6NhZWbo6udlY2gFvj0tfXt/foPkDsGAzDG5etQ9syahvqRD5CEGWCvvbOjgdPMfo3IzwSPG8/FXMGL6MqN6rBx93Lxd6JJzcUm82u+vwxPSczNTt9cOYXIqk8pEJx2d/8y8hk8lifMd6unnZWtjwmnH5Gf3lVZW5hXkrqC1o3xhpOfWPD6djzm5avw7uWkYHhzClRV+9c52m/9eB2SGCwCBMmidKd40JczJ3H93kaDfT0D//8F8Cyl56b+fvR/eh2MpkcFjw5cvJUzaFmVWw2OyX1RdzdG63tbRAEwTB8Yu9hAz19vm9AnPTSe5duWc19bShSKDOnRE0eHzJkZDSTxUx+9fzqnTjMpcAtqzYG+QbgHctgMNbs2oT+IMyNnBU9fQ7/d4CNyD5hbR1tCSlP0O0LZ80DqKeLRjt1+Ry63crM4uCPfy6Zs2BI9UAQRCKRQgKDT/x+eMrEMBiGEQRJfI4baCYtkl8946rH08X9yC8HZk2NIhJXryCvEDo+5MTeI5hCORVzlvvMYEImk6Onz0a333/ysLsHNwKYX0QmoOv34tHxNJamFr7u3oCjTsac6UBFq0wKmrh3z8/grx4aCpmyYt6Sras2KsgrJDx70tQiRvs9v/Qz+u8/fQRB0JyImXs27eQ3QFtFWXnLqo3L5y3hWY7tpfeeu3oRcOD4MUFmI0fzNPbS6Q+ePuKrAwBEIyBad/fztBfo9oWzogFj5+yCnLScDJ7GGeGRaxYR9atHM9ZnzKYV65hM5lngLythbj643dzaEhU2LTpqjsC2xKkTwzYsW8tzeFpOBmBWD8PwPKwp/cOkhCHTdhFENAJKSElER7E42zk64ScAYLKYF+J4J/y+Ht4LZoI0R4QAb/9vImdn5ec8w3IEkDyfa7/ceXzf08WdoNM3gPH+gQtQXohnr1wAxFJ6OLs52tjzNNJ6upNeiWaVXgQCGng/8zBzynTAUbcT7vGkVDY2MMJcDhSA2REzgnwDzsSeF4npRRi4FgpNdY1NIvLtjwqb5u7kOrilpa31XuJDwCFzIjGWjx4lE3UtByMCAaVmpaOnmlZmFoDXT0dnx21UYMqy6EUCp0ThAYbhNYtXaWlo/nH8L36d0UTLxesxdQ31u9ZvF1V+ahiGVy9cweNKdTvhHiBdv6ONPdquWNdYP2RqPSKIQECYX4qosEjAIbce3eX55Lk4OLs5uuLtLwDcpbePn6rBi0diJbcoPyHlybLoxXzFNA6JrrZOyN/N+vQ++o37oNuchhWk8Rhr1swvwgqopr62BBWkrK2lDchx3NbR9uQFb2wleKFDMMxGmS6cFX3jQTw4/aCYaG1vO3T2qJ+nT+j4EJGfPHLyFJ4PYtLLZy1trXj7+7h5oad++SWFACsAQYQV0NMXyehPaUhQMGAadevhHZ4CJSrKyp4u7kL2BJOpIeHuTq5/nT4iTEEWAeCuWJEVKGsWrRTH+XW1dS3NLAa3MFlMtBV3ADk5uQkB43ka2Wx28qtnQvZEKAFxOJyXmak8jSQSKWRsMN4h3T09aEdVW0sbggkG+AWG4Y3L18nBcofPDuFJI1pu3I9/+750+5pNIszCyYOnM+8j9+R5EiArUkggxlOd8vq5kD+LUAIqflfS3tHO0+hs5wiIJE98/gQ94TcbaSpMN8Coq6ptXb2x8G3RvcQH4rvKYMqrKq/fvzV76gyxZiEeZTySp4XJYj55kYy3v6aGpou9E09jQ3Pjh6GSnYMRSkCYiSABAVlsNhszmx2/Gez4xdHGfnpoRMytq+8/YK+Hi5Ce3p4/jx+wsbAiEm4rDMaGRujGx8+eAmadAd7+6MacwjxhuiG4gBAEyczL5mkkkUje+OkB8t7kYw70JFC1b17UXIvR5n+dPDyk04iQnIw529Pbs3mF2FNkYOYbaetow0sgBEGQj7sXOgmVkF5mgt9kxccP6JGpq4ML4KufkvoCuxPiT0dCIpG2rtrQ1d11TJyxdk9eJL/KTF2/dLXA6YiJg2eWTEnFjYWiKlLRI6fK6g/CzMUE/+fy3mAo198TN+Kik9aFJ3b0eqo40NfVX7dkdWp2Omaso/DUNdafj7s0KWiiZMIau3EyAOcXF6IHpgOg08cgCCKMRVFwARUUF/K0kEgkQHqb1Kx0PH/K2gYJOaGO8fIb7x948vLZ2noRJx5gsph/HvtLZ4T2smgJ5eapqcN2vGSz2YCgKFcHZ/T7PqcQ96s3JAIKqJdO56Z/G4ylmQWgeGxWPu+AaYBPNSAPX9GycsEyLXXNv04dEq138MXrsTX1tTvWbBEgFZpgfK7DyDjAJQM1Nh1AVUUVbRYvfvdW4F9DQAGVVb5HO8CjV30H6O7pKXmP62n6/kMFkQJsIoGqSN22elN1zWfMyDUeSspKX6EMXWiyC3IfJT9eMnchemotPrLzc/A2vasoA4wK3BxceFp66b3VNZ8F64aAAsKMOrWzssXbP/dNPmB6yWaz0RM68WFhaj4vau6dx/cLSorAe8bcvHI69jx4n7aOtiPnj7s7uYWOnyS6Pg5BU0sTXspfCII4HE5hKXYCbgiCMLNvV1Rh/KFEEFBA6FxPJBLJFr/w2xv8++HypRb3hSwOpodGuNg7HTpzFJzQns1m0bppgGAuBEEOnD5KViBvXrlekgllqj5Vg3d4i/++Nzc1Q9v932O9EYggsIA+8LRYjDYHJNwofodbe5BEIs2LmivJvJAQBMEwvGnFeg6CHDxzDGDL/+8G/B1uPrhdUvZ266qN4luywMTH3Wv76s2A4Iq3+KEpCvIKpqNG8zRiflKIIIiAmltb0J9YgMdCY3MTnoeymqrqT9/+Z3bEDMlXDOE6eRWUFGJ6w/0NnFdLxcfKuHs3Z0fMsLcmmgJRhAT4+P/14+/mo7EzftQ21AES2Vqb87oH1TbUCWZiFeRv+4j19QWMH99VvsdsV6JSv9+6Ryq/Phd3J9eIkPDLN6/iRaUB6KX3/nn8gLW55eyp4l2yAKCvo/fzzu/xHt2379/hHWiJOgRBkNoGQQIyBREQZuinGeqtOMBHrL9HQV5hz6ZdovW0EoAFs6JHGpvsO3kQszIBgh9qffzi6e6eni0rN0o3EawSVenfm3dpaWDU4wUICPNp/4JjWAIjiIDQ6T9hGMZMscYFc733m+mziSe/FR8K8go71mxpa28/cwVjtsXmcCCsL1jSy5TXWWkblq6RwJLFkGioa2xdheFLDoifN9Q3RI8ZAMVGAQgioC+oK+mM0AYY0NATTrNRptNDIwS4tDgw0NNfPm9J8qtn6GK/mKaH2vq6s1cvhI4P8fUAhbxJEkdbh0BU5GEdfpIJsoKC7ghe6X8mkJwajSACakWtqOvr4sYRd3Z1onP8zp02S7p19niYEDAu0Dfg+KXTg9MJ0rppff19EAQNXlpispj7Th7U1dZdOlei08YhWTAzmudj2tnVCaiDhvYGESwDFd//IoIgaJcMfXyHnvrGBp4WAz19gMe0tFi9cLmqisr+k4do3bTE50k7f96zYMMy7p2u3LF+43fbbj2800nrunTjSk1dzdZVG6VSnAuAttYIdBBwHf4iI9oHSzAB8e2I09HZgTacYA7iuNQ38QrI39NvuCVxhiBIiaq0ddWmnT/vmb9+KQRBtpY2c6fN0tPRZXM4Tc1NRaVvYuOvcas1LJ6zwBQVLzwcCPQdw7OMWtdYjzdN0VDT4GmhdXez2Wx+5wR8C6gH662ooY5bp6i1nfd1RbA2lsihddM+19ZYmJrhDde41vDRJqPWL11tafo3l/XoqDkfP1efu3qxuOwtIFiRm3FmhKYW4IkSH462DiQSafC4DfAGQmetQBCko6tzhCZ/PedbQJjOR4C8lp1/z0sCw7C0Ht+EZ0+u3r5OJpPdHV293Tw9XTwGZ+UpKSs9EXPG2d7pu03YtSNNR47+aef35+Ni7j95aKRvGD5h8sCmfkZ/3puCzLzsnMK8Xnqvt5vn7o3fSuKW/g5VkWpsYPRp0LIoXtVwCIIw/da7e7rFLiDMdX8N/EppXX9fbNJQUxesjrrwTJ0YxmKxXmWmZeRlZeRlkUgkJ1sHH3dvbzdPFWXlI+eOa2uN2LV+O2BwA8Pwsm8WtbW3XboR6+3mqaKsUlBcmJaTkV2Qy41OV6IqBfkGTJPeBNNQ32CwgPrws5ipq6mhG3t6+S4+zLeA2GwMASlScEsG8zwEKpJdMxqMElVpXtTceVFzKz9+SM1OT8vJ5JbYPRlzRmeETlNL055NO4mIe+X8pXlvCnb8n920bho3+beaqupY3zG+7t7Odo4S8O8GwLMkR8dPwUFWwHhOmPznJ+X7bplYbyDAU8uTOGI4FPCyMDW3MDVfPGfBh+qqzPzstJyM2vo6XW0dgsGN6mrqY7z9kl6mUMiUsT5jxvqMcXN0GQ73BaG8ywEComClIRAgiaxoBETBFxCT+bf9Mes+SQvz0Wbmo82ip8+Zu2ahm6ML8bmho4190suU77ftluJCHiY8U5y+ftxPGAXrDSRAhmS+7UBsLAEB4kpZf//kdXR2SjddBib9/f3Ey9RDEKStpQ0Bn29pwWPjBWSRUsD+hPH9BuJbQPymcuZ5qJksJnolRLogCMKvXYrDGXbPAARBHA6n+svfPFNJJNwvDBvrFgRYHuD7AMzfGvO7xkUelWET4CwnFeTk5NRU1fjKqchd8RgOK6mDqf7yiWftApBvCXM2LcAMgP8FKSwBsbCmZlzQXzfMgGipwGKxcovyDpw63EXryi8uJP5y5QYIXLkdl5adgekHIhXScnh/WEVF3NkxtoD4nwrwrTjMnL2AoBAq6h7evn9XU19rbIAR2i0ZmCxmYcmb9JyMrILcnt4ePR1de2u74nclWQU54JyyXDo6O9JyMixMzRkM5l+nD8vJybk5uvi6e3u6uIswgTe/sNnsF+m8AZMA8wrmXyYnJ34BYVpKAN6Qaqq8BisEQeIf3tm0Yj2/lxaSfkZ//pvCzLys7MK8XnqvkYFhWPAkH3dvi9FmTBZzw56t56/FuNg7gUspQhB05soFhINsXbXRSN+Q1tOdmZuVmpNx6OwxCIJsLKz8PH3HePkRSW8tWhKfJ6EXuQFvoO5eDCO1nBzfa5R8CwjzIeui4cY2YBqpn6e/mjB2vGTmwP2M/jelJek5GRl52fQ++miTURGTwv08fAZ75SnIK2xYuvbff/y49+j+PRu/xTNrcQvhpmanL56zwEjfEIIgVWWViYHBEwODO2ldmXnZmXlZF+Jizl+75Ghj7+Pu5evuDch0I0K6aLQbD+LR7YA3UGcXRiCehjrfveVbQGoqGCZwzN5wwUwuiSDI0fMn9//wu/iWNWg93dn5ORl5WYUlb5gspoWp+cwp0/08ffBKTtlb2y6cGX3h+uUdP+1et3iVlbklzw6fa7+cu3apsKRovH8g2htOXVVtUtCESUETunt6sgtyMvKyLsTFnI49bz7K1MPFPdA3AFzqSkiOXjiBGUmoO0Ib75AurBIcAvwdfAtIHeuNAogrxXuZ1zXW/3ro9++37xFtbjJaNy23KD89NzO/uJDNZttYWC+YFe3r7k1kxtTPYEAQVP3l046fdluZW7o6OGtracuTSI3NTcVlb0vL33H9WLq6cVcoIQhSUVYePyZo/Jggeh89pzAvIzfrTsL9uLs3RxqZ+Hn6BHj7i3zwd/nW1SycKFV9Xdzqn11Yf5mWBN5AKsrKZAUFHpt3G346CCP836u47O3eI/u2rd4s/HuoubUlMy8rIy+7tPwdDMMONvZL5y7y9fAi7lZR8bHyxoN4JSr1xx3/+VBd9SLj1c0HtwfmZXo6utNDpwV4+x08czS3KO9RcuLg1XhMqIpU7kIHd60+Izfr/pOHcXdvjjYZ5ePuxfMNFQwEQWJuXr2dcBdvBwNd3Nce2qtCkUIRwEtOkJU/LQ2thubGwS0NKK+xAYwNjLgFUDC35hblf/vz7m2rNgnm4/G59ktmXnZWQc6H6ioSieRk67h28SofNy81Vf6SMvfS6ftOHGKxWBuXrbUys7AyswgdH0Lvox85d6Ky+sMf3/0y4K+yY+2WbT/sung9xt7adrTJKCInp5Apfh4+fh4+DCazsKQoIy/rYVJC3N2bRgaGvu4+vh7eFjixXWA6uzoPnT0GyMwCwzAg9RvaVWiE1ggBuiGIgHRGaPMICO23OoAihaKtNQLghPWltmbbj7tCx0+aGR5JZMiJIMj7DxVZ+TmZeVl1jfVkBQUXB+eQRSt83L3VUTM+gpyKOdPQ1DDeP3CwazpVkaqhrqGirDLY28nE0HhZ9OLjF0/tP3lo//d7+XpkyQoKXq4eXq4ebDa7qLQ4Iy8r6WXyrYe3dbV1/Tx9fN28rC2siNjE+xn9j5ITbz64DXB5hiBIS0MT0L36Jl4B6fGzmDOAIAIyMjAs/ntu6MbmJg6Hg2cINzE0BpccYLPZD5MSHj974uPuFeDlb29th36FsFisN+9KsvKzswpy2zvaFSkUd2e36Kg5ns7ugMkqEZJeprzIeG2oZ7BywTKeTZh/56SgCcVlJa8z02JuXlk+b4kAVySRSG6OLm6OLmsXrSyrfJ+Wk/k6M/Xu4/sa6hperh7erh5Odk5oe1s/o7/8Q0V6buaL9Ndg6XABDLbYbPbgEn3/3d/QWIB7EURA6J4xWcz6pgbuzBaNhal5PiobFRo2m52WnZGWnQHDsJG+ob6unpaGJlWRSuvprqmrqf7yicFkUsgUZ3tHPw8fX3dvIXXD5UttzZkrFxTkFbav2Yy2AOF9edcsXPG+svxh8mM3JxdhEuzDMGxraWNrabPsm0Vlle/TsjNSczKevkimkCkjjUwM9Q3UVFQRCGppa2lpa/34uZqvdWhbK9wcsbUNdehTCTa6F0RAJlgxhFXVH/EEhI7EBoMgSE19LU/8q721Xej4EB93L9HO2k5ePtPP6F+1YBlmkDneB0VZSXnrqo179v5w+OzxI78eED6zwv9XUvTikrK3KakvXmWmolOg8AXgZ6/ECvU0wUr7OiSCBGdhDh6rPuOGl9taWgsThmFiaPz9tj2//uvHAG9/0aqnl04vqyyfEDAuLBh7SgVI3GFrabPsm8UdXZ1lFdiR/4IBw7CjrcPmFeuP/3bQ0dZBmPNgFm7mUoUSEAzDo4wJzQl4EOQNpKmuoTNCm2dYgylqLspKysaGRl8ECnwMHT9p6dyFYgrCUqJST/5+RAff2gYmfMJkH3cvfr3QCaKvq//Tt/9JSEk8d+2SAB5Uo4xHAtINov8sA119nsK/BBEwPNTKjNdQW1FVCbhPQPY7PGAYXjJnweqFy8UawieweriIST1cYBgOnxD6/dbdAoz2AMm+mCwmOhsJT+UN4ggoIPT16H30ymrerFMDuDu58XuJ+TPmRg6b+Hkp4mzvtHvDDn59rl1RiRAHKKssx6puK2CaFJG9gSAIelNagm7k4mzvyNdj5OvhPXNKlCA9+yfibO+ErnQJQFFRETMRIpfidxh/k4WpZN9AFqbm6GeiGFU4bAAFeQUnW9wChjyoqaquW7JasI79U5keGmFvjZvDlAcvFw/Ad78Ila+STCYLZg2HBBaQIoWC/sqWvn8HcAzydvMkePLZETMlnHLwq2B59BKCk1lA3hlaT3cFKsG3g7WdwANNwXOseLrwZthgsph5+JU7xnj6EvmKqSgrhwTilhuTOJIrMTYkZqNMnVH1mtBQyBSAbTMrPwc913EVIluB4ALyQgkIgqBM/HT0ioqKPq5Dv4SC/AIlluydAMMri0jwmKAh9/Fy8wD40mPWC/Bw5nuKM4DgAjIyMESbnvPeFACiG4P8A4c8rffwSx00fPB0cR8y8iYcxygKQVBfX18hqnzsKOOReEsIRBAqTRg6FpjeR88uwE3B72znCK4tRyKRbCxwDRjSYBh9wiAIoipSAbkoIQgyG2UKKJOYnpfFU60WAg6YiCCUgLzdMGrzpLx+gXsxObkpE0IBJzTQ1R9umb+GG2A3tHDgz5vyGqPErruTUNXWhRKQnZUNOjtiQUkhIFnaxMBgQOyLYD5N/1OoYfmY/3eTqupYrKKWXBqbm9CJf3W1dTFNesQRSkAwDI/zG8vTiCAIXmVCCIKoilTAJGs4DZ+HKYCZbEjgRMD7O+lVCnptePyYQCHTDQqbKnX8mCB0DxKfJQFCDSMmhePdJ2byIRmDwcvooERVipw8Be8oBpOZhCoRD8PwhIBxQvZHWAHp6eiiI2DaOtpeZ/HmXB5AS0MLbyQksaphXy/dWOE4EARFhU3DjKDi8iz1BTrux93Jla+cJJiIIFlzyFiMT9LdxAcAZ5oZ4dMxnQdq6msBR0meYdUZLphlwjQ1NKeGhOMdgiDIw6QEdHvwGGFfP5BIBBToNxYd/FX95RPa5DCAirJyxCSM921fX99nyRYOAzPc0hHT++iYhUrmRMwAGA9zCvPQKXU01DU8RWFyE4GAyAoKmLPH2PhrgCd4+uQIzPz2gLLnMrLyc9ApRIz0DUMCJ+AdgiDItbs30O1TJoRi5sngF9HUGwgLnoR+Aio/fgBELZHJ5FWoKAgIgp6+TOY3h5X4GG6fsNdZvOWYYRhetXA5wFsoPScT7T6mqKgYOj5EJF0SjYBUlFUCfMag26/duQ74D9wcXdAl1ptaml9mvBZJr/5hfKr5nPeGd6168rgQZztcPxkOh4P3+gGMuPlCZBVPoqfPQVtxKqurnqe/Ahy1Yv4S9J1cuR2HdpmTgR4SaGtpL5w1D3BI0qtn6NGPElVpetg0UfVKZAIaoamFGS5+6UYswElIS0Nr3ZJVPI3NrS2XbsSKqmP/DF6kv0KPDlctWApIK9BLp1+7cx3dPm3SFBG6W4my5tL0sGnoJHsdnR23Ht4GHOXr7h2MMmclpDzhqRvyv0xNfS26+vjEwGAvoHvM1dtx7Sjbj6qySmiwKMuTi1JA6qpqkZOnotvvP3kEjulZHr2EJ30OgiAHTx8twjcE/O/Q0tby4/5fePJrm482WzkfYwoywKeazwnPnqDbZ4RHAupSCICIq77NmTYTnYmHyWIePncMMLdSolL3bN7JE8fEZDF/OrBX6gNq6Wa1Lqt4v+P/7OGJY1dVVtm5bhtgEs5ms4+cO4HuuaGewZSJYaLtoYgFRCFT5kXNRbeXV1Xef/IQcKCxgdHmFet5DHdMFvOvU4ePnD9Bw7HfixsEQaR1aQaDceN+/J7ff+Cp3g3D8NbVG8FuVfGP7mKGRS+eu0Dk3jKirzsZ5DcWM4zwyp3rtfW81XoH4+3mOSdiJro9+dWz1Ts3xMZfA2QhEhPdvT1dNEkLqLun5/GzJ2v/tenK7Tj0mvT8GXPB6Rw+1Xy+cR8jX6K3m6c3AZdifsFN/SQMNfW1G7/bhr75UcYj9/3nN8BDgCDI8Uunn75IxtwKw7CtpbW9tZ35KDM9XT0VJSUOgnA4HANdfTGtOez65d/vKsquHr8opvy9nV2d/QwGB+FwOJy6hvqqTx8rqz8UFBfiuQWHBU9atWA54IQMJnPnT7vR62VUReqx3w5qi8HdSiy1iYwNjCInR6AnX59qPl+8Ebty/lK8A2EYXrNwRU9vT1p2BnorgiCl5WWl5WU87fu/3yuO+vMcDufj548QBJVVlgvptocJi8Vas2sT8eozQX5jwQNnCILOXDmPudq6eM58cagHEscnjMucaTMwnS8fJT/OyM0CdUhObvOKDUTiVwYABDQKQ2l5GTcLfUFJkTjOX1L2lrh6vN08Ny5bC37Rvsx4jfnydrJznDxONAsXaMQlIAqZsmPtFsyv1aGzxwZX1UNDVlD4btNO4g99Kr7vkTCkZv/3tPnFuCt6wgC20Q/G2c5x+5ot4PD4TzWfj186jW5XVVHdsnKD+NwKxFi8faSRyUKsiG56H/3ng7+DfcfIZPLujd8SDBiorK4qqywXsJc40Hq6n6f9t3JAbX1dblGeaM/f3tlBsGaIj7vXd5t3gVfOu2i0Xw//iVncaUX0ErGmEBGjgCAImhoSjll9oqml6c9jfwHcXiEIkpeX37FmC9pIjcmV23ECdhGHm/fjB1dRibt7S7Tnj390Fx1hgyZ8Qii4jCsEQQwm85fDv2NOUUOCJgT58zqtixbxCgiG4U0r1mH6TRaXvT109hh4DkgikTYuW7s8evGQb+A3pcXP/t8LQ3gqq6seJj8e3FLxsfIF4S/OkFR/+ZSQkgjeB4bhudNmrZy/FHzvCIIcOXccM0ua2WhTwHxFVIhXQBAEKVGVdm/6FtNf7lVm6qnL54Y8w9SQ8B1rtwwZsHEm9nxdI26ZdOL09PbsP3EQbcY9EXNGJKXyenp79p3EOP9gFCmULSs3fDN99pBnOx8X8yozFd1OVaRuX70Zs7KuaBG7gCAIMhs5Gq82z+NnTzAdVnjw9/Td/8NecExdL733h30/t+PnzCcCg8H45dAfmELs6+v7/eh+zJIUxGGz2b8f3Q9eGTQxNP7j378OzliNx5XbcZj2fTk5ue1rNkumoJZYDImYxMZfw7SQQhD0TeTsuZGzhjwDg8E4c+X805cpgH30dfX/vWWXYL9dT2/PL4f+ABdU1NfR+377HsEqp/T19R08czQjD2TFGOcfuGbRCiLxcbce3rl86yrmphXzl0ydiOtjL1okJyAEQY5cOJH8EiO6FoKgGeGRYN+oAV6kvzp75QJPOfrBqKqorl28Eu3rCOb9h/K/Th7mycCPiZqq6sp5ywJ8cGNAMaltqNt7ZB8gZEBFWXntolX+Xr5Ezhb/6G7MzSuYm6aEhK2cJ/ahzwCSExAEQQwGY+/Rfbk4OYSmh0Ysmj2fiMWio7PjdOz5tBwMa/UAvu7e0VFzwKkIuLR3tF+7ezPpVQpfvtguDs6LZs0zG2U65J6dtK74h3ceP38KmHZ5urivWbSSyHwbQZALcTH3cFamx3j7bV+9WYDauQIjUQFBENTP6P9x/y8lZdifiUDfgA3L1hBMBp2Wk3EhLgZQRAGGYXcntwBvP1dHF3QZjb6+voK3RWk5GRm5WWCDAgALU/MJAeMcrO2NDY14pN/X359fXJBTkJuelwUov22kb7hq4XKAX/NgWCzW8UunU14/x9zq5er5rw3b+U3HKSSSFhAEQd093T/u/+X9hwrMrTaW1t9t2knQ5ZvBYNxJvH/70d0hK99qa2nr6+qqqajBcnBvb29jc1NDc6MIwz+UqEpG+oZKSkoUMrm7p7uppbm1vQ3826qqqM4MjwyfGErwgenu6dl7dB9mikwIgtydXHdv+la0idiJIAUBQRDUS+/9cd/P73DMx8YGRrs2bDchXPujtb3t5oP4pFfPBH6RSBhFCiVi0pTpoRGAXOA8NDQ1/HRgL2ZUIQRBzvZO/968SyqZcaQjIAiCaD20Xw7+jl5a56KoqLhx2Vp/T0IjSi4tbS23E+49fZHCZOGmSJM6KsrKk8eFTJkYxldV3uyC3MNnj+HNGzyc3Xet3yatvEpSExAEQQwG44/jB/AymsEwPD102vwZc/n6qLd3tD95kfzkRTKPI5/U0dPRnRoSPjFgPF/5stlsdmz8tTuP7+P9TQHe/ltXbZTwuGcw0hQQBEEsFutkzBmAacfC1Hzryo1GBvwl8WOz2Zl52UmvUopKi6Ub50pWUPBx9x7nH+jq4MzvknhDc+PB00ffVWC/pCEImjIxdHn0EknOudBIWUAQBCEIcv3+rWt3buD1hEKmLJm7YPK4EAF8Ejq7OlOz019lpb2vLJfknSrIKzja2vu4e43x8hPAmxFBkMTnTy/eiMWbvsnJyS2duxAzQYWEkb6AuGTkZh08c5TehxuC6GBjt2bRSoHN8520roKSooI3BQVv33R24Va5FxJdbV1HW3t3J1c3Rxd0+TqCNLU0Hb94GuDFpkSl7li7VRxOkgIwXAQEQdCXuprfDv+JN9GAIEhBXmHGlMgZ4dOFTCtR21BX/qGi4mNl+YfK2oZaQODskKipqo4yGjnKZJSNhZWdla2QnjcMJvP2o7vxCSBPD2ND413rtxExkEqGYSQgCILofX0nLp0GO07oausumPlNgLe/qLzs2jvaaxrqGpsaWzvaaDRaR1dnVzeNxWL29/dDEMStBUtVpMqRSCrKylrqmpoamloamjojdEyMjAUu84smKz/n/LVL4LWUIN+AdUvXUIZTItvhJSAuCc+enL92CexvZWlqsXjOAgcbO4n1SnwUl72Njb8GrnyoSKEsnrMAr7KiFBmOAoIgqKa+9vDZY0M6qjra2M+aGsWXB/6woqzi/bV7NwuHctq3tbTZsnI9Zj4uqTNMBQRBEIfDufv4wZXbcUMaBq3MLaNCp3m7eUp3QkscDoeTmZ99L/HBkE8IVZE6J2JGZGjEsL214SsgLrX1dccuniohELijraUdEhQcEjiBLyOvhOno7Hie/irxeRKRKFtnO8dNK9ZpawlVlFPcDHcBQRDE4XBSs9Mv37ra2Nw05M7y8vIezm4B3v7gomsShs1m573JT379PLcon0i2Bp0R2ovnLPD39B22L54BvgIBcWGymA+eJty4H99L7yWyP1WR6u3m6e3m6WLvDMjCJFb6+vvz3uRn5+fkFOUTjCEkk8kzwiKjwqd9LUn7vxoBcemi0a7fu5n4PIn4iimJRLKztHFzdHGwsTcfbSbuZSM2m13x8UNJWUlJWenb8ndEYne4UMiUsOBJ0yZN0RJnGJfI+coExKW5teXek4dJL1MAlmtMuMVBbSyszUaaGhsaGRsaCe9Aw2aza+prP36urv7yqerTx/dVFQD3MbxeTQqcMHNq1HAeveHxVQqIS3dPT0JK4oOkBIGXJkgkkr6OnoGegZamppaGprbmCA11DUUKhUpVUqJSecYfvb293T3d3b093T09nV2dza0tjS1Njc2Nza0tAvshqauphwVPCgueLEKDpIT5igXEhcFgPE9/+TI99W156ddyLzAMO9s7TQgY5+PuLZJs31LkqxfQAA1NDSmvX6SkPm9pa5V2X3DRGaEd5Dd2YmCwvo6etPsiGv45AuLC4XDelBbnlxTlFOaCE6JJEkM9Az9PHw9nd1tL6+FWf0NI/mkCGkxdY312fk5OYV5pRZnkc2XKy8vbWFg52zt5u3qONhkl4atLjH+ygAbopdPLKsvKP1RWVld9qP7Q2i4Wb1cYhg31DCzNLKzNLS3NLE1HjpJ8jITk+Z8QEA/tHe0fv1R/qautra+tqa9tamlpbW/l9xWlqKioM0JbW3OEsYGRsaGxsaGRqclozCJo/2z+FwWESReN1t7ZTu/ra21v5Rb75iAcrtWbQlFUIMmTyWSygoKSkpKaipqmusbwWSeRLjIByRCK4b5WJ2OYIxOQDKGQCUiGUMgEJEMoZAKSIRQyAckQCpmAZAiFTEAyhEImIBlCIROQDKGQCUiGUMgEJEMoZAKSIRQyAckQCpmAZAjF/wU3tsjau50THAAAAABJRU5ErkJggg=="
_ICON_512_B64 = "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAIAAAB7GkOtAACaGUlEQVR4nO2dZXgbR9eGdy1bBpmZmZkpcZiZGRpmhqYppU3apg0zp6GGmROHHTMzMzPIMsiW9/uhfqpfx3as1ax2Jc39o5ftamZPbGmf2ZlznoNiGIZAIBAIRPKQIjsACAQCgZADFAAIBAKRUKAAQCAQiIQCBQACgUAkFCgAEAgEIqFAAYBAIBAJBQoABAKBSChQACAQCERCgQIAgUAgEgoUAAgEApFQoABAIBCIhAIFAAKBQCQUKAAQCAQioUABgEAgEAkFCgAEAoFIKFAAIBAIREKBAgCBQCASChQACAQCkVCgAEAgEIiEAgUAAoFAJBQoABAIBCKhQAGAQCAQCQUKAAQCgUgoUAAgEAhEQoECAIFAIBIKFAAIBAKRUKAAQCAQiIQCBQACgUAkFCgAEAgEIqFAAYBAIBAJBQoABAKBSChQACAQCERCgQIAgUAgEgoUAAgEApFQoABAIBCIhAIFAAKBQCQUKAAQCAQioUABgEAgEAkFCgAEAoFIKFAAIBAIREKBAgCBQCASChQACAQCkVCgAEAgEIiEIk12ABAIMNrb2+vq6xqbWKwmFqupqam5ifdfVhOL9zIMwTp/q6igiCCIvJwcjUaTkZGRpcuiCMJgMBAEkZeTp0nReK/U0tA0MjCUl5MX4r8JAiEQKAAQUYLNZpdXVpRVljcwG6pra+oa6quqK6tra4vLSpqam4QWhoK8vJSUlK62rqmhMY0mzVBQMDE0NtDVl5WV1VBTV2QoCi0SCEQQUAzDyI4BAumG6tqakrKS4rLSkrKSotKSssoyZmNjfUM92XF9BSkpKS0NTV1tXUM9fUM9Q0M9fQM9A011DbLjgkC6AQoAhBK0tLbmF+XnFuTn5OfkFuTnFxe0tLSQHRQw5OXkDfT0DXX1jQ2NTY1MzIxNNdTUyQ4KAoECACGJjo6O/KKC1My0tKyMzNyskrJSiXorKispmRmbmRmbmhub2lhY6+nokh0RRBKBAgARHs0tzbkF+amZaSkZqamZaY0s1tfHSAaqKqpWZhaWphb21rZ21nZ0GRmyI4JIBFAAIATSgXVUVFSkZKUnp6ekZ2UUlhTB99tXodPpDtZ21hZWbo6utpbWUlIwVxtCFFAAIOBpaWmJTY6Pio+JTYyvqqkiOxwRRpHBcHVw8XRxd3dyVVVRJTsciLgBBQACjPLKirjk+IjYqLikhLb2NrLDETeMDAy9XT1dHZwdbR1oNNrXB0AgXwMKAEQgMAzLzMkKi4kIj40sLC4iOxyJQElRyd3J1dvV09PVHValQQQBCgAED23tbSnpqRGxUSFRYdW1NWSHI6HISMvY29h5u3r09+mnBjeIIPwDBQDCB2WV5aFR4XGJCanZaeKUpy/qSElJOdo6+Hl4+3r4wAoDSN+BAgD5Oqwm1sfQoDef3mXl5ZAdC6Q3UBR1snMc7D/A38sX7g5BvgoUAEhvJKenvv74JjgylM1mkx0LhA9k6bJ+Ht6D+w9ysXeCiaSQnoACAOmGpuam98GfXrx7VVBcSHYsEIFQV1Uf5B8wctBwXW0dFEXJDgdCLaAAQP6juaUlNz/vXciHT6FBLa2tZIcDAYa0tLSTnWOAj7+fhw9DgUF2OBCqAAUAgiAIUlVT/ep94PuQjxVVlWTHAiEQBXmFYQOGjBg41NjAiOxYIOQDBUDSSc/OePzqWUhUGIfDITsWQpCly8rJyvJORBUZ/65/5eTkaDRpBEHa29tbW1sQBGlrb29tbUUQpJ3T3tLa0trKFuNyNkdb+8mjJ3q6uMN9IUkGCoDkkpKRduPBrfiURLIDwY8SQ1FdTV1LQ0tDTV1dVU1JUZHBUFRUYCgyFBkKCooMRUUGQ0Yav7FaK7u1kcVqZDU2shr//aKJxWKxKmuqqmtrqmuqK6urWtkivFdmYmg8eczEQX4B8KBYMoECIFlgGNbR0RGdEHv78d2MnCyyw+krsnRZAz19Az19A119HS0dTTV1dTV1LQ1NWbos2aEhTFZjTW1NRVVlVU11VU1VcWlJQUlhWUW5CD1R6WrpTB03aUi/QVI0qc4tMCFiDxQACaKeWf/u88fXH94Ul5WQHUtvKDIYZkam3Nu9ob6hoZ6+loaWaO1UcDic0oqywpKi4tKS4rKSopKi/KJCij8r6OvqTRgxzt3JVVdbh+xYIEICCoBEUFVT9eD541cfAtltVNzUlpeTNzM2sTC1sDAxszS1MNQ3EK3bfV/o6OgoLi3JysvOzs/NzsvOzsulph6gKOrv5btw+lxdbdijRvyBAiDmVNVU33lyL/DTu/b2drJj+Q8pKSlzY1MHG3sLU3NLMwt9HT3xu+P3DofDyS8uzM7NzszNTk5PKSotJjui/0FaWnr04BEzJkxVUVYhOxYIgUABEFuampvvPXvw6NVTihTxoihqpG9oZ2XrYu/k7OCkxFAkOyIKUd9Qn5SWkpqVnpqRmp2fS5FPpZys7Jiho6eOnaikqER2LBBCgAIgbmAY1sBs+BwRcvPR3fqGerLDQUwMjV0cnJ1s7e2t7XkpmJBeqK2vS0pLTk5PjU2MK6ssJzscRImhOGvydH8PPxVlZWlpabLDgYAECoBYwWpiPXjx+HN4SEl5KYlh0Gg0GwsrTxcPX3dvAz19EiMRdcoqyyPjoiPjopPSkslNK9LS0Jw4csLYYSNhLxpxAgqAmIBh2PuQT3/fvELiql9JUcnZ3tHLxcPH3UtBXoGsMMQSJqsxITkxKj46Ii6qkcUiKwwDPf15U2b38/YjKwAIWKAAiAOZuVlnr15Mz84g5epqKqr9vf37e/vbWFpL2lmu8OFwOMnpKUERIaGRYUxWIykx2FhYL5m90NbKhpSrQwACBUC0qamruXTr2sfQIOH/HRXkFXzdvfy9/NydXOG2gPDp6OhITE36EPIpNDqiuaVZyFdHUXSAb/+FM+ZqqmsK+dIQgEABEFXYbPaTwOe3Ht8VcmcuGWkZNycXf09fP09fOVnyC3Eh7La2+OSEkMjQ4MgwIdcWyMvJz5o8Y5BvgJqqqjCvCwEFFADRA8Owl+9fPwt8WVAiVLN+cxOzkYOGD/DtB/f3qUkji/U+5GPgx7f5RQXCvK6JgdGsSTO83TxlZPDbLkFIAQqAiFFWUXb0wsmktBShXZEuI+Pl5jly4DAXB2ehXRQiCFl5Oa8/vPkY+kmYTR18PLxXL1wOe9OLFlAARAYMw15/fHPhxmWh7fkY6hkM6T9oxKBhsGhLFGlqbg4K//zy3eucgjzhXJGhwJgzeca44WNgLoCoAAVANCirLD92/mRiWrIQrkWj0QK8/UcPGQnTPMSDtKyMJ6+fhUSFdXR0COFy9ta2a75ZaWRgKIRrQQQECgDV+Xfhf/2SEB7n5eTkhg8YOnHkOC0NmNohblRUVbx8H/jyfSCrifAyAhqNNmnU+DlTZgrSjAEiBKAAUJqyyvIj504kpxO+46+irDJ6yIhxw8fA3R7xpqm5+e3n9w9fPK6qqSb6Wvo6equ/We5s70T0hSC4gQJAUTAMexr4/Mqd60Qn9hno6k8cNX5I/4FwsSY5tLe3fwwNevTqKdH5QiiKjhoyYvGshbJ0OqEXguADCgAVqaiqOHL+ZGJqEqFXMTE0nj1phq+HNzyyk0wwDAuNDr/58A7RMmBnZfvt2s3qquqEXgWCAygA1IKb4//3rauEpvpoa2pPGzdp+IChsBMsBMOwqPiYf+7fzCUyWUhJUWnmhKljho6CfqKUAgoAhSgtLzt5+Wx8cgJxl9BU15wxYcqwgCHQvAHSGQzDQiLD/rl/k9B2oa6OzuuWrNaC7hGUAQoAVQj8+Pba/Vu1dTUEza+mqjZ93OSRg4bDJRikJzgczvuQT7cf3y2vrCDoEqqqqstmL+rv4w83HqkAFADy4XA4V+9ev//8EUHzK8jLTxs3Zdzw0bJ0ibbuwTCM1dTEbmvt0hhZCkUV5P/rVCMrS5fw8/D29vbHr5/deXK/qbmJiPlRFB09ZMSSOd9I+O+ZCkABIJmi0uJDZ45l5mYRMTmKooP9ByyYMU+8C/SZrMbautrautoa7n/r6+rq62rqalpbW1nNTW1t7S2tzc0tLX1vqCJLl1VkMBQZiooMxX+/UGAwFBhKiopaGlramlramlrycvKE/qNIh9nIvPX43vO3LwlqRGNpZrFj7VZtTS0iJof0ESgApMHhcKITYg6eOdrUTIiXr5WZ5dK5i2wtrYmYnCyqaqqKS0uKy0qKSkuKy0pKy0tr62q7rOiFgyKDoa2hpaWppa2praWhqa2hZWxgpKejK2bn6kWlxX/fvBIVH0PE5EqKSltWrnd3ciNickhfgAJADh0dHdfv37z3/BERyyt1VfVZk6aNGDhM1LdZa+vrMrIz8wrzi0qLS8pKispKhOx9zS90Ot1Y39DUyMTIwMjMyMTE0FhVLJ694pMTzl+/VFAM3n0WRdEpYyYumD5X1N+rIgoUABJoYDL3nz4clxQPfGYZaZmJo8ZPHzdZTk4O+ORCoJXdmp2Xm5GdkZGblZGdWVldRXZEgqKirGJqZGJjYWVraWNnZSO6Ttrt7e2PXj299eguEZWJ7k5uW1dtVGQwvv5SCFCgAAibrLycvcf2V1SBz7JwsnVYvWiFvo4e8JkJpba+Li4pPj07Iz0rI7+4kNzW54SCoqixgZG9tZ2tlY29la0obn+XVZSdunKeiLWLno7uzvXbTQyNgc8M6QUoAELlzad3Jy+fbW9vBzutgrzCnCkzxw0bLSrP0Ww2OzkjNS45IS4pPr+oQDLfhBpq6vY2di52Tp4u7mqqamSHwwfBEaFnrp6vZzaAnVZOTm7DkjWw47wwgQIgJNrb228/uXfz4R3gM3u6eKxauFQkWrOWVZbHJyfEpyRGJ8RSfDdfyBgZGHq7errYOznaOohEjV4ji3Xlzj+vP74BewNBUXTq2Enzps4Ws7N0ygIFQBjUMxv2HNqbnp0BdloVZZVFM+cP7jcQ7LRgwTAsNTM9ODI0JDKshrAyN7FBSVHJ3cnVw9nN3clVSVGJ7HC+QnJ6yolLZ4pLARcPuzu5bli6RrSeikQUKACEU1JW+uvB30vKS8FO28/bb+X8ZcpK1L1HFBQXBkeEfgj5VFZZTnYsogeKoraWNv28fAf6DaDyX5nNZl+5e/1p4HOwdxIzI5Pv1m/T1dYFOCfkS6AAEEtYdMSB00fAJk6oKCmvXbzK280T4JwA+fe+HxpUVlFGdizigIy0jKujs7+Xn7+nr5wsRWu5o+Jjjl88VVtfB3BOJYbit2u2ODvAdgIEAgWAQILCgw+eOQo2rcXZ3mnjsrUaapRz1q2tqw389O5N0DvibGQkHAV5eV937wDffi72zhQ8J6hnNpy8dCYsOgLgnHJycttXb/Z0cQc4J6QzUAAIAcOwt0Efjv99CmAXVhqNNn38lFkTp1Mq1QfDsISUxFcf34RFR4hxBielUFVRHdp/0KjBw7U1tcmOpSvvgz+evnoe4Ak/jUZbtXD5iIFDQU0I6QwUAPBwOJzrD2/effIQ4O9WR0t784oNlPJ1qKuve/v5w+sPb+AWPymgKOps7zRy4DA/Tx9K5cxUVFUcOnssJSMN4JzTxk2eM2WWNPWee0QdKACAYbe1/XH0r+iEWIBzDh84dOmcRdTZ/41PSXz1PjAsBi75KYGuls7IQcOGDhiioqRMdiz/wuFwrt278eDFY4C3l6EBg1cvXC4jAw1EQQIFACSt7NY9h/8E2NFFXk5+7eKV/b39QU0oCBiGhUSF3X/2MCsvh+xYIF2RkZbp5+U7YeQ4C1NzsmP5l9Do8KPnTwD0OvTz8Nm2ehNsaAEQKADAaGlt3XN4b0JKIqgJ9XX0dqzbSoXieA6H8zHs8/1nDwtLisiOBfIV7Kxsp4yZSJEksZLy0j+PH8grzAc1obO9087120TXUolqQAEAQ01t7Z/H96dmpYOa0NPFY/OKdQwFku2xWlpbAz++efjyaVWNyPuySRQWpuZTx0zy9/IlPWWA3dZ25ur5N5/egZrQytR8x/rtWhoiUPpOfaAAAKC2rm7P4T8yc7OBzMY1yJ0/bQ65H10mq/Fp4PNnb14yG5lkxaAgL6+qrKqoqNi5+wqridXIYtXV17a0grelxA1dRkZZSVlBXkFBXl5W9l8r1lZ2a31DfV19fXMLIS0fvoqpkcnUsZP6e/uTfkr86kPg2WsXQblgWZiYblm1yVDPAMhskgwUAEGpqqneffiP3Pw8ILMpyCtsXrHey9UDyGz4aGW3Pnn97N6zRwR1BOwJFEVNjUysza2szCxMDI11tXV7r4Ctq68rqyjPLy7Iys3OzM3OLyoAmHTbOwwFhoWpuYWJmamRia62ro6mVu++BfXMhsLiovyigrSs9JSM1KqaauHEyUVXW3f6uMlD+g8iVwbSszP2HjsAyg7E1Mj4h407KJgIK1pAARAIZiPzh727cgFtcRro6X+3bpuRviGQ2XCAYVhIZNil29eIcKvuCTqd7u3q6eXm6ebgrKKsgnueRhYrPjkhNjk+IiYSuFElgiAoitpYWns6u7s6OluaWgjyfFZQXBgRGxkaFS7M43RDPYM5k2eS67VZU1ez5/Cf2YD+1Yb6Bj+s/1ZfTx/IbJIJFAD8MBuZP/75S05BHpDZPF08tq3aSGIjl/jkhIs3rwA8r/sq1hZWoweP8PP0Adtfl8PhRMZFvwv+EBUfAyRRVVdbd+TAoQG+/YHvO+cXFbwNev8m6D2riQV25p6wsbBeOGOeg42dcC73Jc0tzftOHgKVJ62vo7dr24+6WvA5ACdQAHBSVVO9+9AfuYDu/sMGDFm9cDlZ9f2ZuVmXb11LTEsWzuVQFPXz8Jk8ZqK1uSWhFyqrLL//7NHbz+9xbz1bmVnOnDjN08Wd0POY5pbmV+8DH79+Vl0rJLdUFwfnJbMXkpVg1tHRceHG5aeBz4HMZqCn/+v2n7REwQ6dgkABwENdfd2uA3tyQOz7oyg6c+K02ZNmCD4VDsoqyq7cuR4SFSa0t4Gni/vcKbPMTcyEczkEQapqqv+5d+N9yCe+/o262rrzps7q7+0vtKN4Npv9JPD5vWcPhfM0QKPRRgwcOnPidDWSuha/+hB45uoFII9oxgbG29dsNjYgbe9UdIECwDe1dbW7D+/NApHzIy0tvW7xqkH+AwSfil+4LV5vPrzNbmsTzhX1dHSXzvnG04Wc8+2MnKyzVy9k5mZ99ZV0On3ulFnjh48h5YGsgcm8dPvqu88fhPPBlJOVnTFh2uTRE0g5H45JjN138hCQSjEzI+Nv12yF5wH8AgWAP1paW3ft252SCcDnhKHA+G7dVic7R8Gn4pf4lMSzVy8UlRYL53Ioik4cOW7u1Nl0Uuv4Ozo6Hr16ev3BLTab3dNrLE3NNy5fR+I5PJfEtORjF04KzVfVzNh05YJlpDhNZeXl7D70Rx0IH2lzE7O9O3eTeIomikAB4IP29vY9h/fGJMYJPpW6qvqPm78zNzYVfCq+qKmrOXP1AljP3t5RV1XfvHK9k62D0K7YO8WlJftPHer26H7S6AkLps2hiNNyU3PT6SvnP4YGCedyKIqOGjxi/rTZwq89LK+s2LV/D5COSX4ePltWbSR3nSFaQAHoKxiG7T99OCgsWPCpTAyNd239Xl1VqJ7+GIa9D/l08folJqtRaBd1tLXftmqTKkm7zD3Bbmu7dOvKszcveT+hy8isWbSSlL243nnx7vW5fy4KzXRPiaE4f/qcEQOHCbkIsba+7teDv+fk5wo+lbOd489bvoeecX0ECkCf6OjoOHXl3Kv3gYJPZWVm+fPW75UYioJP1XdKyktP/H06KS1FmBclN7Xpq4RGhR+7eIrVxNJQU9+xbhvRKUm4SU5P2Xt8fwNTePXYTnaOqxYsMxDufnpTc/PvR/4Ekoo2pN+gNYtWQA3oC1AAvg6GYdcf3rz18J7gUznY2P2w8TsFeZBp772DYdjrj28u3rgsZOOEmROmzZ48g3Qjmt4pr6x49SFw/IixZGXC9JGi0uJd+/dUVgvPjokuIzNr0owpYyYK8y/Y1t7214lDEbGRgk81ecyEb2bMp/jbjwpAAfg674I+HLlwQvBflIez2461W+l0OpCo+kJlddXRCycBGpT2kXlTZ08fP0XIFxVvqmqqfvprd3FZiTAv6uLgvH7JKk0hpti3t7fvP304NCpc8KkWz1owafQEwecRb6AAfIXEtORd+/a0tQuaK+nt5rlt9WZhHk+9+fTuwo1LAN3Y+8icKTNnTpgm5ItKAlU1Vd/9/lNFVaUwL6rIYCyft2SgX4DQrtjR0XH0wsn3wR8FnAdF0Q3L1g7pNxBIVOIKFIDeSM1M++XAb4LfQwN8+m1avk5ou+FNzc2nLp/9FPZZOJfrzJihI1fMXyr860oIpeVlO37/EUjSJF/08/JbtXCZkmJv3nwA6ejoOHbx1LvPHwScR1pa+ufNO10cnEEEJZ5AAeiR0vKynXt/qq4RtDq/n5fflpUbhHb3z8jJOnD6SFlFmXAu1xlPF4/vN2wn3XlYvEnLyvjxz11CK9/joaqiunbRSqH51GIYduryuVcfBE27UJCX//27X4VZeS5aQAHonlZ267ZfdwrujMZtYiecuz+GYXefPrjx8DYprXp1tXUP7tpLegcbSeBDaNChM0eFf10URSePnjBv6myhvZ9PXjrz+uNbAedRU1Xb//PvWupaQKISM+BirRva2toOnDoq+N3fw9ltyyohrf2Zjczdh/64du8GKXd/GWmZb9dugXd/4TDIL2DkoOHCvy6GYfefP/rxr19q62qFcDkURVctXB7g00/AeWrrao+ePyW0XhGiBRSAbrj3/FFYjKB5CJ4uHjvXb5eRFsapb05+7pZfdoCy2MXBnMkzhF/VLMksnfMNWV6eyempG37aJpzUMikpqY3L1nq6uAs4T3xywt83rwAJScyAAtCV0OjwGw9uCTiJrZXN9jWbpKWlgYTUO++CP3772w9C8435EltLa5hvJ2TodPr6pWvIKrKrb6jfdeC3e88eCmEDWVpa+ts1WwS3zHr06qngJwriBxSA/yGnIO/QmaMCvq1NjUx+2vSdLF0WVFQ90d7efuLSmSPnjvfibkY0UlJSKxcsgwe/wsfS1HzCiLFkXZ3D4Vy588+B00daWlqIvhadTv9x0w57a1sB5zl95XxiahKQkMQG+Ln9j0YW6/cjfwlYMauno7tr6w9C2A2vZzb8tO/X1x/eEH2h3hkzdJQZ3PwhidmTZ2ioCdVRqgtB4cFbf/1OCLaysnTZHzd9Z2lqLsgkHA7nrxMHSXxWpiBQAP4Fw7BjF04K2AtXU13j120/CcFXIL+oYNuv3yWnpxJ9od6Rk5ObMWEquTFIMrJ02TlTZpEbQ2FJ0bd7vhfCylpBXuGnLd8b6ArkUFTPbPj14O9NzU2gohJ1oAAgCIJwOJwbD26GRgt08KvIYOza8oO2JuHZZhGxUdv3fE+FhczEEeNUlJTJjkKiGdJvIFmnwTwaWaxdB377EPKJ6AupKCn/tGWnirKKIJMUlhSdvHwWJgVxgQKAIAgSGRd96/F9QWag0Wjb12wxIr4p3asPb/Ye3y+EjdevIicnN2HkOLKjkHSkpKSmjJlIdhRIe3v74XPHbzy4TfSxsK6Wzk+bdwrY9SUoLPhzeAiokEQaKABIVU3VsYunBHnjoii6dvEqF3sngFF9CYZhNx7cPnnpDCmZ/l8ytP8gRQZM/CefAb79dbV1yY4CwTDs5qM7Ry+cJPr9aWlqvm2VQMWVGIYdv3RayM561ETSBYB7LsRsFMhsffakGUR7TrHZ7H0nD918dIfQq/QdFEXHDB1FdhQQBEEQKSmpEQOHkh3Fv7z7/OEX4jfZPV3cl81dJMgMLS0tB08fFdzkUdSRdAG4/uBWWlaGIDMMCxg8cyKx5pdNzc2/HPwtODKU0KvwhY2FlaGeAdlRQP5laP9B1Gm8E5+csOO3H4nuXjB6yMixw0YLMkNmbtbhs8cl3AtHogUgJjHu3rOHgszgaGu/+psVgMLpHiar8ef9u4XczOurDPAVnj8w5Kuoqqh6OLuRHcV/5BcV7PjtRyBtfnthyeyFbo4ugswQFB784v1rSdYAyRWAwuKiYxcEcgjR1tTavmYLoSuv2rra7//4OSM7k7hL4ABF0X5evmRHAfkffN29yQ7hf6iqqdr5+0/5RQXEXYKbeWFsYCTIJP/cu0lokBRHQgUAw7DLd65V11bjnoFOp3+7diuhSZAl5aXbdu+k4LvTysyCan3eId5untTZBeJSW1/3077dhL6BFeTlv9/4rbIS/kYFzEbmobPHKJJYIXwkVAAev34WERuFeziKopuWrxOwLrF3istKfti7S5htYPuOuxOFdhsgXJQUlSxNLciOoit19XU7//gpMzeLuEvoaulsWr5ekPa/uQV5d54IlAUuukiiAGTl5Vy+fU2QGWZOnObvSeAeSHFpyQ97f6muFbQXDUHY29iRHQKkGwR3yyGCRhbr53170rMFSrXoHXcn19mTZggyw63HdwmNkLJInAC0tbcdOXe8vb0d9wyeLu6zJk4HGFIXiktLfvjzl5o6it79URS1MqPcShOCIIitpQ3ZIXQPq4n1077diWnJxF1ixoSpfh4+uIdzOJwDp482twi7gTbpSJwA3H3yQJBNSU11zQ1L1wryvNk7xaUlO/f+TNm7P4Ig+rp6CvIKZEcB6QYqu/K1tLTsObw3JSONoPlRFF23ZJUgBXFlFWXnrl0EGJJIIFkCkF9UcOcp/s0+Go22bdVGQU6ceqeqpmrXgT3Cb/nNFzD9n7Joa2oJwYQcNy0tLbsP/Z6Tn0vQ/AwFxnfrtgryG3gT9D4qPgZgSNRHggSgo6Pj2MVTgmz+LJm90NaKqKfs+ob6n/btrqiqJGh+UAhoxwghDhRF9XX1yI6iN5qam3ft30OcB4OpkcmSOd8IMsOZq+epYLQlNCRIAJ4GPhckob6ft5+AlYe90MBk/vDnL8WlImBOoqmuQXYIkB5RV1UjO4SvUM9s+PXA78R1FR45aFg/bz/cw8srK/6+dUVyskIlRQAqqiqu3buBe7imuuaqhcsBxtOZltbW3Yd+LyguJGh+sChD/2cKIxLu3GWV5bsO/MZqYhE0/5pvVmhrauMe/vJ9oID2MCKEpAjAmasXcLf6QlF04/K1SgxFsCFx6ejoOHjmSEYOgYnSYCHo9wABgqIiUQdUYMkrzP/lwO8Cdt/rCYYCY9tq/HahGIadvnJOQh4CJEIAPoQGRcZF4x4+c+I0J1sHgPHwwDDsxKUz4TGRRExOEDIydLJDgPSINMWKgXshPTtj38lDBN1nrc0tBbFozC8qePrmBcB4KIv4C0Aji3XxxmXcw+2sbGeMJ6rr4Y2Ht998ekfQ5ARBNb8BSGeIS1Amgqj46BOXzhA0+YzxUx1t7XEPv37/FmUrMQEi/gJw6fZV3ImV8nLym1esI+iW9y74461Hd4mYGSKxiJyv5dug9/efPyJiZhRF1y9Zg7t3WHNL8/nrf7cSs0lFHcRcAGIT4wM/vsU9fNGsBYKcJvVCambayb+JWvsQigRWS4oQjU2NZIfAN1fu/CPIDm0v6GhpL5q5APfw4IhQsS8LEGcBaGpuOnv9Am6zbxcHZ4IaLVVUVfxxbL+IdiOCAkBlGhtFTwAwDDtw+ghBWXAjBw0TpFPC2WsXmprF+Q0vzgLw6sOb4hKcmfUK8grrl6wiYke1qbl5z+E/6xvqgc8sHCheqCzh1DNF8n3V3NK899j+Rhb4xFAURdcsWom7eXVtfd395w+BRkQtxFYAKqoqbz7E30F32dxFmuqaAOPhgmHYsQsnKWjx33eqavA3UYAQTUl5Gdkh4KS4rGTfKUKSgjTU1BfPWoh7+IPnj0tF9rf6VcRWAE5dPot7s8LdyXVI/0FAw/mXO0/uh0SFETGz0KC+WYXE0tLSItLPZ3FJ8QL6tPfE0IDBuDeC2trbrtz5B2w81EE8BSAiNjI6IRbfWFm67IoFS8HGwyUmMe7Gw9tEzCxMRPrxRbwpKCkS9fa2j149fRv0noiZVy5YJieL0ycuODI0MTUJbDwUQQwFAMOwizeu4B4+Z/IMXS0dgPFwqaiqOHjmqCAtiClCUWlxK1vMc+NElPSsdLJDAMDpq+eJWGRoa2rNmIC/NOzk5bMimrXRO2IoAJFxUSXlpfjGmhmbjh8xFmw8yL/tJo4wG5nAZ+4JeTl5FWUVLQ1NRQYD98KnWzo6OnIL8gFOCAFFeg5+r8MvkZaWVmQwNNTUdbV0NNU1FRkMaWlpgPP3BJvN3n/qMBGLjEmjxpvjbZlQXFpCUL0CuQjjLypMOjo6rt+/hW+slJTUusWriCj7unLnH+LspbQ1tS1MzSxMzA31DLQ0NLU0NFWUVbq8hsPhVNdWV1ZXl1eW5xbmZ+Vm5eTn4nZiSUxNsrW0FjhwCEgwDEtOS8U9XFVF1dLU3MLE3MTIWFtTS1NdU01F9cuXNTCZldWVFVWV+UUFOQW52Xk5RCQFFBQX/n3zysoFy8BOS6PRVixYtuO3H/BtlD188WTiiHG4K8uoiVgJQAeGvXj3KqcgD9/w0UNGWhDQ5z06IfbRq6dg52QoMNydXF0dXdwcXTTU1L/6ehqNpq2pra2p7fD/7Xw5HE5aVkZMYlx0Qkwun7+xxNSk6eOn4AgbQhxZedn8NpKj0WiOtg7uji4ezu5GBoZ9GaKspKSspGRhau7n+W//xeKykrik+JjEuLjkBEGabXThxbvXDjb2AT79QE3IxdbSetTg4S/evcYxltXEevDysYDNh6kGKuqnRp2pqa3dvmcnvjQVFSXlk3uP4s4X7onq2pqNP21tYILZ/JGWlvZ0cR/oF+Dl6iEjLQNkTgRBCooL3wa9fx/yqY/VCXQZmX9OXKLToSschbj+4FbfnUXMjU2H9B80wC8AoH00k9X4OTzkQ8hHUA+7DAXG4V//Al6Kz2pirf5uI750KQV5+XMHTomTIa5YCcDDF48v3sR5/Ltm0Urgdb8Yhv168I+YRJz5SJ1RYiiOHDxi7LCR6qpfX+/jg8PhfAwNuvvsQV/60uza+oObowtBkUBwsO77zV8tpkVR1Nfde8qYidYWVsRFkpGT9fjV05CoMMGT+u2tbfd8uwv4ruyrD29O4jWhmzZuyoLpc8DGQyLiIwCsJtbybWvxHbRamprv/3kv8LrfR6+eCmJEykWRwZgyZtK44aOF0+4Vw7DgyNDr92/13rdv5KDhq78hqkMOhF/SszO27/6+lxegKDrQL2D6+ClCa+lcUl56/f6tzxEhAt5hZk2aDnzXBcOwrb/syMrLwTFWTlb23P6TXx6ziSjikwV0+/E9fHd/FEWXz18C/O5fWFx07e51QWag0WiTR084/dfxqWMnCa3ZN4qi/b39j+45sGD63F7Ou4IjQ9htYpgVJ6K86TV33snO8cDPezctXye0uz+CIPo6eltXbTzw814B8wVuP76XnI7/cLtbUBT9ZuZ8fGNbWlvFKR1ITASgoqriGd4GDv6evjYWgHNaOBzOkfMnBLlF2lhYH9z15zcz55Oy4SgtLT117KSTfxzxdPHo9gWNLNbHkE9CjgrSLY0sVlBYcLf/S01Fdef67Xu+/ZmI7Ia+YGFqvvf7PSvmL1GQV8A3Q0dHx/GLp9hsNtjAnOwc/Tx88I19/vYVcT2NhYyYCMDNh3fw3W2lpKRmTwZ/rH/7yb3MXJxdHmk02jcz5//5wx5TIxOwUfGLhpr6Dxu/XblgabfPH49ePxWb/UOR5tnbF926nng4ux36dZ+Pu5fwQ+oMiqJjho469tsBXgYav5SUl956DL5zxjez5uPLpGhlt95+ch94PKQgDgJQUVX5ITQI39iRg4YZ6fcpAa7v5BXm3336AN9YbU3tvTt3Tx49gSKtnVAUHT1k5MFf/jT7ooKmsLjo3ecPJMQE6UQru/VZYNdnXykpqZULlv60eWe3ufykoKmuuXv7zzMmTMX3xn7w4jG+Lfte0NXSGT9iDL6xrz4ElldWgI2HFMRBAG4+uoMvAVlOVnamANXh3dLR0XHs4il88TjZOR785U9CMzTwYahn8MfOX92dXLv8/Oq9G7A9ALm8ePuqntnQ+Scy0jJbV20cPWQkWSH1BI1Gmztl1vY1m3EcaHE4nOMXTwH3Cp0+foqSohKOge3t7Q9fPgYbDCmIvABU19a8D/6Ib+zEkePVVNXAxvPwxeOs3GwcA4cGDN615XvKphjLy8n/uOm70UNGdP5hbV0t7mcdiOA0slhdfv+KDMbub3/q5+VHVkhfxd/Td8+OXTiyaHIL8h69fAI2GAV5hcmjJ+Ab+zboPZMleu13uiDaAoBh2NtP7/GtC5SVlCaNHg82nrLK8huP8DQhmDJm0volq4XjtYIbKSmplQuWTRr1P7+0hy+f4HZeggjI9Qe3Ot+D6HT6Dxt32FnZkhhSX7A2t/zju1/7UsHehRuP7gC35h8/fAy+2pqW1tYXb1+BDUb4iLYAsNvaAj+9wzd2+rgpuDMTeuLijcs40hUmjZ6wcMZcsJEQxzcz5w/vVDHX3t5+4bqgtQ4QHOQXFbx8/5+lAY1G2756M/Xv/lwM9PR3f/szvxrAZrOP/30KbOoBnU6fMnYivrFPAp8DT08SMqItAOExEeVV5TgGamtqjx4KeJM0JjEuPCaS31HjR4xdhDclmRRQFF21YFnn3JKo+OjAj29JDEkC6ejoOHbhvz1x7h/Fy7X7nF1qYqCrv2vrD/yarySlpeBe8/XE6MEj8BlO1DfUvwkCHIyQEWEBwDAM9wb07EnTAXrpIAjC4XAuXL/E7yhfD+8ls/E3qyMLGo22ddUmWysb3k/OX/+7rEJs2+ZRkIcvHnfOM54xfupw0EYmQsDYwGjHum38fhKv3b3OagLZPVhaWnrauMn4xj548ZiINpZCQ4QFIDIuKq8QjzG9prrmQL8AsME8fv2sqLSYryGWpuabV6ynSLonv9BlZHas2cLbPG1pbT1+8TQsCxAOhcVFnY+a3BxdZk2aTmI8guBk67Bs3mK+htQzG4CnHgwfMMRATx/HwPLKCpFu8irCAnD32UN8A6eMmQjWXqquvu7243t8DZGTk9u8coPQDB6IQE1Vbce6LbzlW2Ja8pPXz8gNSRJoa287eOYIb+tZX0dv2+pNUlIi/EEeOWjY0IDBfA15/PoZ2NQDKSmp6eNwOpw/EGVnCFF938QnJ6Rl4mmAp6KsMnzAELDBXL59ram5ia8hqxcuN9DFs+KgFDYW1kvmfMP79sqd6181pIQIyN83r/I6XsjJyX23fhtDAbCHufBZMX8JXz5F7e3tV+8IZLT1JQN8+2trauEYmJWXI7odg0VVAHAv/yeMGAvWyD4tK+M9n644A/0CgO9BkcXoISOG/f/yra297ci54wC7gkC6EBEb+fztS96365esNjYwIjEeUMjSZdctXsXXdmhIVFhyegrAGGg02gS87WAfgi5QEBoiKQD5RQXxyQk4BirIy3cpZRIQDMPOXbvA1963gryCaKX9fJXl85fw7DSy8nKuCuaBCumJ6tqaYxf/S4IcN3wMlQu++MXWyobfdtwXb14Be+w0YtAwZSU8hcHRCbHVtfy1Y6MIIikAb/DmgY0dNhrs8/LHsM/8WpQsnDEPePkxucjSZbes3ECX+fcw4NGrpxGxfKfDQnoHw7Aj547zWsuZGpksnDGP3JCAM3fyTL4+Glm52fw+fPeOLF12zNBROAZ2dHSIqDmu6AlAU3Pzpx7Mb3uHLiMzdthogJF0dHTc5tOk0EBXH3jfMSpgZmy64P/vRxiGHbt4it/+tJDeuf3kXnxKIvdrOTm57as38xRXbJCTk5vNZzrTtbvXW1pbAcYwbthoOVk8qRkiWhQmegKQmZNZW4/HjHv4wGFgzRHfBX/sS/fEzsydMkukEzZ6Ydyw0d5u/1aHNTCZB04f6ejoIDcksSEjJ6tzv9+1i1biy1mkPsMHDOXLnbe6tubxq6cAA1BSVOI3JYkXyaewzwAjEQ6idzN6h8v6DUXRiSPHAQyDw+Hc4TP109TIxN/LF2AMlAJF0XWLV/EqA5LSUu6Ii2c6uTQ1N+0/dYhXbTRq8PAAn37khkQcUlJS/LqzPX71FKwl7YSR4/Ct0nAYAZCOiAkAs5EZHBGCY6CHs7uOFp5q7554/fFtWSV/LhTjh48R0bKvPqKspLRl5Qbeh+fGw9uimx5HHU5dPseznjc1Mlky+xtSwyGcgX4BmuoafX89k9WIuxtgt+hq6fi6e+MYGJUQI3JHwSImAIGf3uHr/DUGqPNPW3sbv7WIDAVGgK/YLtx4ONraTxkzifs1hmGHzx3H16gZwuX1x7e8jQVZuuzWlRvBJjFTEGlpaX5T9R6+BPwQMGrwcByjOBzO6w9vAIYhBERJADAMe/k+EMdAXS2dL5uZCMLL94FVNVV8DRkaMFik6377zpzJM3g9lqtqqg+fOwEtIvBRUl568cZ/TquLZy80MgDcvY6aDO43iK9NGGYj8zlQZ2Zneyd8dZqvPrwRLWsgURKA2KR4fI5jIwcNA7j3wmazcRR/i1PKdu/QaLQtKzfwrLaj4qM71y5B+khbe9u+k4d4C1s/Dx98y1JRRENN3dXBma8hD148BvgQgKIovmy9mrqa6IQYUGEIAVESgFcf8Cz/ZaRl8B3r98Szty/53elTU1G1oV6jR+LQ0dJeu2gl79u/b17Nyc8lMR5R5PKta7xfmqa65prFK3t/vZjRz9ufr9czG5lg27MMGzAU3yM7cLdqQhEZAahvqI+IjcIxMMDHH0f/uZ7gcDhPA5/zO8rXw1u8j3+/pJ+339BOFhF/nTwIuwf3neiE2Kf/f7CJouiGpasp2yuUIDyc3fj9yDx4+bilpQVUAIoMRj9cOXtR8TF19XWgwiAakRGAd8Ef8W2ujQLq/fA5IqSqpprfUY62DgBjEBWWz/vP4au0vOzcP3+TG4+oUFdfd/TCSd7ByYwJU53tncgNSfioqahamJjxNaSByQTbngXfrYPD4XwUnYIAkREAfPYP5samvANJIOBY/iMIYmtp8/UXiR1ysrKbV6zn+UW/DXr/HlcNh0SBYdjBs8d4S0hrc8uZE6aRGhFpONk58jvk6ZsXADMObCyszY1NcQx8//kDqBiIRjQEIL+ooLCkCMfAIUB3/5PTUzNysr7+uv9FW1OLr7xmccLC1HzetNm8b09fPc9v7bSkce/ZA57RoYK8wtZVm8D2rhAhLEzN+R1SWl4WGRcNMAZ8fdZyCvKAN68nCNEQgNCocByjaDRaAJ9HSb3zJBBPwxMTQ2OAMYgcE0eO4/WqbWlpOXDmSFs7nkoOSSAzN+vGw/9afa1csBRs9aJoYWVuiWMUvmf0ngjw7Y+vd2xkHJ4DS+EjGgIQFo1HANwcXVXBmf9UVFXgK/XW19EDFYMogqLouiWreS6P2Xk5wFt5iActLS0HTx/ldVMYNmCI2DSNwIeOpjaOPJz4lMTc/2+YIzhKDEUPFzccA4MjRaNPpAgIQFlFWQ6uv+ggoJ+fJ4Ev8Lmb6WrrAAxDFFFRUt6wdA0vqePx62ei6JpCNKcun+W1OdTT0V3aqdWaZIKiqJaGJo6Bz4DWnQzyG4BjVFpWOs/Ag8qIgAB8DMVzpC4vJ+/t7gkqhuaWZtxNCDTUJPQAoDNuji6TRo3nfo1h2NELJyqr+SulFm/eBL3/EBrE/ZpGo21esUFeTp7ckKgAvh2wD8GfACZierl6KCny3SUGwzCwDcsIQgQEICQKz8OUv5cvQOuFN5/e8dv1l4cYtGwFwvxpc6z/vxqukcU6dPYY9IvmUlpedr5TjuyCaXOscW1/ix/4Wie1tbe9+gjMk0daWrq/N54y/lBcG9dChuoCUFldhW9HD+z+6Zug97jHKsjDpRyCcC0iVqzn/TaS01Nu8+mnLZa0tbftO/Wf5YObo8vE/39UgsjJyuEb+PrDG4DLC3z+27FJ8QAL0wiC6gKA7/hXQ03dmf8k4p7Iys3OK8zHPVwWV4MhsURXW3fZvCW8b28+ugP9oq/euZ79/11FuxyWQPA150IQpKqmGuBby97aDkcvKTabHZ0QCyoGgqC6AITgSgD19/ID+CnC14KGRwcHbnT8x5B+A3mH89AvOiYx9vHrf3OLURTdsGytmPWLFhBpmjTusW/BVWOhKOrn6YNjYEg01XOBKC0A9Q31qZlpOAb6uAE7/m1vbw8KF6iwG6a9d2HVwuU8r11J9ouub6g/ev4/y4eJI8d5OOPJOBRjBPnshEaFsZpYoCLxx+XmGx0fQ/GPP6UFICYxDsdGnhJD0d7aDlQM4TGRDUyBlqii2CqaUOTk5DYuX8crcI2KjwZr5i4ScC0fav8/WaVLyTSEiyCfHXZb26ewYFCRONrY43g4a2puTk6jdC4QpQUA3y6el6sHwOp5Afd/EARpkOAtjp6wNrecM3km79u/b17BV+ohutx//iguKZ77tZyc3JaVG/BVnIo3LexWQYa/+4w/d6MLKIri6xMZ8/9/ZWpCbQFIS8YxytvdC1QAdfV1sUlxAk5SXcu3e6gkMHXsJJf/b/rR1t6274QE+UVn5WZff3CL9+3yuYvx9Z8Se6r5d97tTEZOVn5RAahgvN08cIyKTYwDFQARUFcAqmqqcJTS0el0N0dXUDHg9qDuTFWNiPWJFg4oim5evo7n1VFSXnr++iUyAxIWLS0tB8/8Z/nQ39sfbMMicULwakGABrROdo44qvPyiwr4bR8rTKgrAPHJiThGuTm64E4d+xIg757K6krBJxFLVFVU1y5aycvXevPpHa8aVow5deVccdm/lqi6WjprFklWq6++g2FYpcC3zqDwEFApBjLSMi4OeBozxCUlAAmACKgrAPhsXb1c8DymdUthSVFBcaHg8+QUwG6IPeLl6jFu2Gjet6cun+XdHMWSd58/fAj5xP2aRqNt6lQZB+lCeVWF4IVUVTVVmbnZQOJBEMTT2R3HqBgK7wJRVADa29tjcR2euDm5gIohPCYCyDxFJcXULwgkkYUz5/HabnTZHhEzyirKzv1zkfftvCmzbC1BdisSM3j1cQKCz0y+W7zdPHEUGMUlxwu+k0wQFBWAxLQkHEeChnoGmup47AO7JRxXC+IvwTAstzAPyFRiiYy0zLY1m+Xk/i36z8rN/uf+TXJDIgIOh3PgzNGm5n/f1U62DpPHTCQ3JIqTk58HZJ7gyBAg8yAIoqKsgqNNTSOLBWQvgQgoKgCpGek4Rrk5Alv+19TVZPLf/KsnUjPx/HMkB30dvSWzv+F9++DFY7B9najAlbvXM7IzuV+rKKtsWbURWj70Dr4i0C8pr6wAmGTs+v+pa3wRn4LnRFMIUFQA8DmpuuD623RLWHQEwPJUfNtZEsWIgUMDfP+13MIw7NjFU7V1teSGBJCYxLhHL59wv0ZRdO2ilTi8ZSSKpubmtCxgy6YwcLtAzvZ4zoGTqFoORkUB4HA4mbl8r75pNJqDjT2oGCIA7f9wSclIhccAX2XVgmXamlrcr+sb6g+fJ9wiIiUj7U3Q+3pmA6FXqauvO9Lp3zJx1HhvcFYl4grYffPP4HaB7Kxs6XQ6v6NSM1NBBQAWKgpAdn5uSyvfFYC2ltagEiqampvw1aD1RHt7O2WfAakDQ4GxecV6XhV3XFL8/eePiLvc7cf3vvv9x2MXTm74YQtxGoBh2JHzJ3j9SSxNzedPhZYPXyc6PgbgbMWlJYUlRUCmosvI4HCaYTY2VlRRsUEYFQUgBdf+jxM4/+fIuGjgiSjBkaFgJxRL7KxsZ02czvv2n/s307MziLgQu63tztP73K9r6+uCI4CtELvw4MVjXhagnKzs5hUbpKXxO1xKCOy2ttBoMDl4PAA6M7vgutWk//8JEKWgpADgOvxxAGcAR8QJZGh0OEBvQjFm+vgpvLMcDodz8MwxIiwiamqrOxuNZQHKOOxCl4ymFQuWGehBy4evE0bAhyU+GVg1lpM9HgHIgALQFzAMw5EzQ6PRrM2tQAUQnwK+co/NZodEUt0cnApwLSJ4Z6RlFWVnr14AfpUuhwv1DeC3gFpaWw+e/a+mYZBfwJB+A4FfRSz5SEBBeFJ6CihfXnNjM17Wct9Jz4EC0AeKSovrG+r5HWVmbIrjT9IteYX5Avo/90QQYfsMYoaqiuq6Jat5WZLvgj8CvyN0ycAk4uHszJVzxaX/WT6sWLAM+CXEEg6Hk5gK8gSOC5vNTgWUVkSj0Wz4X27m5OVQsMKRcgKAb6fMzsoWVADENSm0MrMgaGbxw8PZrXNr3JOXz5aUlwKcv8sTAIYATjf6HBHCMxKn0WhbV2+Elg99BMMwhgKDiJkB7gLhuOGw29ooWBBKOQHIzcfjnGNnaQMqgEQCMnYV5BVWf7N8/rQ5wGcWYxZMm2Nj8a9TQktLy8HTRylbT9+FssryE3+f5n07b+psKzNLEuMRLaSlpf/8YY+tFbBPNI84kAKAJzwKHgNQTwBwiaQNIE8VDMNSMgALgKeL+4k/Do0cNBzstGIPjUbbvGIdb+GcmZt16/Fdgq6FIsCKcjkczsHT/1k+uDm6TB49AdTkEoK2ptbenbvXLV6lpKgEcNqc/Fwc28vdYmNpjaPrFAUTgaglABiG5RXm8ztKQ01dU10DSAA5+bmNLGDbwbJ02ZULlv6wcYe6qjqoOSUKXW3dVQuX8769/fheAuXLKa7d+y91VUVZZcPSNdDyAQcoig4bMOTE74f64WrG2y0YhiWkgNnglZeTN9I35HcUfAL4CpXVVTjuv5bg9tYBHgBoqmv+vvPX0UNGws+/IAzw7T9swBDu19yiKiaFW2wmpiU/ePFv8RqKousWr8LRSBbCQ0VZZfuazau/WQ6qySuoc2AE122ntKKMau9eaglALi7PJnNjM1ABgDoAcLS1P/TLX5b8GwdCvmTZ3MW81VZVTfWJS2eAXwKIRtczGw6cPsKzfJg0aryXK7DuFJLMyEHD93y7S0VZRfCp0gEKAP+fbgzD8sC1qAQCtQQAx/4PgiCWZmDusx0dHUAOADxd3H/e8oOyEsjtS0lGTlZ22+pNPAOW0KjwVx/ekBvSl2AYduTccZ6BnaWZxTxo+QAOe2vbP777VXAHvdzC/FbBGs3zwLfxUATIkQIU1BIAfH4dFiZgBCC/qIB3docbbzfPHeu20mVkgIQE4WJiaPzNjPm8by9cv1RUWgxwfsG36R69esozG5CTk9u8fD20fACLgZ7+bzt+URVMAzgcTlYumKpvMyNTGWm+P+aFJSDft4JDLQHA8alWU1UDtc2agysDtTOeLh471m7F8baAfJWxw0b5efhwv25lt/514iC7rY3ckHhk5+Vcu3uD9+3K+Uuh5QMRGOjp79ryvRJDUZBJQLlLSUtLGxsa8TsKlCcdKCgkABiGlfDfD9bU0BhUAPhOIHiYGBpvWbke1GkV5EvWLVmlranN/Tq/qODa3evkxsOlpaXlwOkjbe3/qtHgfgMHQ8sHwjAzNv1u/XZBnq4ywFkymPB/84FbQD1SWV2FwwXa2IBvEe6J7Hz8z4aa6ho/b/leQV4BVDCQL2EoMDavWMeT2Mevn+G27fui9Bf/FtDZfy7yGtnrausun7cE91SQvuBgY7e6U3Iwv6RlAfOXxZEJWl1b09TcBCoAwaGQAODb1cXxN+gWDMNyC/AcQSMIIiMts3P9dg01mOxPOHZWtjPGT+V+/W/jsP+32hcE3EcAwRGhb4Pec7+WlpbetgpaPgiDoQGDhwYMxje2tq62sroKSBg4ngAQBCmi0jEAhQSgGJcA4NiG6/7qZSW4bYcXzZqPo1U0BB8zJ07jteWrb6g/iqtxGJBeY2WV5cf/1/IBYEkKpHeWz1tiqGeAb2x+Ec6lXhdMcN18KHUMQCEBKCrl+wAARVEjfTACkI3XEd7L1WPM0FFAYoD0BRRFNyxdzTsJjEmMexr4nO9Z/vf+j8MKgsPhHDpzlPc47+7kOqmTex2EaORkZTetwHnkll9UCCQGTXVNHL51lEoEopQA8P17UVVWAfXEje8EWE5Wdvm8xbDWV8hoqmuuWbyS9+3l2//k8PnnE9z+8/qDW7zdZBVllfXQ8kHoWJqaTxyJR3QBrsGNDPjegi4sASM/QKCQAJSWl/E7RF9XD9TVs3HlgM6dMouXlwIRJn4ePqOHjOR+3dbedvD0EYEKfPi8dyelpfD6FaMouqlTBxuIMJk9abquti6/owqKgd2C9fm/Or5yV4KgigA0shpr6mv4HaXH/2+/J/L5L9HW19EbO2w0qAAg/LJ49kJTIxPu14UlRRdvXO77WEGcpbmWDx0dHdxvJ4+e4Obogns2iCDQ6fQF0/l2WS8sKeL9+QQEh/xUVlc1t7YAubrgUEUAyirKOzh8/0lw/Pa7pam5GYdP7Nyps2DWP4nQZWS2rNzAs4h4+T4wOCK0j2Nb/vcT2PfdGwzDjl88XVP372LF0tR87pRZfRwLIYJ+Xn78Ng9gs9nllRVArq6ng+cW1NjYCOTqgkMVAcCXgYPvt/8lZRV87z4ZGxgBNKqF4MPYwGjxrAW8b09fPcdz4+mdlpb/EYCOjr4+EDwNfB4RG8n9Wl5OfuuqTdDygXRmTpjG7xBQu0D6Onh2oatr+d7tIAiqCEBlTTWOUaC2gEr5F4AJI8bCQz8qMHrISF8Pb+7XDUzmka9lhTa3NEfGRb98H9j5hzn5ucERoUzWV9ZlOQV5l2//w/t25YKloJYgEEFwd3I1N+HPEhiYAOA6hqyoAvP8IThUWbzk4UrC0cMlv19Syme/WRVllYH+A4BcGiI4a75ZmZ6dyV37xybFP3jxeMqYiV1eU1JeGhEbFZ0Qm5ye8uUBQFNz818nD6IoamZk4mDr4Gzn4Obk2sXTqaW19cCpwzzLhyH9Bg6C7wHKMH74mCPnT/T99aBuwQryCooMBr9dTCqqKoFcXXCoIgCNX1t8fYkigwEqB7SMzw3BQX4B0O+TOigrKW1cumbXgd+4a/9r927YWFg72NghCNLR0RESFfbo5ZOMnKyvzoNhWE5BXk5B3pPXzxQZjACffmOHjubl+V24/jcvU1lXW3cZtHygEv29/c9fv8Rq6uuNuAJQMTCCIBpqGvwKQB2gzpSCQxUBwOHsqKmuCerqZXxmoEK3L6rh6ugyZujIZ29eIgjC4XD2Ht+3dM6iuob6Z29e4Dvua2SxXrx7/fJ9oKeL+6jBI/IK819/fMv9X9LS0ttWb4KWD5SCTqf39/Z/9SHw6y9FEARBKsGtwTXVNfjNIQTVmlhwqCIAOHxAAXrv8HUGYKRvaGZsCurSEFAsnDEvPjmRu0hvYDIPnjna7ctQFFVTUdPW1OSWcTa1NHVwOqrragq66waBYVhkXHQXy7m5k2fCXm8UJMCHHwGorsQwDMgxHo6VKHwC6EoB/7V5GoAawbPZbL4O5WGfP2oiS5fdtGL9tl+/6zbFW05OzsvVw93R1c3JtduiLQzDyqsq0jLTQ6LCYhPjenokNTc2nfzFAQOECthb2/V9O57d1lbfUC9gexku6vz3I6mt7VOumhCgigC08b8FpK4Cpg9MRXUlX9Zg7s5uQK4LAY6qsrKUlFQXAVBRUh47bPSYYaN6bySCoqiulo6uls4g/wFNzc0fQz/dffqwqqbrTrGZiRnM/qImNBrN3cntU9jnPr6+oqoSiABoavD9BFBdB9NAO4FhGA53RnVAW0B19Xw8jslIy9hYWAO5LgQszS3Nvx35q729nfcTFEUnjRp/7sDJmROn8dVGSkFefvSQkaf/Orpi/pIuvZ3fff7AOwyAUA17a7u+vxiUKbSqsjK/Q1hNLIp0BaDEEwC+34WaigqQqzc0NvT9xRamZjD/R0Aqqiov377GamLp6+pbmVl4OLt3ucnigMPh/HXiYOemnmoqquuWrPYQ4HFNRlpmzNBR/bz8jv99OiI2ivtDDMNOXT6ryGD4e/oKGHNHR0dOfm5SekpGdmZDI3PkoGEBPv0EnFPC4aZ+9ZHKajDnwCrKeG5ErKYmKvSPooQA4GsFo6zIt/B2Sx0/HUWs4fJfYO4/f/g5IgRBkNikeARBpKSk7KxsvVw9fN298dVVYRh28vLZmMQ43k+sLax+3Pid4LqCIIiKssrO9dufvH528eYV7nNqR0fHwdNH1Xeo21rieTOw2eyYxLjwmIiIuKjOG9ZJacmmhiY43CUhPIz0DeVkZfvYWPCrdX99REUJz42oSy06WVBCAHD4gCIIogTi440gSD2TjycAQ9jsW2Ayc7M7f9vR0ZGcnpKcnnLp1lUjA0MfNy8fNy8rc8u+b7U/evX0zad3vG/trGx3bf1BTlYWVMAoik4YOU5ZWeXo+RPcIrK29rY/jx84+MuffTcBZTWxIuNjwqMjohNiuzUuxTAstzAPCoAgoChqbGDUl5oPBEEaGplALopvJcpq5q90gCAoIQD4nPmUcQnvlzD5eR/gbkIE4eFk65D1vxrAo7C4qLC46O7TB5rqmn6ePn4ePvbWtr0rQU5+7tVO3eGNDAx/2LgD4N2fxyC/AFk6/a8TB7lv15q6mlOXz+5cv733UY0sVlh0eHBkaEJqUufziS+RkpKyNrcEGbFEYtRnAQDlyCYnJ0en09lsNl+j6ur5WHcSByUEAEdahZSUlCL/vXi6ha+cXB0tHSAXlWQWTJ+rrqp27/mjXjbfqmqqnrx+9uT1MzVVNT8Pbz8PH0dbBymprjkLGIadunKOd2OVl5P/ds0WRQaYN8aX+Hn4zJo4/fqDW9xvw2MiI+Oiu00LbmpuCouJDI4IjUuO7/2+z0VLQ/ObmfNBudtKMlp9zsrna+XXO8qKyl8mjPUOq4kShqCUEAAcKDIYoLLxGhr6KsUoisK+H4IjJSU1YeS4ccPHJKYmfQj5FBId3st+aG1d7fO3r56/faXEUPRy9fD38nNzdOEZcH4OD8nIzuS9eNncRUb6xG6hzJgwNTs/JzzmX0PQ6w9udRYArtPc54iQXioJOqOno+vn4evn6WNlZgGzS4Gg2efyIFBnAAiCyMvL8TsEZgH9R1sflkhdkJcDVohfx+zrE4CyohJsAAAKKSkpFwdnFwfn5fOXRMRGhUaHxyTE9dLVi8lqfBf88V3wR0UGw9vV09fDR1db59bju7wXuNg7Dek/iOiwURRdtWBZfHIC96QxJz/3zad3tlY2KRlpEbGR8ckJfbnvGxsYcTe4YEk5cPqe2g/wCUBelm8BYDVBAfh/2tv5rgIDKAB93woEeFEID3k5+YF+AQP9AthsdnxKYkhkaFhMxJeuDDwaWSyuEnT5+ezJM4SziFZTVZs4cjxPe45dPNXHgcYGRv5evv29/OFJL3H0/fgH5BMA/3cG+ATwH33ZJO2CnBzfktsTrX0+vZGBFQBEQqfTvVw9vFw9VrW1xSXFhUZHRMZG9fFT6mLvZGdlS3SEPCaNnvDi/asG5teXkCiKWptb+Xn6+Hv66mjB9tGE0/c7A5vNbmtv62L6jQ95/p0BWVAAeLTz36BVAdxivO8uFLD3k3Cgy8h4u3l5u3lxOJyktOTQ6PCwmMjeW31NGj1eaOEhCKIgLz/AN+Bp4POeXoCiqIWJmaerx2D/AfBol7K0tbWDEQD4BCAIbfxvAcnxv+nWLRiGtXP6+vyB40kFIgg0Go17TrBi/tLs/NzIuKigsODiL4xjNdTUXR2E3Za9n5fflwKAoqi9ta2/p6+fpy9At1pI3+Erp7ytvQ1BACwlcaQd42iBTgSUEIAvOzR9FV4rcAFpa2/vuw1R3zeLIGBBUdTS1NzS1Hz2pBlZeTmBH98EfnrHe9v4uHt9mSFKNHZWNhpq6jwfWSWG4rjhY4b0H6itCfd5yISvVRqoJR2NxveNtAOjhABQwgwOx59BGlA2Dl/nz83UeGqTcCxNzVctXG5sYMT7ibO9k/DDQFHUwcae9+3Y4aNnTZoO7/6kw+SnvAvH3kO34NgcxmF/SQSiKgA4JLdb+LKhbmxi4ehcBiGCzluoFnw2BAeFmZEJ72uKZPVB+HJ2AfUEII3jCaADCsD/g0OHaTQwkfN1Q8cwrK6eKp0cJJzmln/zRGXpsloaWqTE0Pm65ZXlpMQA6ULvyQJdwNGGpFtwbEhgcAuIB45CMGBPAHxqT1kF/JxTAt6KW0VZmawaWuVORvB87TxAiKOMHyXuewJI7+DaAgJyZUGhhADgOAQGVZHL7zNgYQke52oIWFrZrbz3DInVeZ3XfRTJ6oOUlpf2/cUcaqTikAglBADPeQggAaVJ8Sckhfz3LoYAp7Nss9tITM3678kDxyIGApyOjo68wvy+vx5ULgkOP2OKFBVRQgBwPMJ3ABIAGRn+/gzp2RlArgsRBAV5BV79Ti+mEUTT2dBRFVB/OoggFJYU9bEbDBdQd2EcOZ0yUAB44NjBxXC1EPgSfusA8wrzecePELJAUZTXh6+pqYmsjLqGTvv+KkpQAMgnLSudr9cDW4bz/waETwACAeoJQJpPe5+Ojo6UjDQgl4YIAm/F3dbeBqq5K7+UdKpJVldVIyUGSGfikxP5ej2oLSAcSxCK+ApTQwBwbAGR9ASAIEh0QgyQS0MEQVVZlfd1Tn4eKTHkFxXwvra2sCIlBgiPjo6OhBQ+BQDQMpwDzwAEAccZAKj8LRw7cZFxMRSp4pNYWE2sRtZ/TpxJ6SnCj4HD4XR+FiSiCSWELxLTkvl1eAaVTY4jE4EiZwCUCALHGQC/HTh7gkajSUlJ8fU8UVFVkZ6daWtpDSQASN/p6OhISksOigj5FBrU+azvc0TI4lkLhGwHlJGT2Tn1849j+z2c3fw8fLzdvZQYisKMBMIlOCKU3yGgluGt/Jw8cwGlPQJCiSBwbAH10jqKX2SkZfid7UPIRygAQqOjoyM9OyM4MuxzeHBtd22Ea+tq45Lj3Z3chBnVx9Cgzt92dHRExkVHxkXTLtGc7Rz9PH183b15J9UQomGz2SGRfAuArAwYT0kcTwCgjh8EhBICgON3AeoJAEEQugzfAvApLPibmQvgUz+hcDicxNSk4KiwsOjwr7ZeefL6uTAFoKm5+VNYcLf/i8PhxCbFxybFn7p8zs7Kxt/Lzx9aQxPP54gQfvd/6DIyoEyFcTwBSPFZgUQQlBAAOp3vOynAJwCGAoPftw6rifU26P3YYaNAxQDhwVvvB4UH13W33kcQhEajOds5dnR0xP//oV9MYlxWXo6lqblwgnz25gWricX9WpYuO2n0+KLS4oTkxM5vJAzDUjLSUjLSLly/ZGNp7efh4+/pA+1CCeLl+0B+hygylEBdHcftSEqKHP+SLlBCAHA8iPFV7tE7ioqKCP9OXk8Dn48eMkL4NvTiSnt7e3xKYkhUWHh0RE96TJeRcXV08XX35u6zt7W3rfluY3llBff//n3z8p5vdwnBF6iuvu7Bi8e8b2dNmj5lzETk/9f+wREhYTGRnY8HMAxLy0xPy0z/++YVblvgAJ9+hnoGRMcpOSSkJOKo0FRkMEAFgKM2iCwDqy5QQwBk+RaARnANnZUV8SwESspLP4QGDek3EFQYkgm7rS0+OSEkMjQ8Noq3pu6CLF3Ww9nNz9PH08VDoVPzVRlpmYXT5/118iD326S0lI+hQYP8BxAd8/nrl3ihqqmqjRs2mvs1jUbzdHH3dHFf3d4Wl5QQEhkaGh3R5dZQUFxYUFx48+EdYwMjbgNkYbYyFlduPb6LY5SSIrCzeoD95YUMJQQAx7NYI6v7mwWuq+N8H9x4cCvAxx9IT1FJo5XdmpCS1O0tkocsXdbFwcnf09fXw7snx7d+3n7uQa4xiXHcb89cvWBnZUto7/XAj2+Dwv/b/Z83ZdaX+8gy0jLcm/vKha2RcdGfI0KiE2K7nFpxleDes4c6Wtrebl79vHxtLW0osioULeKTE5LS8OQBMxSACQCO25EUSonNA0oIgLIS3wLQ1NzU0dEBZAdGCdcTAIIgFVWVT14/5z7+Q/oCk9UYERMZFhMRlxTfUycGJYaij7uXr4ePq6NzX8R18eyFCalJXHu4puamvcf3//7dLwRZhCalpZy9doH3rbO909CAwb28XpYu29/bv7+3f3NLc3hMZEhUWGxiXJd/eHllxZPXz568fqatqeXj7g2VgC84HM7565fwjQW4BcTi/wlAEdzzhyBQQgBUlJS//qL/BcOwpuZmIH9CQZ4Ebz6808/Lj9AlpxjAbGRGxceERIXFJMb15L+tpKjk5eLu7+Xn5ujCV3a2kb7h3CmzLt++xv02Jz/3z+MHdm74ls6nycdXSctM/+3In7zbt4K8/JpvlvfxTi0vJz/If8Ag/wHNLc1R8TGh0eHR8TFdzrEqqiq5SqCpruHn4ePr4WNvbQsPmXrn0aunBcWF+MYyFMAIALutDUejQFNDYyBXFxBKCAC+v0QjiwlEABQFeB+0slvPXD3/46bv4JLtSyqrq0KjwsJiIlIy0nqqnebe7Pw8feys8N/sJo+ekJmTFRIVxv02Nin+lwN7vt/wrYK8As7QvyAsOuLgmaO8ZA8URTev2KCrrcvvPPJy8gE+/QJ8+rHZ7OiE2NDo8Mi4qC6GplU11U8Cnz8JfI5bFCWEotLiGw9v4x6upqIKJAwcy38EQdRVKZEZTIl3lZoKHiOtBiYTxyfwS3BvAXGJToh99ubFuOFjBI9EPKioqgiPjQqJDE3NTO/pvq+rpePv5evr4W1tbiW4dqIoumn5upr62rTMf80gk9JStv7y3bbVm8yMTQWcnN3W9s/9m49ePuH9W1AUXblgmZerhyDT0ul0P08fP0+ftva2lPTUyPiYT2Gf6xvqO7+G2ch8F/zxXfBHJUUlHzdPP08fF4c+bYtJAhwO58j5E4LUA2mqawCJpPZ//2p9BFQJgoBQQgBkcVVU1fDT/LMXBPdxvHz7moONveD3GpEmv6ggJCosNCq8s0VaFwz1DPy9fP08fMxBt3Gn0+nfr/92yy87Kqr+zQotLivZ9uvOqeMmTR07Gfd2UExi3PnrfxeXlnT+4aJZC0YNHi5oxP+PjLSMi4Ozi4PzopnzE1OTQqLCw2IivlSCN0Hv3wS95x2M+3n4yMnJgYpBFPn71tWM7ExBZgDVSpqvLsQ8KLJnQA0BoNNlpGX4bc/brSsADgR/H7Db2n4/+te+H39XBfRQKUIUFhd9jgwJjgjtpVeambEpd5/H2MCIuEiUlZT8PX0evnzC+0lbe9vNh3fefHo3afSEYQGD+34yzPV1ePTqSXJ66pf/d1ivB7+4odForo4uro4uKxcsTc5IDY0KD4sOr66t6fyaVnZrRGxURGzU6SvnPFzc/Tx8PF3cSWyKSRZBYcFPXj8TcBItDU0gweATABnQZ1T4oIQAoCiqralVXFby9Zd2oq6hDsjVNTU0UBQV0OCzoqryj+P7d2//GfjZIwXBMCw9KyM0JiIkMoy34u4CiqKWphZ+nj7+nr56OgB26r5KK7s1LDriy59X1VSf/+fva3ev+3n6erq4O9s59ZR1xmQ1xicnxCbFRyfE9vKpfvz62exJM4DF/QVSUlJOtg5Otg7L5i5Kz8oIjQ4PiQrv8ntuaW0NjggNjgjlFsd5urj7evjgSKYQRZLTU45eOCHgJFJSUqBaOOBYiaqpqFLkj0UJAUAQRF1VjV8BALUFJCMto6GmXlVTLeA8aZnpfxzdt3PDNnHdpcUwLC0rPTgyLCQytMvKlAeKonZWNn4ePn6evqBWWH3k0q2rZT1XdLe0tr4P/vg++COCIJrqmga6emqqarJ0WQRBGhobautqq2qqa+pq++ILe+fJfTdHVyG4AaIoamtlY2tls2jWAu6T1ufwkKLS4s6vYbe1cZ8Jzl676GznxDWhw5FXLSrkFxX8fvQvHFk3XVBXVQPVkqWO/zMA4SyJ+gJVBADHzQLfk1e36OnoCS4ACILEJMYeOH1k26pNFGn3AwQOhxOfkhgaHR4eHVHPbOj2NdxFK/fuo0ZGb6zQqPDnb191/om+jp63m+eH0KAvDYWqaqqqaqr6OLO7k6ucrFxodDjvGZHD4Rw6c/TQr/s6lyUTjZGB4WyDGbMnzcgrzA+NDg+JDOuS/tje3h6TGBuTGHvq8llbS2t/L79+Xr4USTUBRU5B3q59u4EUgYI6AUYQpKaHxVAvUGeNSBUBwHHXqKgC1ghQX0cvMTUJyFShUeG/HPx9x9qtwrw7EAHXpCEyLvrLM0ke0tLSLvZOfh4+PqSuOiuqKo//farzT9RUVH/Z9oO2pvb8aXM+R4Y+f/OSX68YKSkpdyfXSaPGO9k5Igjy5PWzzgVHZZXlpy6f3bJyA4jw+cPUyMTUyGT2pBlFpcUhkWGh0eE5+bmdX8DtWso1oSPraYwI0rMzdh/8A5TpgqY6sF9IeQ+7oL1AkRQghEICwP/xKUAB0NXWATUVgiDxyQk//rnrh03fgUo0FiYtra3R8TGh0eFR8TE9mTTQ6XR3J1c/Dx8vVw9Q1TS44XA4B84c6bwqRFF047K1XN9NaWnpQX4Bg/wCistKIuOioxNik9NTOBxOT7MpyCs42to72zv19/bv/OcbP2JsalZ655Yjn8I+uzg4E3Qg3BcM9QxmTJg6Y8LUsspyrhJk5mR1Psr6z470xmVRN6ELjgg9fP44QBN4Q31gv4eKSr4FgDoZg1QRgM4tXvtIc0szk9UIpPsS8C25rLycDT9u3bx8naujC9iZCYJnzhMSHd7S0tLta+Tk5Dyd3bmmbNTphXDj4W1e+j+XyaMnfPlrN9DVNxilP2nU+Kbm5uKy4uy83NuP7/JOMlwdXcYOHaWnrWugp99TPdqab1Zk5mR1Xnacu3bB1tKa9FuqrpbOlDETp4yZWFVTFRoVHhod/mXlHc+ETjgZWQDhcDjXH9y69+wh2D6shrpg/mrcuxC/o3Qo4wpOFQHQ0cbzG6morAAiAPo6eoJP0oX6hvpfD/0xc8K0aeMmU/ZIgGvOExodHpeU0FMaLkOB4e3m6efh4+bkSrUcp/iUxLtPH3T+iaWZxdwps3oZoiAvb2VmaWVmWV5Zfv/5I+4PF89aYPK10nyGAmPT8vXf7/2Zd1Dc0tp64NThv376nSJbuprqmuNHjB0/YmxtfV1YdHhoVHhiWnKXY+3cgrzcgrzrD26JhAldZXXVgdNHUjPTvv5SPjEA9ARQVsG3kzyCIABr1AWEKgKAr1FGRVWlBYgeIIZ6BtLS0j3Z1OCGu3gJiQpbu3illZkl2MkFoU/mPAxFL1cPKvsQ1DMbDp893nlhKCcnt3n5+j5H+99dr49D7K1tZ0yYevPhHd5PcgryLt26tmzuoj7GLBzUVFRHDxk5esjIBiYzPCYiNDo8PiWxyx+aZ0Knqa7p4ezm5erh7uRKnZUKhmGvP765dOtqF58MIKAoaqALZs2HbyOaOmlaVPlgqyqr0Ol0fvf4SstLgVydRqMZ6Or3UsIqCHmF+d/u+WHEwGGzJk4jt1Kspq4mNCo8NCo8OSO1p3xHZSUlT2d3fy8/St0OvgTDsGMXTtbU/U8Cxsr5Sw309HHM1vcl8KyJ05PTUzunDDx788LZztHH3QvHdYlGWUlp+MChwwcOZTWx4pISouKjQ6LCupjQVdVUvfoQ+OpDoIqyio+7l5+Ht7OdE7mSn5SWcuHGpS6H2wDR0dSW5b8LYbcU/29Wbh8x0jcEcnXBoYoAoCiqrKjc9+Q8LsWABABBEBNDY4IEAEEQDofz4t2r9yEfJ40aP27YaAHdh/iloqoyJCosNDo8PSujZ1M2TT9PHy8Xd0dbByrf93k8eP4oMi6680/6e/sPJr4/D/eEeeOPW3k7vxiGHb1w4rDJfipn2jAUGP28/fp5+61auDw+JbHbTgz1DfWvP7x5/eENQ4Hh5eLu6+Hj4ewm5HyVzNys6/dv8Ro8EIQhuPsvjluQspISdSwDqCIACIIY6OrxLQCl/NWO9YKJIeFnYi0tLTcf3nn44vHQgMHjho8h4uCBB4Zh2Xk5kXHRUfHR2fm5Pd339XX0uOY8FqbmlN0I/pKMnKxr9292/omuls6aRSv5mgT3P1dTXWPNopV7j+/n/aSRxTp09tieb3+mvnsznU7nNqtZ9c2/vdi6NLBEEITVxPoQGvQhNIhOp7s6OPfekwcIGIZFxcc8CXwen5xA3FV4GIFLAcJxC6LO8h+hlACYmZjxenz3kaKe/Wf4xcTQBNRUvdPS2vrszcvnb1/ZWtoM7jegn5c/wMYULa2tsUlx0QmxUfExvRTK6WrpeLl59vPyFcV+hC0tLYfPHuucykmj0TatWM9v4YUgSSV+nj4jBg59/fEt7yfJ6Sl3nt6fOWEa/kmFC13m37Zl6zo60rMzgiPDgsKDuxTNsdlsbpmx7GVZD2c3f09fDxd3sAUuuQV5H0ODgsKDgVRi9hGAGVBF/G8BGZCdNtYZCgkAjnQ6JquxgckEcqJiaiQkAeCCYVhqZlpqZtqZqxdsLW3cnVxcHV3NjEzwbb+UV1ZExUdHxcckpib34qlnYWru5+Hj7+mLb6OcIhy/dLqLa8icyTOFYMzQhaVzF6Vmpne2wLv58I69tZ2TrYOQIxEQKSkpOytbOyvbxbMWcJXgc0RIl9VDK7s1JCosJCqMRqPZW9u5O7l6urjjvo02NTcnpSXFJSfEJMaVlpeB+Efwh6WZBZB56pkNzEYmv6NIzxvuDIUEAN9zWWFJoYONveBX19LQVFVR/dI2gGg4HE5yekpyesrVuzdk6bIWpmbW5lYGegZ62jq62rqa6hrd7szUN9QXlBQVFBWkZ2emZKRWVve2dcYtAhrg299AV4Tv+1wCP74NCgvu/BNHW3vBu3LiyDGXpctuXrlh+687eYrb0dFx5NzxI7v3k14ch4/OSpCSkRYaHR4aFdbF9InD4SSmJiWmJl2+fU1TXcPC1NzU0MTE0NjYwEhDXaPbhwN2W1tZRVlZRXlJeWluQV5WbnZxWQnYpH6+kJeTB7UJU1iMZwcCCkD34DuZySvMByIACIJYmVl0OVcUMq3sVm7pJu8nKIoqKyopKioqKSp1cDiNLFZjUyOrqamXWlbeQDsrG38vPz8PH4C2J+RSUl564cblzj9RZDA2Llsn+M47vvMPc2PThTPnnf/nb95PKqurzv/z94ZlawWMh1ykpKQcbe0dbe2XzV2UlZfzIeTT5/DgLz0vq2qqq2qqw2MieT+h0WhKikpKiorKikqtbDazkclsbOxywEA6luCOu3IL8OQpwTOA7lFiKKooq/RkO9MTeYX5oAKwNCVZAL4Ew7B6ZkNPFmxfIiUl5WL/ryWkirIKobEJmfb29r9OHOycuIKi6Iala8jNvRk3bHRialLnm+C74I/e7l5+Hj4kRgUQS1NzS1PzJbMXco1gP4UG9fJu5HA4dfV1wn+M5gsrc2AVOThuPnQZGUpli1FIABAEMdI34FcAcguACYC1BYVqtfhFXVV9+MAhwwcMpdTbCyD3nj/MLcjr/JMxQ0d5u5GcfY+i6Polazbmb+28C3fm6gVHWwcgNeoUAUVR7u7Qwhlz45ISus0iFRUsTcEcACAIksu/ABjoGVAqVYxCoSC4Ho7yiwv64uHeF6xA9KcVPi4Ozt+t23b+wMk5k2eK692/pq7m7pP7nX9ibWG1eNYCUPMLsiWtyGBsWv4/21C1dbWXbl4BERflkJGW8XL12LBs7aXDZ1cuWEqp3Yw+YmUORgA4HE7h/zpy9wVKHQAgYiAAbDab304yPaHEUNQD0WVeaFiamu/e/tOv23709fAWieot3LwN+tC5B4iKkvK3azZTx6DCwcZ+xoSpnX/yPuSTMPMahY+cnNzoISOP/XZwy8oNulogzXQJRVlJCZ/rzJcUlRbj6EsD0IUUCFT5CHHB55KamZsNaiVib2NXAq66mDh0tXTmTp0V4NNPFB9ZcFBb/19WopSU1NZVGwH6uQNh5oRp3Mo77rccDicnP0dsjt97AkXRAb79fd297z57cP/ZI37begsfB2swCSMIgmTmZOEYZW5sBioAIFDrCcDM2AzHHS0rF89foltAJRQRB4qik0ZPOP7HoQG+/SXk7o8gyADfAO4ei7S09NaVG53tnciOqCtSUlJbV21ytP33/aOrpWNnbUduSEKDTqfPmTzzyJ79vH8+ZQEYYQYuAaBOJwAu1HoCUJCX19HSKavgrzYkPTsTVACONpT+0Gqqa25YupqCtz+isbW0/mHjjoTUpGEBgym77ywnK/vrtp8+hX3GEKSfly8ouzFRwUBXf/f2n+8+fXDz0Z2vpimTBbe/GxAyc/i+7TAUGFQ7paOWACAIYmFixq8A5BbksdlsIK5V2pra2ppaAHuNAaSft9/qhSsA+kaIFh7Obh7ObmRH8RVoNJoQDOkoi5SU1IwJU53tnfadPEjBIxAVJWVQJhCt7NZ8/k+AzY1NqfbUTq0tIARBzE343iPjcDi5hXmgAqDmLtD4EWO3rdoksXd/Yvgv84dqH0uRxtbSev9Pf4CyWwCIo50DqD90dl4ujqccU4rt/yDiIQAIgqRmAOsZ5AzuIREIUlJSy+ctWTrnG3iTIg4SnQnEEjVVtd92/EJ6lUYXANo0pWSk4hhlJlzDsb5AOQGwsbDGcadLAdc0zs3JlTq3Wmlp6e2rN48dNorsQMQc6vzFxQY5Wdnv1m0d6BdAdiD/AfAAIDkdjwCYg2hfCBbKCYAig4Fjny4lPRXUIk5NRVXIzqA9gaLo2kUr/TzFxFSAysAnACKQkpLasHRNPy8/sgNBEATRVNcAZYbI4XDSsvhecSrIy5t+re+08KGcACAIYm/Nt0k9k9VYwP+ZTE+4ObqCmkoQFs1aIMknisIEPgEQBI1G27Jyg4uDM9mBIN5unqD+yjkFuTg6FdtYWFPKBIIL5QJCEARfl5KktBRQAbg5kv9+nTJm4sSR48iOAgIRFBqNtmPtVhOyF79erh6gpkrGdauhZvMlcRKAZFAB2FvbEdoA76u4OrosmD6XxAAkDbgFRCgK8vI7129TkFcgKwA5OTmABwBJ6bgEgP+NDSFARQHQ0dJWV1Xnd1R8SiIoVzhpaWl3Z1cgU+FAWUlp49I1cFOCaDrf9OFvm2h0tXXX8tm0GSDujq4y0jJApmpvb0/kf60pJSVlDc6GGiBUFAAEQcxM+D6GZTWxsnKzQQXgQ14G28r5y9RU1ci6umQCnwCEQD9vv2EDhpByaYD7P6mZaS0tLfyOMjUyIXdToScoKgDOdnjcDmKT4kEF4OXqQYrZ5PCBQ/t5UyJrQuzpvOqHTwDCYcnsb3A83AuIlJSUpwswAYhLTsQxyoWq9i0UFQB8ZYTxKQmgAlCQVxB+RZi8nPy8KbOEfFGJBa76hY+CvPyiWfOFfFE7KxtlJSVQs8UlxeEY5eboAioAsFBUAGwtreXk5PgdlZaV0chigYpB+HWMMyZMVVVRFfJFIQgUAyEywLc/wIrcvtDf2x/UVPXMhux8vvsA0+l0e6paw1JUAGSkZRxt+H6XcDicyLgoUDH4e/kKs8uKjpb2+BFjhHY5SGfgFpAwmTd1ttCuRaPRAFaiRcZG4VgrONk6ALGqJAKKCgCCIO5OeB6aOrfnFhAVJWVh7gLNmjQDVKICBEJlbK1sPF3chXMtVwdnFWUVULNF4FpfUnb/B6G0AODy/o1JjGtpbQUVQ4BPP1BT9Y6KskqAD7AHVQiE4kwZM0k4FwL4EWaz2fHJeE4Z3ZxcQcUAHOoKgL6Oni7/HXpb2a2xuE5pusXP01c4z26jh4yAy3+I5OBgY2dBvDMaXUbGx90b1GxxyQk4Fpea6hqUbWGEUFkAEATxwFWNBXAXSEFe3tOZ8GdVGo02YuAwoq8C6QV4CCx8Rg4aTvQlvN28FOSBZd9HxkfjGOVO4eU/QnkBwLMLFBEb1d7eDiqGAX79QU3VE96unhpqwk6OhnQGHgILnwCffnQZYp96Ae7/dHR04FtZAixBIAJKC4CLgzOO8jlWEwugL5C3q6cawamZvtDwmQzgmp9cFOTlXYm03VVTUQVYAJyQkljfUM/vKBlpGVcKOKH2AqUFQEZaBt8DVBi4XSCiu7zSaDQh7DJBegduAZGCP5FLn2EDhgBM4w6OCsMxytneEUc9kzChtAAgCOLrgecMJyw6HOBHetiAIcRtETjbOcJOv6QDt4BIwc3RhaDfPIqiwwIGg5qNw+Hg2/8B+AhCEFQXAHyePLX1dWlZ6aBiMNDVt7OyATVbF1wpnCMs3sBbPumoqqga6hkQMbOboyuOHMKeSExNwrH/g6Io1boifwnVBQC3J8/74E8Awxg+YCjA2TpjY2FF0MyQ3un8hAi3gMjCwYYQj4QRg0B+YD+FB+MYZW9tp6muATAMIqC6ACAIgi+TNyj8cysbXEWYbz+A9YQ8pKSkzIzNgE8L4Re4BUQWpkamwOdUU1H1dvUENVsruzUkEs8BwABfIZWRCoIICICvuxeOz2dTc3NodASoGGSkZYYTYGVuYmgsJysLfFoIRFQwMzYFPueIgcMAHv+GRoU3t/DdARhFUX9PX1AxEIcICICaqpqNpTWOge+C3gMMY9TgEcC94UyN+O57A4GIE0b6gM8AaDTaiEEgyyrfh+DZTDbUNyBizwA4IiAACN6CjoTUpLLKclAxaGloersBe67kzQl2QghEtGAoMMC2yhrkPwDgznt1bU1CCp4OMF7Urv/iIRoC0N/bX0qK71AxDHsf/BFgGGOHjgI4G4Ig6rD1I0TiAVgGj6LoxJHjQM2GIMi7zx/wdRr39RCN6k7REAA1FVV8TSTeff4IMMHDyc7RGmjSDnSAgEAAtutyc3Q1MTQGNRuGYW9wbSNra2qLSnafaAgAgiABvng8eSqqKgDaQiAIMmnkeICzicQuIQRCKLJ0YHkQk0aBXP7HJsWVVZThGDgsYLCo5JWJjAD4e/ri69L+9vMHgGH4efro6+iBmo1GI6HvPORfYO4/NQBllmBiaOwMtPf6649vcYxCUXRI/0EAwyAUkREARQYDX2OdkMgwgI2CpaSkJo4C9hCA42ADAoF0y+TREwCuu2vraiNi8fT/cnVw1tbUAhUG0YjSDQifKVsru/X1h0CAYQzpPwiUP6iUiDwnQiDE0dYGwLxdX0dvAK5d4p54/fEth8PBMXDkYML7HABElATAz8MH36np0zcv8P0tu4UuIzNp9AQgU3XAXQgSgepLDdo5AARg5sRpAMt0OBzOK1yrRjVVNR/K+/90RpQEgEajDR+Ix+KjurYmODIUYCRjho5SVwWQwMNkNgg+CQQnUH2pQWMjU8AZ9HX0wLbvDgoPrq6twTFw1KDhwMtFCUWUBABBkJF4f7+PXj4BGAZdRmbyaAAnAXX8WwxCIGJGbX2dgDPMnjwD7G332ZsXOEbhXqGSiIgJgJqqqp2VLY6BWXk5yempACMZNWSk4Fn8ODxmIRBxAsMwAZdBRgaGYJf/aZnpGTlZOAZ6uXpQ3/6zCyImAFKo1CD/AfjGPnn9DGAkdBmZqWMnCTiJ4GsfCESkKa8sF/B8btbE6WCT7h8H4rxR9PPyAxiGcBAxAUAQZKBff4YCnhZaYTER+Mo6emLkoOG6WjqCzJBXmA8qGAhEFCkoLhRkuJmxKdjbbllFWWhUOI6BykpKXuA8qIWG6AmALF12kH8AjoEYhj0NxLO11xPS0tLzps0WZIbs/FxQwUAgooiAAvDNjHlgl//3nj/CZ/4zNGCwgjxIVzvhIHoCgCDIqMEj8P3VAz+9ZbIaAUbS39vfFpdVNZf6hvraulqA8UAgokVaVgbusd5unmA7qtbV133Aax/pJyLub10QSQEwMTR2cXDGMbCltfXF21cAI0FRdMH0uYLMkJ6dCSoYCES0wDAMtwDQaLT50+aAjedJ4HN2WxuOgU62DraWRLUNJxSRFAAEQXCbvj569aSpuQlgJA429oK0fo5OjAUYDKTvwD7ApFNQXMjEWwQwavBwYwMjgMEwWY3P377EN3ba+CkAIxEmoioA7k6u+P78jSzWo1dPwQazZPYCuowMvrGRcdHwTgSRTMJicDZtZSgwZk2cATaYJ6+eNTXz3foRQRBLMwt8NmVUQFQFAEVR3FmYj189A3sSoKutOwGvTXRtXW12Xg7AYCAQUQGf2xqCIDPGTwHYRQBBEFYT6ymu4i8EQaaPE9XlPyK6AoAgyEC/AHzOzE3NTWALgxEEmTFhCm4LwKDwYLDBQPqCqDi2iyvllRX4lj4Guvpjh48GG8zDl09YTXg8gzXU1H3cRcn8pwsiLABSUlKTx0zEN/ZJ4PN6oD48snRZ3EdSgZ/etbS2AgwG0hfgzhu5vAv+gONPgKLoygVLZaRx7rh2C7OR+TTwOb6xg/sNEmlTdxEOHUGQIf0Gqqng8WNoaWl58PwR2GACfPrh60fBamJ9DA0CGwwEQmUwDHuHq1PTkP6DwHZ9QRDk3rOH+Hb/1VTVJoHrDkIKoi0AUlJSQwLwNAlAEOTZmxc1dXgM/3oCRdFVC5fR6XQcY5+/fQkXpBDJITQ6vKKqkt9RSopKC2fMAxtJTV3Nc7yp4QE+/cAeRQgf0RYAGo020Lc/vs1cdlvb/eePwcajr6M3Y/xUHAPzCvM/h4eADQYCoSwPX+A5hFs0c76KkjLYSK4/uN3KxrMBKy0tPWHEWLDBCB/RFgAEQUyNTPw9ffGNffX+dVVNFdh4poyZaG5simPglbvX8RWhQCCiRXxKYno23/VfDjZ2wHvtFpUW49uJQhBkWMBgEWr92BMiLwAIgsybOhufGzi7re3y7X/ABkOj0VYtXI7jXKiiqgL3SRQEIipgGHbl9jV+R9FlZFYtXA48cevavRv4vEilpaWniXL2Jw9xEAADPf2hAYPxjf0U9jk5PQVsPNYWVviOhu4+fdDAFLQ7EgRCZT6GBmXxn/05d+psI31DsJGkZqaFReOsRBsxcKgYLP8R8RAABEFmTZyOuxb3/PVL+Pz/emHOlJkmhsb8jmI1sa4/uAU2EgiEOjQ1N1+9e53fUXZWtsB32zEMO3/9Er7MCzqdPmMCnqM+CiImAqCprjFi0HB8Y3PycwM/vQMbj4y0zMZla3FsTL18/zoyLhpsMJBugUlXwufvW1eqaqr5GiInK7th6RrgufZvPr3Lys3GN3bcsDFAWoJTATERAARBpo+fgtuP+597N/DVAfaCuYkZjhpxDMOOXTwFO4VBxI/45ITAj2/5HbVo1kI9HV2wkbS0tPyD91FbQV5h6lic9acURHwEQE1FdcaEafjG1jMbbj66CzYeBEFmTJhqbW7JdzAN9UfPn4RlARBxor6h/tC54/y+q92dXEcOGgY8mDtP7+PuwzF22CglRdHO/e+M+AgAgiBDAwbrauPs0fjszYv8ogKw8dBotC0rN+B4LolJjMVtTQXpI9AJSGh0dHQcOH2E33uuEkNxzaKVwDN/SspLH73E6QesrKQ0fMAQsPGQi1gJgJKiorujK76xHA7nwo3LQMNBEATR1dZds2gljoEXb1wOiQoDHg+EB3zCEhrnr1+KT0nkawiKomsXr9JU1wAezOnL59racRbcTBgxVlcb8H4UuYiVAEihUlPHTVaQV8A3PD45ISI2EmxICIL09/bHUcDS0dFx6OyxlIw04PFAIMLk3rOHz/h/nJ00aryvhzfwYILCgvmVIh7amlrAXUhJR6wEAEEQLQ3NpXMX4R5++sp54KfBCIKsmLfEQE+f31FsNnv3oT/yCvOBxwOBCIdXHwJx5H3aWFgDb/eIIEhzS/Pft67gHj53yiyGPANgPFRA3AQAQZCh/QdZmlngG1tdW4Pj/fpV5OTkvl//rbwc34cBTc1Nu/bvwVE4A4GQzsv3gacun+P34FeRwdi6agO+2v7euXz7n+panP6PBnr6g/wHgI2HCoihAKAoumT2QtzDX74PjEuKBxgPFwM9/TXfrMAxsLa+7vu9P0cnwNbBEJEBw7Dr92+dvsL33R9F0XWLV2tragMPKTE16eX717iHz5k8Uyw7CImhACCCNWrHMOzEpbMtLS1gQ0IQJMC337jhY3AMbGlp+e3In++CPwIPCcIFnyEMpFvYbPb+U4dvPb6LI5WZoK3/ltbW43+fwZ1a7e/pG+DTD2xIFEE8BQBBkEWz5uM2h6ioqsBdJ9I7i2ctsLWywTGQw+EcOXf8yLnjRCgThM1mkx2CmFBUWvztnu8/R+DxNndzdFkwfS7wkBAEuXTrSllFGb6xCvIKK+YvARsPdRBbATDQ1RfEr+PJ62dEZODQaLQda7dqqmviG/4u+OP6H7ekZaaDjQoCW3IKDoZhL98Hbv7525yCPBzDDXT1t67aRER7xYSUxJfvA3EPnz5+ipqqGsB4KIXYCgCCICMHj8BtDoFh2PG/TxGxMFRTUf1+47dysrL4hpdXVuzc+/PVu9fxNbGD8Oi87YOvJQiER3Fpyc/7dp+6fBbfb1JeTn7Huq2KDPA5Ni2trScuncW9+aOmojZ6yEiwIVEKcRYAFSXlRbPwnwYXl5bcegzeHwJBEHNj043L1uE+U+JwOHefPli+bc2TwOfAfUwlB2bjf87bLa1wYw0ntXW1Z69dXPfDZtz59SiKblm5wdjACGxgXATZ/EEQZO7UWbgXkSKBOAsAgiDDAgY72jrgHv7gxWPcloG94+fpM3MiTuciLsxG5vl//l7/w5ZXH97ALWwcNHQSgGo+LSohCIJU1VT/ffPK8u1rn715Icgp+sIZ87xcPQAGxiNesM0fbzfPEQOHAoyHgoi5ANBotE3L1+FIwOfC4XD2nTpM0GbLrInTB/fD2dGeR2FJ0clLZ5ZsWfnP/Zsl5aVAApMQGhrqeV+X89+gXGLBMCwuKX7vsf3Lt615+PKJgIuPYQGDJ4+eACq2zrS0tp4UYPOHTqcLUlIqKkiTHQDhaGlozps2+9y1i/iGl1WUnblybtOK9WCjQv5NeV5V31Afkxgn4FQNTObtx/duP75nbmLWz8vP283TSN9QLNOWAVLX0MD7uryygsRIRIL29vaUjNTQ6PDQ6AjcVppd8HRxX42rOKYvnL12QZDNnzmTZ+pq4XSWFCHEXwAQBBk3bPSn0M84+lBz+RAa5GTvNAxv18leoNFo29ds3vnHzzn5uUAmzMnPzcnPvXr3uoqSsoONvY2FlYmRiYmhkdj0rwAFk9VYW//fXay8spzEYKgJhmHVtdV5hflpWRkpGWmZuVlgdxqtzCy3rdpERMUvgiCfwj6/DXqPe7ilmcXEkeMAxkNZUAnxnc8vKtj40zbcO5VysrL7f94LvCspl9q62u17vq8gchdCli6rpaGpqqKioqwiLytHo0kjCMLp4DS3NLe2sptbmof0H0SEwlGW+OSEn/bt5n0rIy1z/dRl3IUjokjgx7fxKYkoinI3SGVlZWWkpVlNrOaWlqbmpoqqytLyUnYbTtfMr6Krrfvn97tVVVSJmLy4rGTLrh3NLTh3bukyMn/++JuFiTnYqKiJRDwBIAhiYmg8deyk24/v4Rve0tq67+Sh/T/9QafTwQaGIIiaqtov237c+ftPxDUCa2W3FpUWF5UW9/QCDqddogSgi71SW3tbTl4Ovho9UaSuvu7EJfyVsQKioqT885adBN392W1t+04ewn33RxBkwohx5sZmAEOiMmJ+CNyZmROnCbKEzy8qEMRKsHf0dfR+3vI9EXnQfaSguFBCngW5ZOd39ddLzZKg8rrIuGiy/twK8vK7tv6gr6NH0PxnrpzLxVWJxsVQz2D2lBmSc34mQQIgIy2zXLCS7udvXwWFBYOKpwtmxqY/b/5eTk6OoPl7p6m5ubishJRLCx8Oh5OYmtTlh7iPiESRqIQYUq4rS5fdueFbcxOi1tdBYcFvBNj6R1F0/ZLVMtIStBMoQQKAIIiLvZOHs7sgM5y6cq6iiqiMEWsLq53rt5O1Ey05hqNJ6SkNTGbXH6YmS4glHLutLS45QfjXlaXL/rj5OycB6nJ6p7is5MSlM4LMMHbYaMnZBuQiWQKAIMiOtVtMDI1xD2c1sf46cYi4wzEXe6edG74l4qThq0TGRgn/oqQQFh3+5Q+ZrMb4FBJui8InPjlB+JaCMtIy367dQtzdv5Xd+ufxA4Js/evr6C2cQYgVHZWROAGQlZXdtnqTIKvszNysExdPAQypC26OLt+v3y58DUjJTCPuFJo6YBgWHtN948+QqG6EQfwQJD8SH9LS0jvWbfFwdiPuEmevXcwvKsA9XEZaZtvqTbJ0nA5doovECQCCIMYGRtPGTRFkhg+hQfeePQQUTje4Orr8vPl73IZx+OBwOO8+fxDmFUkhNim+p7ZQYdERYr8LVFtXGxEn1Ec9aWnpb9ds8XQhxOyBy/O3L998eifIDEvnfmNhKhF5n12QRAFAEGTK2IlWZpaCzHD17vUIIvdMHG3td27AbxqKj7dB78U+F+jxq6c9/S9mIzO0u90hceJN0HthihydTv9u3VZvN0/iLpGSkXbhxmVBZvB29RRvy89ekFABoMvQl89fLEjKDYZhB88cFeSp86u42Dvt2bFLSVGJuEt0obisJCqenPwQ4ZBfVND7+eeT18+EFozw6ejoEHClzBdycnI/bPiW0LV/RVXl3mP72tvbcc+gpKj0zcz5AEMSLSRUABAEsbGwXj5XoKzQ5pbm3478+WU+CUCszCx//+4XYRo53H6Cs1ZOJHj06mnvjzhpWRkZ2ZlCi0fIfAj5VCYs0wslhuLubT+5ODgTdwk2m/3n8f31zIavv7Rnvpkx31DfAFRIIofkCgCCIEMDBg30CxBkhvLKir3H9wuyAPkqxgZGe7/frautS9wlOpORnRlPRo6gECguLfkYGvTVlz18+UQIwQgfDodzC28lPL+oq6r/9t0v1hZWxF0Cw7ADZ450qejml2EDhwzpL6gjr0gj0QKAouiab1YI6PCTnJ7y962roELqFh0t7d927DI1MiH0Kjz+vnVVLPvMXLhxqS9SHRIVlpoJvhso6bz9/EEQd8y+Y6Cn/9ePvwmSbN0XLt68EhYdIcgMejq6y+csJsiNTlSQaAFAEEROTm77ms0C5lw+DXz+7M1LUCF1i6a6xh87fyX0gZpHbkGe8DMFiSYiNqqPlW4Yhl26dVXMDsNbWltvE9Pergu2ltZ/7NytpYGz63UfefbmRS+H+X1BTk7uu3XbyCq8pw6SLgAIgpgYGq+cv1TASc79czEonCiXCC4K8go/b945ctBwQq/C5fLta3ViVBPQ1t528SYfiSJpWRmfI0KIi0f43Hh4u7K6iuir9PP22739ZxUlZUKvEp0QK2DaD4qiW1dtFNojNZWBAoAgCDJswJABvv0FmQHDsMPnjsclxYMKqVtoNNqqhctmTZxOtFkVk9V4Fm8LHQpy+da10nL+dj/OXbsoNhKYV5hPdHYTiqKzJk3ftmoT0QWMGTlZf508KGAm69ihozycCKxKEyGgAPzLigVLBXQobG9v//3ovrRMYk0lURSdPXnGtlWEVy0GR4aKx0ZQVHzM0zcv+B1Vz2w4deUcEfEIGQ6Hc+ziKUJz/2WkZdYvXTN7EuEmmqXlZb8d+VNAHwtrc8uFM+ZJ+NY/DygA/6LEUPxx03cMBYEMmVvZrXuO/FlYUgQqqp7o5+2394c9RO+0nr56XhBnXSpQXVtz5PxxfBv6YdERwsyaJ4grd69n5WYTN78SQ3HX1h+GCNzd+qvU1NX8tG+3gI9lcnJym1aslxVufSWVgQLwHwZ6+ttWb5KSEuh3wmxk7tq/h9D2XlzMjU33/fg7oZl2bDZ738lDTFYjcZcgFHZb2/5ThwQp1Dhz7YJI20SHRUc8IjKr1dLU/OAvfzra2hN3CS5NzU2/HvxDcCPedYtWGejqAwlJPIAC8D+4O7kunrVAwEmqaqp/PfQ7s5HAAjEuaqpqv3/3y/jhY4i7RHFZyR9H/2prJ8r9lDgwDDt89lhKhkAJnWw2+4+j+4RwfEoEhSVFh8/hfPrpC4P7Dfxj525tTW2C5ufR3NK8a/9vgj+MThkzKcC3H4iIxAcoAF2ZMHLciEHDBJyksLjo14O/C8F0V0ZaZuncRZuWryPuSCA5PfXIuRMiVxlw5ur54MhQweepra/bc3gvq4kl+FTCpKqm6teDvwtij9wLdBmZNYtWbly2VgietWw2+7cjfwn+HObt5rlg+hwgIYkTUAC6YdWCZRYCNy3KyMn67ehfrexWICH1ziD/AX/+sEdPh6hq4aDw4GMXT4lQavz1B7devHsNara8wvwf/vxFCI90oKhnNuza/xtB+5B6Orp//fj7iIFDiZi8C23tbX8c2/dl+zZ+sbawEnx3VyxBRehTLUwKS4q++/1HwX1+7K1tf9q8U15OHkhUvdPU3HT879PBEQCWvd0yyH/A+iWrKZ4+gWHYheuXngQ+Bz6zubHpL9t+UlYSnjcfPhqYzJ/2/UrQ6b2/p++6JasU5BWImLwLHR0d+08fFvz9rKuts++nP4iuThBRoAD0SFZu9vd7dwn+EG1nZfvT5p0K8sLQAARB3gV/PHP1PEG7Tx7ObtvXbBGySXXfaWtvO3z2OHE1XEb6ht9v+Ja4Jy3BKS0v+/Xg7yXlpcBnlpeTXzRrvnDqEBEEaW9v33/6cKjALXoYCozfv/vVzBjWfHUPFIDeCI0K/+PYPsHnsTQ137XtRyWGouBT9YXi0pL9pw/n5OcSMbmpkcl367fpaukQMbkgMBuZfx4/kJiWTOhVFOTlN6/Y4OVKoMUxbjKyM/cc+bO+oR74zNYWVpuWrxOwUKbvtLJb9x7bH5MYJ+A8UjSpnzbtdHdyBRCTmAIF4CsER4buP3VY8DoaC1PzX7b+IDRz/7b2tn/u3Xz48gkRf18lRaWNy9Z6urgDnxk38SmJR84d76nVF1ikpKTmTpk5Zcwk6uwpYxj2/O2rS7euAO9WTaPRZk6cNn3cFKH9Y1taW38/8md8SqKA86Aoun7p6qH9BwOJSlyBAvB17j97dOk2AL9P4W8ip2SkHTl/gggPSBRFRwwctnj2QtK3gzgczu0n9249uivkd7K1hdWGpWsM9ci3kq9nNhy/eIqI/nQGevqblq8TsHceX7CaWL8e/D0tC0Dtxbyps2dMmCr4POINFICv09bWdvHGlWdv+bYT+BIjA8Pd239WU1EVfKo+wmazbz66c//5IyL+0Po6eivmL3F1dAE+cx9JyUg7d+1CDknlynQ6fdbE6eNHjKXLyJASAIZhH0ODLt26WgvatohGo40ZMnLetDnCFPj6hvpd+/cA+WsODRi8YekawecRe6AA9Il2DufgqcOfQeSVmxga79r6vTCbfCEIEp+SePLSWYLs4AN8+s2bOktoLWu4lFWUXblzHUimv4BoqmvMmDB1xMBhRDvhdCErN/v89UtEtC6wNLNYu2ilmbEp8Jl7oaau5qe/dgOxUfFy89y2aqOcrKRbPfcFKAB9hc1m/7xvd3JGquBTqauq/7T5OyF/wNhs9r1nD+8+e0BE/zIajTZy0LDJoydqa2oBn7wLldVV3AYMlKpPNjc2nTByXIBPP2lpaaKvlZaZfu/5o8i4KOAfXu4zzeTRE4R8vFFRVfnTvl/5dWztFldH553rtkOj/z4CBYAPWE2sP08cBOL5zFBg7Fy/XQgmKl3IK8w/cekMQW1vaTSav5fv2KGjbC1tgC+HMQxLTE16/u5VeEwkZcuS1VXVxwwdObjfAE118D597La2yNiop2+eC+hv0RNerh7L5i7W0SLc2qELhSVFu/b/VlUDwG/D0dZ+15YfhFCfLDZAAeCP5pbm7bu/zy8qEHwqaWnp9UtWC9iUGAcdHR2vPgT+c+8mcS5v+jp6A/0CfNy9BH/KwTAsOz83Ki76c2RIYTHhNqtAQFHUzsomwKeft5un4ErQ1t6WnJYSFB4cEhXe1NwEJMIu6OvoLZu3yJ0Mi/y0rIzfjuwVvOISQRArM8vd3/4stIIb8QAKAN+UVVT8eui3opJiwadCUXTmxGmzJ80QfCp+aWSxbj68/eztS0JX09qaWo62DraWNjYWVoZ6Bn3cHmlvby8uKykoKoxPTYyKj6mtqyUuQqLR19FzsnO0t7EzNTTu+2+gvqE+v6ggIycrITUpNTONzWYTFJ4sXXby6AlTx00m5Rw7JCrs0NljQP51JobGv+/8VWilNmIDFAA8FJYU/Xbkz5IyMPWWIwYOXblgGSkWC9l5Oef++Vs4PdBpNJq+rp6etq6GmoaaiqqCvLysrJwUiray2S2tLU3Nzew2dlV1VUFxYWlFGaENTMiC+xvQ1tBSUVJWVlJWUlTibZSx29gNzIb6hoba+tqi0mIgK+LeQVF0cL+Bc6fM0lTXIPpa3fIk8PmF65eA3H/0dfT++H63MJPrxAYoADgpqyz76a/dZRXlQGbz9fDevGI90U2+eiIiNurizctAjuAgIoGzvdOimfPNBXY8xAeHwzl77cLL94FAZtPV0vntu1+Ibo4krkABwE9BccGvB/8A5bloY2H93fptZK1i2tvbX7x/ffvxXSGsPSEkYmxgtHDGPBKruJmsxr9OHEwQuNCXC7z7CwgUAIEoLiv5ef9vFZVgngPUVdW/XbPZ1soGyGw4YDWxHr18+vj1M4J85CEkoqejO2vi9AG+/Ul0sCgpL/3t8J9FpQDOzxB49wcBFABBKass+3nfb6WA/BelpaWXzF44ZugoILPhg9nIfBr44vHrp03NUAbEAU11zRkTpgwLGEKulXdMYuz+U0dAtdbR09H9bccuItJtJQooAADILyr46+RBgEmKg/wCVi9aQdaRAJfa+rp7zx6+/vBGOD1tIESgrak1efSEEQOHCaE8rRcwDLv//NG1ezdApZwZ6Rv+uv0nDTWhltOLJVAAwFBQVLD3xAEguaFczI1Nd6zbJvyqnC40MJnP3rx4+uZ5I0vEeiJKOHo6umOHjho1ZISMNDk+RTyampuPXjghuLM/Dyszy11bvxeasa54AwUAGI0s1m9H9ianA/CK4KKkqLRl5QY38qzWeDQ1Nz9/+/JJ4PM60KZjEODYWFhPHTvJ281TyN5E3VJUWrz32H4gDj9cHGzsftwkvPZKYg8UAJCw2ey9x/dHxceAmlBKSmrGhKmzJk6nwoe5vb09PCby4asnBDlJQAQBRVFPF/fxw8e4ODiTHcu/vA/+ePrKuZZWYFuILvbOP27aAZ0eAAIFADBsNvvI2eNBkSC7Enq5eqxbvEpFWQXgnIKQkpH29M3zsOgIsSzXEjkU5BWG9h80YeQ4ITjx9ZHmluZTl899DA0COKefp/emZeuhyxtYoACAp729/cKNS8/evAQ4p4qS8trFK73dvADOKSAVVZWBn94Gfnon0lYNIo2NhfXwAUMG+PUnN1+gCzn5uftOHgLbl3howOBVC5fRZeDaHzBQAIji3rOHV+78A/DXi6LoqMHDF81aQKlPO4fDiYiLev3hTWxSPHwvCQdFBmOw/8DhA4eaGBqTHcv/gGHY49fPrt65DtCpG0XRuVNmwd5eBAEFgEBCosIOnDnSxgZpW6+rpbNp+ToSi8V6oqqm6kNI0IfQT6Li2Sly0Gg0VwfngX4Bfp6+ZPUg64Xa+rpjF05GJ8QCnFOKJrVu8SrY15c4oAAQS1RczIEzwIpfuNBotOnjp8ycMI06Tck7k5Wb/SHk0+eIEOB9CiUWSzOLQX4BAT79VKnqdxYcEXrq8lmwBuMK8grb12x2d3IFOCekC1AACKeotPj3o38BLBHg4mBjt3HZWm1NkgsFegLDsOT01JCo0JDIMKgE+LAwNfd19/b38qVC6/meaGAyz167EBQeDHZaPR3dHzbuMNI3BDstpAtQAIRBXX3d/tOHE1KSwE6rIK+wYPqcUYNHUCFJtCe4ShAeGxkZFwUNR78Kt5mMj7u3v6cPZdWdR3BE6JlrF+ob6sFO62TrsGPdVljqJQSgAAgJDodz4cblp4HPgc9sYWq++psVlqbmwGcGTnFpSURcVHR8TEpmGkwh7YyykpK7o6u7s5ubo6uykgjc+Orq685euxgcGQp85hEDh65auJxc2yLJAQqAUPkYGnT84mng7jo0Gm3SqPEzJ06jVIJQL7S0tCSlJ8enJMUnJxQUF0rmm5AuI2NjYe1o6+Du5Gplbknlx7jOYBj27vOHv29dZTYCdg5HUXTWxOmzJ5PQIE9igQIgbLLycvYe2weqi0BndLS0V8xf6uFMQmdXQairr0tKT0nLTE/JTMsrzBfvJwNZuqy1uaWDjb2TnaO1hRUFk3l6p7is5MzVC/HJCcBnlpeT37pqo5erB/CZIb0ABYAEmI3MfacOxyXFEzG5t5vnivlLyerzJyAtLS3p2Rlp2RnZeTnZeTlVNdVkRyQoKIrq6+rZWFhbm1vaWFibGBqL6OYGm82+9/zhvacPAeb489BQU/9h4w4LUdjGFDOgAJADh8M5f/3y87cviPj9MxQY86fNGTV4uKjsKvREXX1dVl5ObkFefnFBUUlxUUkxEXcfsMjJyZkYGJsZm5gamZgamZgYmoiBc1lIVNjfN69WVFUAnxlF0eEDhi6YPkdZSRn45JCvAgWANDgcTnxK4qEzR+uZDUTMb2RguHD6PHF6puZwOGWV5QXFhWUV5eWV5aUV5WXlZZU1VWTtGsnJymppauloahvo6uvp6Bno6unr6olZi5LCkqKLNy7HJMYRMbmcnNzWVRs9nNxE9KlIDIACQDLllRV7Du/NLyogaH4XB+dvZs43NzYlaH7S4XA4tfV1VTXVtfW1VTXVtXW19Q31DY2NjSwms7GRyWpkNjLxKYSCvLycrLyCvLyKsrKKkoqaqqqSopKKkrKmuqamhqaWhqYSQxH4P4c6MFmNtx7dffbmBaguLl3Q1tTaueFbMX5nigRQAMiHzWZfuHH5xbtXBM2Poqi/l+83M+ZTxy1SyGAYxmpqamtjt7axm5qbO9/RWltbZel0BEURBGHIK6BSqIy0jLycnIK8Annxkkwru/Vp4It7zx6CrWDvjIuD87ZVm0Qi4VW8gQJAFd4Gvbt48wqzEWQxfWdk6bLjho+eNm6yJN/aIL2DYVhIZNil29eI2O7nQqPRls1bPGLgUGkamV0qIVygAFCFjo6O4tLiYxdPp2WlE3cVFWWV2ZOmjxg4DO66QjqDYVhYTMQ/924C7N71JZYm5kvnLba3tiXuEhC+gAJALTgczu0n924/vkfowaa2pva0cZOGBQyBMgBBECQ+OeHK3etZudnEXUJGWmbOlJmTRo2HbzlKAQWAiuQW5B05fyInP5fQq0AZgMQnJ1y9eyMzN4vQq+hq6Wxfu0Uk3EokDSgAFIXD4dx5cv/2k3vt7e2EXkhXW3fq2EmD+w2QkRaxqlQIbjAMC4kKu//8EaGrfi7+nr7rlqxiKDCIvhAEB1AAKE1eYf7hc8eJfhRAEERFWWX0kBHjh49VZMAPqjjT3t4eFB589+mDolLA/uRfoq2pvXzeIkr1MYV0AQoA1eFwOP/cv/ngxWMhlDspyCuMHjJizNBRIuokAekFZiPz1Yc3TwOfC6E9A41GG9J/0OJZC+DCn+JAARANUjPSzl+/RPReLRcURZ3tncYPH+Pp4i7qZhIQBEGKy0pevHv9+sMb4Da03WJtbrl28SpTIxMhXAsiIFAARAYMw56+eXH17vWWlhbhXNFAT3/UoOFDAwbDdZwo0tHRERUf/fTNy4SUROF8zOXk5OZPnT122GhqNiuFfAkUABGjvLLi3D8XI2KjhHZFuoyMl5vnhOFjKdiJHtItNXU174M/vXwfSFw915fYWFhvW71JYqvNRRQoAKIHu439OSz02v0bVTVVwryujYX1qCEj+nv50el0YV4X0kc4HE5kXHTgp7fRCbHC/FxrqGlMHj1h1JARItfeAAIFQFRpa297/OrZ7cf3mluahXldBXl5X3dvfy8/D2c3+KRPEQqLi96HfHz7+UMd8Qe8naHRaKOHjJwzeSZMHhNRoACINrV1tVfuXn/3+YPw/45aGpqD/AcE+PQzMTQW8qUhXCqqKj6FBX8I+USof0NPONk5Lp+3GP71RRooAOJAVl7OuWsXUzPTSLm6jpa2t5tXPy9fOyvo8SIMKqoqw2MjQyJDUzPTSfn8aqipz58+d7D/AJgkJupAARATMAz7EPLp8u1/aupqyIrBSN/Qz9PHy9XTyswC3hqAk1eYHxkXHRodnp2XQ1YMdDp96phJU8ZOlKXLkhUDBCBQAMSKltbWJ6+f3X/+iDgn976grKTk6ezu6eLh7uwqLyfyDRFJhMPhZORkRsZFh8VEFJeWkBiJqorqsP5DRg4ZqqOpQ2IYELBAARBDmKzGe08fPH3zgs1mkxsJXUbGztrO1cHZ1dHFzMgEPhb0kYLiwvjkhPiUxMS0ZKGVffQEXUZm+MBhE0eO09HShn9BMQMKgNhSU1dz69G9wE9vibaT6yOqKqqu9k72Nvb21rZG+oZkh0M5yirKUjLSElOT4pITSdzH64y0tPSIgUNnTJiqrqpOdiwQQoACIOZUVlfdfnwv8NNbgjq74kNFWcXe2tbeytbWytbc2FRaWhKbQ3E4nPziwpT0lNTM9JSMNIrc9Llw24gunD5XV1uX7FggBAIFQCIoLit59PLJ++BPwnGD4QtpaWlzY1MrcysrMwsrc0t9HT1xLS/AMKyotDgrNzs7LyczLzs3P4+Cfw4URQf6BcyaNF1fR4/sWCCEAwVAgqiqqX4f/PHx62f1DfVkx9IjdDrd2MDI3NjU2NDY1MjE2MBIRUmZ7KBwUt9Qn1dUUFBUUFBcmFeYX1BSRPqGfi+oqqi6O7pMGDXe3NiU7FggQgIKgMTRym599T7w/vPHlNpz6AVFBkNfV99QV19fV19fR09LU0tbQ1NVRZXsuP6H2vq6yqrKkoqy0vLSsvKykoqy0rJSJquR7Lj6hLqq+iD/AZPHTBBdrYXgAwqAhNLW3vbm0/tHL5+UlJeSHQse6HS6toaWtqaWuqqamqqairKKqpKKmqqairKyEkNRQYEB1pemrb2tgclsYDbUNdTXMxuYzIaGRmZldVVldWVVTU11TXVbexvAywkNc2PTaeOn+Hv6iuu2G6R3oABINBiGRcZFPwl8Hp+cQHYsgKHLyDAUGAwFhpysLEOBgaCoIoMhhUopyCv0NKSV3cq9jzc3N7ey2c0tzU1NTU0tTc3NLSJ6f+8FOyvbqWMnebl6wMxOSQYKAARBECQzN+vhiyeh0eEUyRmFEIQSQ3FwwCA/dx8HGzuyY4GQDxQAyH80slifw4PD4yJjE+MplTYKERApKSkPZ7f+3v7ebp6wvQ+EBxQASDdwO4q8ePeqoqqS7FggAqGmojqk/6DRQ0Zoa2qTHQuEckABgPQIh8OJio959SEwJjEOPhCIFtzGzqMGD/d196bRaGSHA6EoUAAgX4fZyAyJCnv3+SNZjtOQvqOhpj7If8DIwcN1taBrG+QrQAGA8EF+UUHgp3dh0eFwa4hSoCiqr6vn6ezu5+lrZ2UDE3sgfQQKAAQP5ZUV8SmJEbFRccnxpHuOSjKWZhaD/AICfPqpqaqRHQtE9IACABGIVnZrbGJ8RFxUZGxUPbOB7HAkAhRFLUzMvN28Anz6Gejpkx0ORISBAgABQ0dHR3p2RnhMZHhMZHEZma1LxBU5OTlnOycvVw8vV3fozwwBAhQACHiKS0vCYyIi42PSstI5HA7Z4YgwNBrNxsLKxcHZxd7ZxsIK5vNAwAIFAEIgzS3NianJqVlpianJ2Xk5UAz6grKSkq2lrZmxiY2FtaONvZycHNkRQcQWKAAQIdHc0pySkZaenZGWlZGRndHU3Ex2RBRCW1PbwcbO3trW3srOUN8ApvFAhAMUAAgJdHR0FJYUpWWlZ+Xm5BXm5xcXUNkonwg01TXNTUzNTczMjc0szSw01TXIjggiiUABgJAPhmFlFeW5hXn5hQV5RflFJcWlFWXiZEunyGAY6BoYGRga6hmYG5uam5grKymRHRQEAgUAQkk4HE5ZZXlRSXFxaXFRWUlRSVFxaYmo9FdRVlIyMzYz0jc00jcw1DMw1DdUo1j7GgiECxQAiMhQW1+XX5jf0MhkNTWVVZQVFBc2tzSz2eyq2pq6+jphRkKXkVFTVddQU1NRUlFTVVVWVNbV1tHS0NTS1GLIKygpwtU9RDSAAgARB2rr67gN1ltbW+sa6guKC2tq/6fhZVt7e2trK4IgHE57c2sLgiAdnI6mlu4PomWkZRQVFBQUFBTkGQwFBUUGQ5GhqKKkrCCvoKCgAG/xELEBCgAEAoFIKLARKAQCgUgoUAAgEAhEQoECAIFAIBIKFAAIBAKRUKAAQCAQiIQCBQACgUAkFCgAEAgEIqFAAYBAIBAJBQoABAKBSChQACAQCERCgQIAgUAgEgoUAAgEApFQoABAIBCIhAIFAAKBQCQUKAAQCAQioUABgEAgEAkFCgAEAoFIKFAAIBAIREKBAgCBQCASChQACAQCkVCgAEAgEIiEAgUAAoFAJBQoABAIBCKhQAGAQCAQCQUKAAQCgUgoUAAgEAhEQoECAIFAIBIKFAAIBAKRUKAAQCAQiIQCBQACgUAkFCgAEAgEIqFAAYBAIBAJBQoABAKBSChQACAQCERCgQIAgUAgEgoUAAgEApFQoABAIBCIhAIFAAKBQCQUKAAQCAQioUABgEAgEAkFCgAEAoFIKFAAIBAIREKBAgCBQCASyv8Bxrvbg8lYdFwAAAAASUVORK5CYII="

_MANIFEST = {
    "name": "Ed Nicholls Console",
    "short_name": "EN Console",
    "description": "Practitioner dashboard — Ed Nicholls Acupuncture",
    "start_url": "/dashboard?token=earseed2026",
    "display": "standalone",
    "orientation": "any",
    "background_color": "#4D5D53",
    "theme_color": "#4D5D53",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
    ]
}

@app.get("/favicon.svg")
def favicon():
    try:
        with open("favicon.svg", "rb") as f:
            return Response(content=f.read(), media_type="image/svg+xml")
    except FileNotFoundError:
        # Fall back to serving the 192px icon as a PNG
        return Response(content=_b64.b64decode(_ICON_192_B64), media_type="image/png")

@app.get("/manifest.json")
def manifest():
    import json
    return Response(content=json.dumps(_MANIFEST), media_type="application/manifest+json")

@app.get("/sw.js")
def service_worker():
    try:
        with open("sw.js", "rb") as f:
            # Must never be cached — browser must re-fetch on every load to detect updates
            return Response(
                content=f.read(),
                media_type="application/javascript",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Service-Worker-Allowed": "/"}
            )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="sw.js not found.")

@app.get("/icon-192.png")
def icon_192():
    return Response(
        content=_b64.b64decode(_ICON_192_B64),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"}
    )

@app.get("/icon-512.png")
def icon_512():
    return Response(
        content=_b64.b64decode(_ICON_512_B64),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"}
    )

# ── Practitioner dashboard ─────────────────────────────────

@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard")

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    # The HTML shell is public — the JS login screen handles auth.
    # All data API endpoints still require a valid token.
    token = request.query_params.get("token", "")
    try:
        with open("dashboard.html", "r") as f:
            html = f.read().replace("__TOKEN__", token)
        import base64
        # Inject favicon as data URI so it works without a separate request
        try:
            with open("favicon.svg", "rb") as fav:
                fav_b64 = base64.b64encode(fav.read()).decode()
            fav_uri = f"data:image/svg+xml;base64,{fav_b64}"
            html = html.replace("/favicon.svg", fav_uri)
        except FileNotFoundError:
            pass  # favicon not found — leave src as-is
        # Inject apple-touch-icon as data URI so Safari gets the logo without a separate request
        try:
            with open("icon-192.png", "rb") as ico:
                ico_b64 = base64.b64encode(ico.read()).decode()
            ico_uri = f"data:image/png;base64,{ico_b64}"
            html = html.replace('href="/icon-192.png"', f'href="{ico_uri}"')
        except FileNotFoundError:
            pass  # icon not found — leave href as-is
        # Never let Railway/CDN/browser cache the dashboard HTML — always serve fresh
        return HTMLResponse(html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="dashboard.html not found.")
