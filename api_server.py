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
def startup():
    init_db()


# ── Auth helper ────────────────────────────────────────────

DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")

def _check_token(request: Request) -> bool:
    token = request.query_params.get("token", "")
    return bool(DASHBOARD_TOKEN) and token == DASHBOARD_TOKEN


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


@app.post("/api/v2/patients/{patient_id}/notes")
async def api_update_patient_notes(patient_id: int, request: Request):
    if not _check_token(request):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")
    body = await request.json()
    ok = update_patient_notes(patient_id, body.get("notes", ""))
    if not ok:
        raise HTTPException(status_code=500, detail="Could not save notes.")
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


# ── Static assets ──────────────────────────────────────────

@app.get("/favicon.svg")
def favicon():
    try:
        with open("favicon.svg", "rb") as f:
            return Response(content=f.read(), media_type="image/svg+xml")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="favicon.svg not found.")

@app.get("/manifest.json")
def manifest():
    try:
        with open("manifest.json", "rb") as f:
            return Response(content=f.read(), media_type="application/manifest+json")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="manifest.json not found.")

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
    try:
        with open("icon-192.png", "rb") as f:
            return Response(content=f.read(), media_type="image/png")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="icon-192.png not found.")

@app.get("/icon-512.png")
def icon_512():
    try:
        with open("icon-512.png", "rb") as f:
            return Response(content=f.read(), media_type="image/png")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="icon-512.png not found.")

# ── Practitioner dashboard ─────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    if not _check_token(request):
        return HTMLResponse("""<!DOCTYPE html><html><head>
        <meta charset="UTF-8"><title>Access denied</title>
        <style>body{font-family:sans-serif;display:flex;align-items:center;
        justify-content:center;height:100vh;margin:0;background:#FAF3E4;color:#2C1A0E;}
        </style></head><body><p style="font-size:14px">Access denied — invalid or missing token.</p>
        </body></html>""", status_code=403)
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
