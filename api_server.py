"""
api_server.py
-------------
FastAPI server that:
  1. Accepts POST /reading with birth data + email address
  2. Runs the Ba Zi engine (bazi_calculator.py)
  3. Builds the Claude prompt (prompt_builder.py)
  4. Calls the Claude API
  5. Generates a branded HTML email
  6. Sends the email via Resend
  7. Returns {"success": true}

Environment variables required on Railway:
  ANTHROPIC_API_KEY
  RESEND_API_KEY

Run locally:
  pip install fastapi uvicorn anthropic resend pydantic
  uvicorn api_server:app --reload
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import date
import anthropic
import httpx
import os
import re
import math
import json as _json
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

# ── App setup ──────────────────────────────────────────────

app = FastAPI(
    title="Four Pillars · Elemental Constitution API",
    description="Calculates Ba Zi constitution and emails a personalised reading.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

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


# ── Request / Response models ──────────────────────────────

class ReadingRequest(BaseModel):
    name:  Optional[str] = Field(default="Friend", description="Preferred name")
    email: str           = Field(..., description="Recipient email address")
    year:  int           = Field(..., ge=1900, le=2100)
    month: int           = Field(..., ge=1,    le=12)
    day:   int           = Field(..., ge=1,    le=31)
    hour:  int           = Field(..., ge=0,    le=23)

    @validator("year")
    def not_future(cls, v):
        if v > date.today().year:
            raise ValueError("Birth year cannot be in the future.")
        return v


class ReadingResponse(BaseModel):
    success: bool
    message: str


# ── Health check ───────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "Four Pillars · Elemental Constitution API"}


# ── Email HTML helpers ─────────────────────────────────────

def _pentagon_svg(constitution: dict) -> str:
    """Generate the Five Element pentagon as an inline SVG string."""
    STATE_VAL = {"Absent": 0.10, "Weak": 0.30, "Balanced": 0.55, "Strong": 0.78, "Excess": 1.0}
    ORDER = ["Wood", "Fire", "Earth", "Metal", "Water"]
    W, H = 380, 300
    cx, cy, R = 190, 148, 88
    rMin = R * 0.12

    def pt(i, r):
        angle = math.radians(-90 + i * 72)
        return cx + r * math.cos(angle), cy + r * math.sin(angle)

    # Background rings
    rings = ""
    for frac in [0.25, 0.5, 0.75, 1.0]:
        r = rMin + (R - rMin) * frac
        pts = " ".join(f"{pt(i, r)[0]:.1f},{pt(i, r)[1]:.1f}" for i in range(5))
        rings += f'<polygon points="{pts}" fill="none" stroke="#D4C4A8" stroke-width="0.5"/>'

    # Axes
    axes = ""
    for i in range(5):
        x, y = pt(i, R)
        axes += f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#D4C4A8" stroke-width="0.5"/>'

    # Data polygon
    data_pts = []
    for i, elem in enumerate(ORDER):
        v = STATE_VAL.get(constitution.get(elem, "Balanced"), 0.55)
        r = rMin + (R - rMin) * v
        x, y = pt(i, r)
        data_pts.append(f"{x:.1f},{y:.1f}")

    # Labels
    labels = ""
    for i, elem in enumerate(ORDER):
        px, py = pt(i, R + 28)
        anchor = "end" if px < cx - 8 else ("start" if px > cx + 8 else "middle")
        col   = ELEM_HEX[elem]
        state = constitution.get(elem, "")
        labels += (
            f'<text x="{px:.1f}" y="{py-6:.1f}" text-anchor="{anchor}" '
            f'font-family="Arial,Helvetica,sans-serif" font-size="10" font-weight="700" '
            f'letter-spacing="1.2" fill="{col}">{elem.upper()}</text>'
            f'<text x="{px:.1f}" y="{py+8:.1f}" text-anchor="{anchor}" '
            f'font-family="Georgia,serif" font-size="11" font-style="italic" '
            f'fill="#A08470">{state}</text>'
        )

    # Dots
    dots = ""
    for i, elem in enumerate(ORDER):
        v = STATE_VAL.get(constitution.get(elem, "Balanced"), 0.55)
        r = rMin + (R - rMin) * v
        x, y = pt(i, r)
        dots += (
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" '
            f'fill="{ELEM_HEX[elem]}" stroke="#FAF3E4" stroke-width="2"/>'
        )

    return (
        f'<svg width="380" viewBox="0 0 {W} {H}" '
        f'style="max-width:{W}px;display:block;margin:0 auto;">'
        f'{rings}{axes}'
        f'<polygon points="{" ".join(data_pts)}" fill="rgba(61,90,76,0.08)" '
        f'stroke="#3D5A4C" stroke-width="1.5" stroke-linejoin="round"/>'
        f'{labels}{dots}</svg>'
    )


def _pillar_cards_html(pillars: dict) -> str:
    """Render the four pillar cards as an email-safe HTML table."""
    cells = ""
    for lbl in ["Year", "Month", "Day", "Hour"]:
        stem, branch = pillars[lbl]
        elem  = STEM_ELEM.get(stem, "")
        col   = ELEM_HEX.get(elem, "#8B6F5C")
        pin   = f"{STEM_PIN.get(stem, stem)}–{BRANCH_PIN.get(branch, branch)}"
        is_day = lbl == "Day"
        day_tag = (
            '<p style="margin:6px 0 0;font-family:Arial,Helvetica,sans-serif;'
            'font-size:7px;font-weight:700;letter-spacing:0.15em;background:#3D5A4C;'
            'color:#FAF3E4;padding:2px 6px;display:inline-block;">DAY MASTER</p>'
            if is_day else ""
        )
        cells += (
            f'<td width="25%" style="padding:0 5px;vertical-align:top;">'
            f'<table width="100%" cellpadding="0" cellspacing="0" style="'
            f'background:#FAF3E4;border:1px solid #E0D5C1;border-top:3px solid {col};">'
            f'<tr><td style="padding:16px 10px 14px;text-align:center;">'
            f'<p style="margin:0 0 10px;font-family:Arial,Helvetica,sans-serif;font-size:8px;'
            f'font-weight:700;letter-spacing:0.22em;text-transform:uppercase;color:#8B6F5C;">{lbl}</p>'
            f'<p style="margin:0 0 6px;font-size:28px;line-height:1.15;color:#2C1A0E;'
            f'font-family:serif;">{stem}<br>{branch}</p>'
            f'<p style="margin:0 0 8px;font-family:Georgia,serif;font-size:11px;'
            f'font-style:italic;color:#8B6F5C;">{pin}</p>'
            f'<p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:8px;'
            f'font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:{col};">'
            f'&#9679; {elem}</p>'
            f'{day_tag}'
            f'</td></tr></table></td>'
        )
    return f'<table width="100%" cellpadding="0" cellspacing="0"><tr>{cells}</tr></table>'


def _parse_reading_html(text: str) -> tuple[str, str]:
    """
    Parse ## headings and paragraphs from Claude's output.
    Returns (body_html, conclusion_html).
    """
    GRN   = "#3D5A4C"
    BROWN = "#2C1A0E"
    LGREY = "#E0D5C1"

    chunks = re.split(r'\n\n+', text.strip())

    # Extract conclusion: last non-heading paragraph
    conclusion = ""
    if len(chunks) > 1 and not chunks[-1].strip().startswith("#"):
        conclusion = chunks.pop().strip()

    body_html = ""
    for chunk in chunks:
        t = chunk.strip()
        if not t:
            continue
        if t.startswith("# "):
            continue  # top-level title — already in email header
        if t.startswith("## "):
            heading = t[3:].strip().title()
            body_html += (
                f'<tr><td style="padding:28px 0 10px;">'
                f'<p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:10px;'
                f'font-weight:700;letter-spacing:0.22em;text-transform:uppercase;color:{GRN};">'
                f'{heading}</p>'
                f'<div style="width:24px;height:1px;background:{GRN};margin-top:7px;opacity:0.7;"></div>'
                f'</td></tr>'
            )
        else:
            para = t.replace("\n", "<br>")
            body_html += (
                f'<tr><td style="padding:0 0 14px;">'
                f'<p style="margin:0;font-family:Georgia,\'Times New Roman\',serif;'
                f'font-size:16px;line-height:1.8;color:{BROWN};">{para}</p>'
                f'</td></tr>'
            )

    conclusion_html = ""
    if conclusion:
        para = conclusion.replace("\n", "<br>")
        conclusion_html = (
            f'<tr><td style="padding:28px 0 0;">'
            f'<div style="background:#F0E6D3;border-left:2px solid {GRN};padding:22px 26px;">'
            f'<p style="margin:0;font-family:Georgia,\'Times New Roman\',serif;'
            f'font-size:16px;font-style:italic;line-height:1.85;color:{BROWN};">{para}</p>'
            f'</div></td></tr>'
        )

    return body_html, conclusion_html


def _build_email(name: str, pillars: dict, constitution: dict, reading_text: str) -> str:
    """Assemble the full branded HTML email."""
    pentagon   = _pentagon_svg(constitution)
    pillar_tbl = _pillar_cards_html(pillars)
    body_html, conclusion_html = _parse_reading_html(reading_text)

    CR   = "#FAF3E4"   # cream
    CRD  = "#F0E6D3"   # cream dark
    BR   = "#2C1A0E"   # brown
    GRN  = "#3D5A4C"   # green
    BRL  = "#8B6F5C"   # brown light
    BDR  = "#E0D5C1"   # border

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Your Ba Zi Reading, {name}</title>
</head>
<body style="margin:0;padding:0;background-color:{CRD};-webkit-text-size-adjust:100%;mso-line-height-rule:exactly;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background-color:{CRD};">
  <tr><td align="center" style="padding:40px 16px;">

    <table role="presentation" width="640" cellpadding="0" cellspacing="0"
           style="background-color:{CR};max-width:640px;width:100%;">

      <!-- HEADER -->
      <tr><td style="padding:50px 48px 38px;text-align:center;border-bottom:1px solid {BDR};">
        <p style="margin:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:9px;
                  letter-spacing:0.35em;text-transform:uppercase;color:{GRN};">
          Bespoke Ba Zi Reading
        </p>
        <h1 style="margin:0 0 12px;font-family:Georgia,'Times New Roman',serif;
                   font-size:36px;font-weight:400;color:{BR};line-height:1.2;">
          {name}
        </h1>
        <p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:9px;
                  letter-spacing:0.18em;text-transform:uppercase;color:{BRL};">
          Four Pillars of Destiny &nbsp;&middot;&nbsp; Elemental Constitution &nbsp;&middot;&nbsp; 2026
        </p>
      </td></tr>

      <!-- FOUR PILLARS -->
      <tr><td style="padding:36px 48px 0;">
        <p style="margin:0 0 18px;font-family:Arial,Helvetica,sans-serif;font-size:9px;
                  font-weight:700;letter-spacing:0.25em;text-transform:uppercase;color:{BRL};">
          Your Four Pillars
        </p>
        {pillar_tbl}
      </td></tr>

      <!-- DIVIDER -->
      <tr><td style="padding:36px 48px 0;">
        <div style="border-top:1px solid {BDR};"></div>
      </td></tr>

      <!-- FIVE ELEMENT BALANCE -->
      <tr><td style="padding:32px 48px 0;">
        <p style="margin:0 0 20px;font-family:Arial,Helvetica,sans-serif;font-size:9px;
                  font-weight:700;letter-spacing:0.25em;text-transform:uppercase;color:{BRL};">
          Five Element Balance
        </p>
        {pentagon}
      </td></tr>

      <!-- DIVIDER -->
      <tr><td style="padding:36px 48px 0;">
        <div style="border-top:1px solid {BDR};"></div>
      </td></tr>

      <!-- READING BODY -->
      <tr><td style="padding:32px 48px 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          {body_html}
          {conclusion_html}
        </table>
      </td></tr>

      <!-- FOOTER -->
      <tr><td style="padding:40px 48px;margin-top:32px;border-top:1px solid {BDR};text-align:center;">
        <p style="margin:0 0 6px;font-family:Arial,Helvetica,sans-serif;font-size:9px;
                  letter-spacing:0.2em;text-transform:uppercase;color:{BRL};">
          Ed Nicholls Acupuncture &nbsp;&middot;&nbsp; ednicholls.com
        </p>
        <p style="margin:0;font-family:Arial,Helvetica,sans-serif;font-size:9px;
                  color:{BRL};letter-spacing:0.05em;">
          This reading is offered as a complementary wellness guide, not a substitute for medical advice.
        </p>
      </td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""


# ── Core endpoint ──────────────────────────────────────────

@app.post("/reading", response_model=ReadingResponse)
def get_reading(data: ReadingRequest):

    # 1. Four Pillars
    try:
        pillars = get_four_pillars(data.year, data.month, data.day, data.hour)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Pillar calculation error: {e}")

    # 2. Five Element analysis
    counts       = get_element_counts(pillars)
    constitution = interpret_constitution(counts)
    spread       = spread_score(constitution)
    balanced     = is_balanced(constitution)

    sorted_elems = sorted(constitution.items(), key=lambda x: STATE_RANK[x[1]])
    weakest      = sorted_elems[0][0]
    strongest    = sorted_elems[-1][0]

    # 3. Build Claude prompt
    user_message = build_user_message(
        name         = data.name,
        pillars      = pillars,
        constitution = constitution,
        spread       = spread,
        is_balanced  = balanced,
        weakest      = weakest,
        strongest    = strongest,
    )

    # 4. Call Claude
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured.")

    logger.info(f"Calling Claude for {data.name}...")
    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model      = "claude-opus-4-6",
            max_tokens = 1500,
            system     = SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": user_message}],
        )
        reading_text = message.content[0].text
        logger.info("Claude response received OK")
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        raise HTTPException(status_code=502, detail=f"Claude API error: {e}")

    # 5. Build email
    pillars_for_email = {k: (s, b) for k, (s, b) in pillars.items()}
    html = _build_email(data.name, pillars_for_email, constitution, reading_text)

    # 6. Send via Resend REST API
    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        raise HTTPException(status_code=500, detail="RESEND_API_KEY not configured.")

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type":  "application/json",
