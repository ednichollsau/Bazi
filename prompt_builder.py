"""
prompt_builder.py
-----------------
Constructs the system prompt and user-turn message sent to Claude for the
Four Pillars · Ear Seed Protocol reading.

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
# System Prompt
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a compassionate and deeply knowledgeable practitioner weaving together \
two ancient systems of pattern recognition:

  • Ba Zi (四柱八字, Four Pillars of Destiny) — a Chinese cosmological map that \
encodes a person's elemental constitution, inherent strengths, tendencies, and \
the dynamic interplay of Heaven, Earth, and humanity at the moment of birth.

  • Auriculotherapy (ear seed therapy) — a branch of Traditional Chinese Medicine \
in which the outer ear is understood as a microsystem of the whole body. Gentle \
seeds or pellets placed on specific auricular points stimulate organ systems, \
regulate Qi flow, and support Five Element balance.

Your task is to write a personalised wellness reading for the person whose chart \
data is provided. The reading must flow through four movements:

1. CONSTITUTION OVERVIEW
   Introduce the person by name and describe their Four Pillars — the year, month, \
day (Day Master), and hour of birth — in accessible, evocative language. The Day \
Master (日主, the Day Stem) is the focal lens: it represents the person themselves. \
Briefly explain what their Day Master element and polarity reveals about their \
fundamental nature, energy, and way of engaging with the world.

2. ELEMENTAL DYNAMICS
   Describe their Five Element constitution: which elements are in abundance, which \
are deficient or absent, and what this balance (or imbalance) may express in their \
personality, emotional tendencies, physical vitality, and areas of natural strength \
or potential vulnerability. Be poetic but grounded. Do not diagnose — speak in \
terms of tendencies and patterns.

3. EAR SEED PROTOCOL EXPLAINED
   Walk through the ear seed protocol prepared for them. For each auricular point \
or group of points, explain in plain language:
     – which organ system or element it connects to,
     – why it was chosen given their specific constitution, and
     – what quality of balance or support it is intended to encourage.
   Where helpful, list left-ear and right-ear points in separate short bullet lists \
for clarity, but keep the surrounding explanation in flowing prose.

4. CLOSING GUIDANCE
   Offer 2–3 brief, empowering wellness suggestions aligned with their constitution: \
for example, foods, movement styles, seasonal awareness, or emotional practices that \
naturally support or balance their dominant and deficient elements.

TONE & STYLE
  – Warm, intelligent, and empowering. Never fatalistic or alarming.
  – Educational but accessible: introduce Ba Zi concepts briefly when needed.
  – This is a complementary wellness perspective, not medical diagnosis or treatment.
  – Address the person by name throughout.
  – 450–650 words. Flowing prose — avoid excessive bullet points except when \
listing the ear seed points themselves.
  – Weave in naturally (do not isolate as a disclaimer block): a gentle reminder \
that this is a wellness companion practice and does not replace professional \
medical or psychological care.\
"""


# ─────────────────────────────────────────────────────────────────────────────
# Pillar formatter
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_pillar(stem: str, branch: str) -> str:
    """
    Return a richly annotated pillar description, e.g.
    '庚午  (Gēng-Wǔ)  —  Metal Yang Stem  /  Fire Horse Branch'
    """
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
    left_points:  list[str],
    right_points: list[str],
    rationale:    str,
    spread:       int,
    is_balanced:  bool,
    weakest:      str,
    strongest:    str,
) -> str:
    """
    Construct the user-turn message sent to Claude.

    All data has been pre-calculated by the Ba Zi engine; this function
    formats it into a clear, structured prompt.

    Parameters
    ----------
    name          : Person's preferred name.
    pillars       : Output of get_four_pillars() — dict of pillar → (stem, branch).
    constitution  : Output of interpret_constitution() — dict of element → state.
    left_points   : Left ear auricular points (list of str).
    right_points  : Right ear auricular points (list of str).
    rationale     : Plain-text rationale from build_protocol().
    spread        : Imbalance score 0–3 from spread_score().
    is_balanced   : Boolean from is_balanced().
    weakest       : Name of the weakest element.
    strongest     : Name of the strongest element.

    Returns
    -------
    str — the formatted user message.
    """
    # ── Four Pillars block ────────────────────────────────────────────────────
    # Identify Day Master for special annotation
    day_stem, day_branch = pillars["Day"]
    day_stem_idx = STEMS.index(day_stem)
    day_master_label = (
        f"{STEM_ELEMENTS[day_stem_idx]} {STEM_POLARITY[day_stem_idx]} "
        f"({STEM_NAMES[day_stem_idx]}, {day_stem})"
    )

    pillar_lines = []
    for pillar_name, (s, b) in pillars.items():
        suffix = "  ← Day Master (日主)" if pillar_name == "Day" else ""
        pillar_lines.append(
            f"  {pillar_name:<6}: {_fmt_pillar(s, b)}{suffix}"
        )
    pillar_block = "\n".join(pillar_lines)

    # ── Five Element constitution block ───────────────────────────────────────
    elem_lines = "\n".join(
        f"  {elem:<6}: {state}"
        for elem, state in constitution.items()
    )

    # ── Balance summary ───────────────────────────────────────────────────────
    if is_balanced:
        balance_summary = "Well-balanced — no element is Absent or Excess."
    else:
        balance_summary = (
            f"Imbalanced (spread score {spread}/3).  "
            f"Most deficient: {weakest}.  Most abundant: {strongest}."
        )

    # ── Ear seed protocol block ───────────────────────────────────────────────
    left_str  = ", ".join(left_points)
    right_str = ", ".join(right_points)

    # ── Compose full message ──────────────────────────────────────────────────
    msg = f"""\
Please prepare a personalised Ba Zi · Ear Seed reading for the following person.

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
EAR SEED PROTOCOL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Left ear  (Yin / tonification / constitutional support):
  {left_str}

Right ear (Yang / regulation / harmonisation):
  {right_str}

{rationale}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Please write a warm, flowing reading for {name} that covers:
  1. Their Four Pillars and Day Master nature
  2. Their Five Element elemental dynamics — strengths, tendencies, and patterns
  3. A clear, personalised explanation of each ear seed point and why it was chosen
  4. 2–3 brief supportive wellness suggestions aligned with their constitution
"""
    return msg
