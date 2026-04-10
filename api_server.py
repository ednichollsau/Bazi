"""
api_server.py  —  Four Pillars · Elemental Constitution API  v3.0
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

# ── App ────────────────────────────────────────────────────

app = FastAPI(title="Four Pillars · Elemental Constitution API", version="3.0.0")

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

PILLAR_LABEL = {
    "Year":  "Your roots & early life",
    "Month": "Your career & outer world",
    "Day":   "Your inner self",
    "Hour":  "Your dreams & legacy",
}

STATE_DESC = {
    "Absent":   "rarely present",
    "Weak":     "gently present",
    "Balanced": "in good flow",
    "Strong":   "a real strength",
    "Excess":   "very dominant",
}

STATE_PCT = {
    "Absent": 10, "Weak": 28, "Balanced": 55, "Strong": 78, "Excess": 100,
}

TIP_META = {
    "NOURISH": {"label": "Nourish",  "col": "#6B8F6B"},
    "MOVE":    {"label": "Move",     "col": "#B85C4A"},
    "REST":    {"label": "Rest",     "col": "#5B7FA3"},
    "MIND":    {"label": "Mind",     "col": "#7D8C8A"},
    "SEASONS": {"label": "Seasons",  "col": "#C4943A"},
}

# ── Request / Response ─────────────────────────────────────

class ReadingRequest(BaseModel):
    name:  Optional[str] = Field(default="Friend")
    email: str
    year:  int  = Field(..., ge=1900, le=2100)
    month: int  = Field(..., ge=1,    le=12)
    day:   int  = Field(..., ge=1,    le=31)
    hour:  Optional[int] = Field(default=None)

    @validator("year")
    def not_future(cls, v):
        if v > date.today().year:
            raise ValueError("Birth year cannot be in the future.")
        return v

class ReadingResponse(BaseModel):
    success: bool
    message: str

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
                '<p style="margin:8px 0 0;font-family:Cormorant Garamond,Georgia,serif;'
                'font-size:7px;font-weight:700;letter-spacing:0.15em;background:#3D5A4C;'
                'color:#FAF3E4;padding:3px 7px;display:inline-block;">DAY MASTER</p>'
            )
        cells += (
            '<td width="' + str(col_w) + '%" style="padding:0 5px;vertical-align:top;">'
            '<table width="100%" cellpadding="0" cellspacing="0" style="'
            'background:#FAF3E4;border:1px solid #E0D5C1;border-top:3px solid ' + col + ';">'
            '<tr><td style="padding:16px 10px 14px;text-align:center;">'
            '<p style="margin:0 0 6px;font-family:Cormorant Garamond,Georgia,serif;font-size:8px;'
            'font-weight:700;letter-spacing:0.22em;text-transform:uppercase;color:#8B6F5C;">' + lbl + '</p>'
            '<p style="margin:0 0 8px;font-size:28px;line-height:1.15;color:#2C1A0E;font-family:serif;">'
            + stem + '<br>' + branch + '</p>'
            '<p style="margin:0 0 4px;font-family:Georgia,serif;font-size:11px;'
            'font-style:italic;color:#8B6F5C;">' + pin + '</p>'
            '<p style="margin:0 0 6px;font-family:Cormorant Garamond,Georgia,serif;font-size:8px;'
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
            '<p style="margin:0;font-family:Cormorant Garamond,Georgia,serif;font-size:9px;'
            'font-weight:700;letter-spacing:0.18em;text-transform:uppercase;color:' + col + ';">'
            + elem + '</p></td>'
            '<td style="padding:7px 0;vertical-align:middle;">'
            '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            '<td width="' + str(pct) + '%" style="height:6px;background:' + col + ';'
            'border-radius:3px 0 0 3px;" bgcolor="' + col + '">&nbsp;</td>'
            '<td style="height:6px;background:#E0D5C1;border-radius:0 3px 3px 0;" bgcolor="#E0D5C1">&nbsp;</td>'
            '</tr></table></td>'
            '<td width="90" style="padding:7px 0 7px 14px;vertical-align:middle;text-align:right;">'
            '<p style="margin:0;font-family:Georgia,serif;font-size:11px;'
            'font-style:italic;color:#8B6F5C;">' + state + '</p>'
            '<p style="margin:2px 0 0;font-family:Raleway,Arial,sans-serif;font-size:9px;'
            'color:#A08470;">' + desc + '</p>'
            '</td></tr>'
        )
    return '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">' + rows + '</table>'


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
    """
    Parse Claude output (v2 structured format) into
    (body_html, tips_html, conclusion_html).
    """
    GRN  = "#3D5A4C"
    BR   = "#2C1A0E"
    CR   = "#F0E6D3"

    body_html       = ""
    tips_html       = ""
    conclusion_html = ""

    # Split into sections on ## and ### headings
    parts = re.split(r'\n(#{1,3} [^\n]+)\n', "\n" + text.strip())
    # parts = [pre, heading, content, heading, content, ...]

    current_heading = None
    tip_section_found = False

    for part in parts:
        part_stripped = part.strip()
        if not part_stripped:
            continue

        # Is this a heading?
        heading_match = re.match(r'^(#{1,3}) (.+)$', part_stripped)
        if heading_match:
            current_heading = heading_match.group(2).strip()
            continue

        if current_heading is None:
            continue

        heading_lower = current_heading.lower()

        if "tip" in heading_lower or "wellness" in heading_lower:
            tip_section_found = True
            # Split into tip lines and conclusion
            lines = part_stripped.split("\n")
            tip_lines = []
            remainder_lines = []
            in_tips = True
            for line in lines:
                if re.match(r'^\[(\w+)\]', line.strip()):
                    tip_lines.append(line.strip())
                elif tip_lines and line.strip():
                    # Non-empty line after tips = conclusion
                    remainder_lines.append(line.strip())
            # Build tip cards
            if tip_lines:
                tips_html = _build_tips_html(tip_lines)
            # Conclusion from remainder
            if remainder_lines:
                conclusion_text = " ".join(remainder_lines)
                conclusion_html = _render_conclusion_html(conclusion_text, GRN, BR, CR)

        elif part_stripped and current_heading:
            # Regular reading section — render with heading
            body_html += _render_section_html(current_heading, part_stripped, GRN, BR)

    # If no conclusion found in tips section, check for trailing paragraph
    if not conclusion_html:
        # Look for last double-newline separated paragraph after any tips
        chunks = re.split(r'\n\n+', text.strip())
        last = chunks[-1].strip()
        if last and not re.match(r'^\[', last) and not re.match(r'^#', last):
            conclusion_html = _render_conclusion_html(last, GRN, BR, CR)

    return body_html, tips_html, conclusion_html


def _render_section_html(heading: str, content: str, GRN: str, BR: str) -> str:
    heading_html = (
        '<tr><td style="padding:28px 0 12px;">'
        '<p style="margin:0;font-family:Cormorant Garamond,Georgia,serif;font-size:10px;'
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
            '<p style="margin:0;font-family:Georgia,serif;font-size:15px;'
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
            '<p style="margin:0 0 4px;font-family:Cormorant Garamond,Georgia,serif;font-size:8px;'
            'font-weight:700;letter-spacing:0.2em;text-transform:uppercase;color:' + col + ';">'
            + lbl + '</p>'
            '<p style="margin:0;font-family:Georgia,serif;font-size:14px;'
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
        '<p style="margin:0;font-family:Georgia,serif;font-size:15px;'
        'font-style:italic;line-height:1.9;color:' + BR + ';">' + text + '</p>'
        '</div></td></tr>'
    )


def _build_email(name: str, pillars: dict, constitution: dict, reading_text: str) -> str:
    pillar_tbl             = _pillar_cards_html(pillars)
    elem_bars              = _element_bars_html(constitution)
    body_html, tips_html, conclusion_html = _parse_reading_v2(reading_text)

    CR  = "#FAF3E4"
    CRD = "#F0E6D3"
    BR  = "#2C1A0E"
    GRN = "#3D5A4C"
    BRL = "#8B6F5C"
    BDR = "#E0D5C1"

    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Your Ba Zi Reading, """ + name + """</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;1,300&family=Raleway:wght@300;400;600&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background-color:""" + CRD + """;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:""" + CRD + """;">
<tr><td align="center" style="padding:40px 16px;">
<table role="presentation" width="640" cellpadding="0" cellspacing="0"
       style="background-color:""" + CR + """;max-width:640px;width:100%;">

  <!-- HEADER -->
  <tr><td style="padding:50px 48px 38px;text-align:center;border-bottom:1px solid """ + BDR + """;">
    <p style="margin:0 0 12px;font-family:Cormorant Garamond,Georgia,serif;font-size:9px;
              letter-spacing:0.35em;text-transform:uppercase;color:""" + GRN + """;">
      Ba Zi · Elemental Constitution · 2026
    </p>
    <h1 style="margin:0 0 10px;font-family:Cormorant Garamond,Georgia,serif;
               font-size:38px;font-weight:300;color:""" + BR + """;line-height:1.2;">""" + name + """</h1>
    <p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:10px;
              letter-spacing:0.12em;color:""" + BRL + """;">
      Your personalised reading
    </p>
  </td></tr>

  <!-- WHAT ARE THE FOUR PILLARS? -->
  <tr><td style="padding:32px 48px 24px;background:#F7F0E4;border-bottom:1px solid """ + BDR + """;">
    <p style="margin:0 0 8px;font-family:Cormorant Garamond,Georgia,serif;font-size:9px;
              font-weight:700;letter-spacing:0.25em;text-transform:uppercase;color:""" + BRL + """;">
      What are the Four Pillars?
    </p>
    <p style="margin:0;font-family:Georgia,serif;font-size:13px;line-height:1.75;color:#6B4C36;">
      In Chinese cosmology, your birth date isn&#39;t just a number — it&#39;s a map. Each pillar
      (year, month, day, and hour) translates into a pair of elemental energies, revealing the
      forces that shape your character, purpose, and path. The <strong>Day Master</strong> —
      the heavenly stem of your Day pillar — is considered your core elemental self.
    </p>
  </td></tr>

  <!-- FOUR PILLARS -->
  <tr><td style="padding:32px 48px 0;">
    <p style="margin:0 0 18px;font-family:Cormorant Garamond,Georgia,serif;font-size:9px;
              font-weight:700;letter-spacing:0.25em;text-transform:uppercase;color:""" + BRL + """;">
      Your Four Pillars
    </p>
    """ + pillar_tbl + """
  </td></tr>

  <!-- DIVIDER -->
  <tr><td style="padding:32px 48px 0;"><div style="border-top:1px solid """ + BDR + """;"></div></td></tr>

  <!-- WHAT ARE THE FIVE ELEMENTS? -->
  <tr><td style="padding:28px 48px 20px;background:#F7F0E4;border-top:0;border-bottom:1px solid """ + BDR + """;">
    <p style="margin:0 0 8px;font-family:Cormorant Garamond,Georgia,serif;font-size:9px;
              font-weight:700;letter-spacing:0.25em;text-transform:uppercase;color:""" + BRL + """;">
      What are the Five Elements?
    </p>
    <p style="margin:0;font-family:Georgia,serif;font-size:13px;line-height:1.75;color:#6B4C36;">
      Wood, Fire, Earth, Metal, and Water are not just materials — they&#39;re qualities of energy
      present in everything, including you. Your chart shows how much of each element you carry.
      Too much or too little of any one element creates patterns you&#39;ll recognise in your body,
      emotions, and habits.
    </p>
  </td></tr>

  <!-- FIVE ELEMENT BARS -->
  <tr><td style="padding:28px 48px 0;">
    <p style="margin:0 0 18px;font-family:Cormorant Garamond,Georgia,serif;font-size:9px;
              font-weight:700;letter-spacing:0.25em;text-transform:uppercase;color:""" + BRL + """;">
      Your Elemental Make-up
    </p>
    """ + elem_bars + """
  </td></tr>

  <!-- DIVIDER -->
  <tr><td style="padding:32px 48px 0;"><div style="border-top:1px solid """ + BDR + """;"></div></td></tr>

  <!-- READING BODY -->
  <tr><td style="padding:8px 48px 0;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      """ + body_html + """
      """ + tips_html + """
      """ + conclusion_html + """
    </table>
  </td></tr>

  <!-- FOOTER -->
  <tr><td style="padding:40px 48px;border-top:1px solid """ + BDR + """;text-align:center;margin-top:32px;">
    <p style="margin:0 0 6px;font-family:Cormorant Garamond,Georgia,serif;font-size:9px;
              letter-spacing:0.2em;text-transform:uppercase;color:""" + BRL + """;">
      Ed Nicholls Acupuncture &nbsp;&middot;&nbsp; ednicholls.com
    </p>
    <p style="margin:0;font-family:Raleway,Arial,sans-serif;font-size:9px;color:""" + BRL + """;">
      This reading is offered as a complementary wellness guide, not a substitute for medical advice.
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


# ── Google Sheets logger ───────────────────────────────────

def _log_to_sheets(name: str, email: str) -> None:
    """
    Append a subscriber row to Google Sheets via Apps Script web app.
    Set GOOGLE_SHEET_URL in Railway env vars to enable.
    """
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

    # 4. Build Claude prompt
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

    # 5. Call Claude
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

    # 6. Build email
    html = _build_email(data.name, pillars, constitution, reading_text)

    # 7. Send via Resend
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
                    "subject": "Your Ba Zi Reading, " + data.name,
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

    # 8. Log to Google Sheets (non-fatal if it fails)
    _log_to_sheets(data.name, data.email)

    return ReadingResponse(
        success=True,
        message="Your reading has been sent to " + data.email,
    )
