"""
api_server.py
-------------
FastAPI server that:
  1. Accepts a POST /reading request with birth data
  2. Runs the Ba Zi engine (bazi_calculator.py)
  3. Builds the prompt (prompt_builder.py)
  4. Calls the Claude API
  5. Returns the reading as JSON

Run locally:
  pip install fastapi uvicorn anthropic
  uvicorn api_server:app --reload

Then POST to: http://localhost:8000/reading
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from typing import Optional
from datetime import date
import anthropic
import os

# ── Import your engine modules ─────────────────────────────
from bazi_calculator import (
    get_four_pillars,
    get_element_counts,
    interpret_constitution,
    build_protocol,
    spread_score,
    is_balanced,
    STATE_RANK,
)
from prompt_builder import SYSTEM_PROMPT, build_user_message

# ── App Setup ──────────────────────────────────────────────

app = FastAPI(
    title="Four Pillars · Ear Seed Protocol API",
    description="Calculates Ba Zi constitution and returns an auriculotherapy reading via Claude.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Restrict to your frontend domain in production
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

# ── Request / Response Models ──────────────────────────────

class BirthDataRequest(BaseModel):
    name:        Optional[str] = Field(default="Friend", description="Preferred name")
    year:        int           = Field(..., ge=1900, le=2100)
    month:       int           = Field(..., ge=1,    le=12)
    day:         int           = Field(..., ge=1,    le=31)
    hour:        int           = Field(..., ge=0,    le=23,
                                       description="Birth hour in 24h format (0–23)")

    @validator("year")
    def not_future(cls, v, values):
        current_year = date.today().year
        if v > current_year:
            raise ValueError("Birth year cannot be in the future.")
        return v


class PillarData(BaseModel):
    stem:   str
    branch: str


class ConstitutionData(BaseModel):
    Wood:  str
    Fire:  str
    Earth: str
    Metal: str
    Water: str


class ProtocolData(BaseModel):
    left_ear:  list[str]
    right_ear: list[str]


class ReadingResponse(BaseModel):
    name:         str
    pillars:      dict[str, dict]
    constitution: ConstitutionData
    protocol:     ProtocolData
    reading:      str
    is_balanced:  bool
    spread_score: int


# ── Health Check ───────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "Four Pillars · Ear Seed Protocol API"}


# ── Core Endpoint ──────────────────────────────────────────

@app.post("/reading", response_model=ReadingResponse)
def get_reading(data: BirthDataRequest):

    # 1. Calculate Four Pillars
    try:
        pillars = get_four_pillars(data.year, data.month, data.day, data.hour)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Pillar calculation error: {e}")

    # 2. Five Element analysis
    counts       = get_element_counts(pillars)
    constitution = interpret_constitution(counts)

    # 3. Build ear seed protocol
    left, right, rationale = build_protocol(constitution)

    # 4. Diagnostic summary values
    spread      = spread_score(constitution)
    balanced    = is_balanced(constitution)
    sorted_elems = sorted(constitution.items(), key=lambda x: STATE_RANK[x[1]])
    weakest     = sorted_elems[0][0]
    strongest   = sorted_elems[-1][0]

    # 5. Build Claude prompt
    user_message = build_user_message(
        name        = data.name,
        pillars     = pillars,
        constitution= constitution,
        left_points = left,
        right_points= right,
        rationale   = rationale,
        spread      = spread,
        is_balanced = balanced,
        weakest     = weakest,
        strongest   = strongest,
    )

    # 6. Call Claude API
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY not set in environment."
        )

    client = anthropic.Anthropic(api_key=api_key)

    try:
        message = client.messages.create(
            model      = "claude-opus-4-6",   # Richest interpretive output
            max_tokens = 1024,
            system     = SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": user_message}]
        )
        reading_text = message.content[0].text
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Claude API error: {e}")

    # 7. Return structured response
    return ReadingResponse(
        name    = data.name,
        pillars = {k: {"stem": v[0], "branch": v[1]} for k, v in pillars.items()},
        constitution = ConstitutionData(**constitution),
        protocol     = ProtocolData(left_ear=left, right_ear=right),
        reading      = reading_text,
        is_balanced  = balanced,
        spread_score = spread,
    )
