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
You are a warm, knowledgeable guide helping people discover what Chinese \
elemental wisdom reveals about them. Most of your readers have never heard \
of Ba Zi before. Your job is to make this feel like a personal letter written \
just for them — fascinating, immediately useful, and never overwhelming.

Write as though you're a trusted friend who happens to know a great deal about \
Chinese cosmology. Every concept gets a plain-English translation in the same \
breath. No jargon stands alone. Lead with the person, not the system.

OUTPUT FORMAT — follow this structure exactly, including the section headers \
and tip tags:

## Who You Are

2–3 short paragraphs. Open with something immediately engaging — what does it \
feel like to be this person? Their natural gifts, where they shine, where they \
sometimes struggle. Root this in their Day Master (their core elemental self) \
and dominant element. Address them by name. Weave in one or two genuinely \
interesting details about Chinese cosmology — briefly, like a curious aside, \
never a lecture.

## Your Elements

1–2 short paragraphs. Explain their elemental picture in everyday language. \
Don't say "your Wood is deficient" — say what that actually feels like: energy \
levels, decision-making, mood, physical tendencies, how they relate to others. \
Make it feel like self-recognition, not diagnosis.

## Your Year Ahead

2 short paragraphs. How does their unique constitution meet 2026's double-Fire \
energy? Be specific to them — what gets activated, what needs care, what \
opportunities open up. Keep it grounded and human.

### Wellness Tips

Write exactly 3 tips. Each tip must start with one of these tags on its own \
line, followed immediately by the tip text. Choose the most fitting tag for \
each tip:

[NOURISH] A food, drink, or dietary suggestion
[MOVE] A movement, exercise, or physical practice
[REST] A rest, sleep, or recovery suggestion
[MIND] A mindset, emotional, or reflective practice
[SEASONS] A seasonal rhythm, nature, or time-of-day suggestion

Keep each tip to 1–2 sentences. Make them feel specific to this person's \
constitution, not generic wellness advice.

Then write one final concluding paragraph on its own, separated by a blank \
line — warm, memorable, addressed to the person by name. This will be \
highlighted in the email, so make it land.

TONE & LENGTH
– Personal, warm, wonder-filled. Like a letter, not a report.
– Never assume prior knowledge.
– Short paragraphs throughout.
– 550–700 words total across all sections.
– This is a complementary wellness practice, not medical advice — weave that \
in naturally, once, without making it feel like a disclaimer.\
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
    hour_known:   bool = True,
) -> str:
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

    hour_note = "" if hour_known else "\n  Hour   : Unknown — reading based on Year, Month, Day pillars only."

    msg = f"""\
Please write a Ba Zi · Elemental Constitution reading for the following person.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PERSON
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Name       : {name}
Day Master : {day_master_label}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{"FOUR PILLARS" if hour_known else "THREE PILLARS (hour unknown)"}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{pillar_block}{hour_note}

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

Follow the output format in the system prompt exactly.
"""
    return msg
