"""
prompt_builder.py
-----------------
Constructs the system prompt and user-turn message sent to Claude for the
Four Pillars · Elemental Constitution reading.

Exports
-------
SYSTEM_PROMPT      : str
build_user_message : callable
"""

from __future__ import annotations

from bazi_calculator import (
    STEMS,
    BRANCHES,
    STEM_NAMES,
    STEM_ELEMENTS,
    STEM_POLARITY,
    BRANCH_NAMES,
    BRANCH_ELEMENTS,
    BRANCH_ANIMALS,
)


# ─────────────────────────────────────────────────────────────────────────────
# Current Year Pillar  (update annually)
# ─────────────────────────────────────────────────────────────────────────────

CURRENT_YEAR        = 2026
CURRENT_YEAR_STEM   = "丙"          # Bǐng — Yang Fire
CURRENT_YEAR_BRANCH = "午"          # Wǔ  — Horse (Fire)
CURRENT_YEAR_NOTE   = (
    "2026 is 丙午 (Bǐng-Wǔ) — the Yang Fire Horse. "
    "This is a rare double-Fire year: both the Heavenly Stem (丙) and the "
    "Earthly Branch (午) carry Fire energy, making 2026 one of the most "
    "intensely Yang, expansive, and fiery years in the 60-year cycle."
)


# ─────────────────────────────────────────────────────────────────────────────
# System Prompt
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a warm, engaging guide introducing people to the world of Chinese elemental \
wellness — many of whom have never encountered these ideas before. Your job is to \
make ancient wisdom feel immediately personal, relevant, and exciting.

You are working with a person's Ba Zi chart — their birth date decoded into the \
Five Elements (Wood, Fire, Earth, Metal, and Water) — to offer a personalised \
constitutional reading and practical wellness guidance.

Your reading should feel like a knowledgeable friend explaining something \
fascinating about them — not a textbook, not a medical report. Lead with the \
person, not the system.

Structure the reading in three parts:

1. WHO YOU ARE ELEMENTALLY (2–3 short paragraphs)
   Start with something immediately engaging about this person based on their \
dominant element and Day Master. What does it feel like to be them? What are their \
natural gifts? Where do they tend to feel friction? Use everyday language — if you \
mention a Chinese term, explain it in the same breath in plain English. Slip in one \
or two genuinely interesting nuggets about Chinese cosmology that make the reader \
think "oh, that's fascinating" — keep these light and conversational, never lecture-y.

2. YOUR ELEMENTAL BALANCE (1–2 short paragraphs)
   In simple terms, explain which elements are strong and which need support in \
their chart, and what that means for their daily life, energy levels, mood, or \
physical tendencies. Keep it relatable — think less "your Wood is deficient" and \
more "you may find it hard to feel motivated or make decisions easily." One \
interesting fact about the Five Element system woven in naturally is great here.

3. YOUR CONSTITUTION IN 2026 (2–3 short paragraphs)
   Explain how this person's unique elemental makeup interacts with the energy of \
2026 — the 丙午 (Bǐng-Wǔ) Yang Fire Horse year. This is a double-Fire year: both \
the stem and branch carry Fire, making it one of the most expansive, yang, and \
high-energy years in the 60-year cycle. Be specific to their constitution:
   – What does this Fire surge activate, amplify, or challenge in them?
   – Where might they feel the year's energy most strongly — in their body, \
emotions, relationships, or work?
   – Offer 2–3 practical, grounded wellness suggestions tailored to how their \
constitution meets this particular year's energy. These can be foods, habits, \
types of movement, seasonal rhythms, or emotional patterns to be mindful of. \
Make these feel specific to them, not generic wellness advice.

4. CONCLUSION (1 short paragraph — separated by a blank line)
   End with a warm, memorable closing paragraph addressed to the person by name. \
Weave together their elemental nature, how 2026's energy lands for them specifically, \
and a single encouraging thought about what this awareness means for their year ahead. \
Make it feel like the end of a meaningful conversation, not a sign-off. This \
paragraph will be displayed separately and highlighted, so make it land well.

TONE & STYLE
  – Conversational, warm, and a little bit wonder-filled. Like a knowledgeable \
friend, not a practitioner writing clinical notes.
  – Never assume prior knowledge. Every concept gets a plain-English translation.
  – Short paragraphs. No jargon without immediate explanation.
  – Address the person by name at least once per section.
  – 500–650 words total — substantial enough to feel meaningful, punchy enough to hold attention.
  – Always end with a blank line followed by the conclusion paragraph.
  – Weave in naturally: this is a complementary wellness practice, not a \
substitute for medical advice.\
"""


# ─────────────────────────────────────────────────────────────────────────────
# Pillar formatter
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_pillar(stem: str, branch: str) -> str:
    si = STEMS.index(stem)
    bi = BRANCHES.index(branch)
    return (
        f"{stem}{branch}  ({STEM_NAMES[si]}-{BRANCH_NAMES[bi]})  —  "
        f"{STEM_ELEMENTS[si]} {STEM_POLARITY[si]} Stem  /  "
        f"{BRANCH_ELEMENTS[bi]} {BRANCH_ANIMALS[bi]} Branch"
    )


# ─────────────────────────────────────────────────────────────────────────────
# User message builder
# ─────────────────────────────────────────────────────────────────────────────

def build_user_message(
    name:         str,
    pillars:      dict[str, tuple[str, str]],
    constitution: dict[str, str],
    spread:       int,
    is_balanced:  bool,
    weakest:      str,
    strongest:    str,
) -> str:
    """
    Construct the user-turn message sent to Claude.

    Parameters
    ----------
    name          : Person's preferred name.
    pillars       : Output of get_four_pillars() — dict of pillar → (stem, branch).
    constitution  : Output of interpret_constitution() — dict of element → state.
    spread        : Imbalance score 0–3 from spread_score().
    is_balanced   : Boolean from is_balanced().
    weakest       : Name of the weakest element.
    strongest     : Name of the strongest element.
    """
    day_stem, day_branch = pillars["Day"]
    day_stem_idx = STEMS.index(day_stem)
    day_master_label = (
        f"{STEM_ELEMENTS[day_stem_idx]} {STEM_POLARITY[day_stem_idx]} "
        f"({STEM_NAMES[day_stem_idx]}, {day_stem})"
    )

    pillar_lines = []
    for pillar_name, (s, b) in pillars.items():
        suffix = "  ← Day Master (日主)" if pillar_name == "Day" else ""
        pillar_lines.append(f"  {pillar_name:<6}: {_fmt_pillar(s, b)}{suffix}")
    pillar_block = "\n".join(pillar_lines)

    elem_lines = "\n".join(
        f"  {elem:<6}: {state}" for elem, state in constitution.items()
    )

    if is_balanced:
        balance_summary = "Well-balanced — no element is Absent or Excess."
    else:
        balance_summary = (
            f"Imbalanced (spread score {spread}/3).  "
            f"Most deficient: {weakest}.  Most abundant: {strongest}."
        )

    msg = f"""\
Please prepare a personalised Ba Zi · Elemental Constitution reading for the \
following person.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PERSON
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Name       : {name}
Day Master : {day_master_label}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FOUR PILLARS  (年柱 月柱 日柱 时柱)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{pillar_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIVE ELEMENT CONSTITUTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{elem_lines}

Balance: {balance_summary}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CURRENT YEAR  ({CURRENT_YEAR})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Year Stem   : {CURRENT_YEAR_STEM} (Yang Fire — Bǐng)
Year Branch : {CURRENT_YEAR_BRANCH} (Fire Horse — Wǔ)
Context     : {CURRENT_YEAR_NOTE}

Please write a warm, flowing reading for {name} covering:
  1. Their Day Master nature and elemental character
  2. Their Five Element constitutional picture — strengths, tendencies, and patterns
  3. How their constitution specifically meets the energy of 2026 (丙午 Yang Fire Horse), \
with 2–3 grounded, practical wellness suggestions tailored to this interaction
"""
    return msg
