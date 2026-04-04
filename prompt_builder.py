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
You are a warm, engaging guide introducing people to the world of Chinese elemental \
wellness — many of whom have never encountered these ideas before. Your job is to \
make ancient wisdom feel immediately personal, relevant, and exciting.

You are working with two complementary tools:
  • A person's Ba Zi chart (their birth date decoded into Five Elements — Wood, \
Fire, Earth, Metal, and Water)
  • An ear seed protocol — tiny seeds placed on specific points of the ear that \
correspond to different organ systems and elements in the body

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

3. YOUR EAR SEEDS AND WHY (1–2 short paragraphs + the point lists)
   Explain what ear seeds are in one sentence for someone who has never heard of \
them. Then explain the protocol in plain, friendly terms — what each point or group \
of points is doing for them specifically, tied back to their elemental picture. \
List the left and right ear points clearly. End with one simple, practical \
wellness suggestion that fits their constitution — a food, a habit, a type of \
movement, or something to be mindful of emotionally.

4. CONCLUSION (1 short paragraph — separated by a blank line)
   End with a warm, memorable closing paragraph addressed to the person by name. \
Bring together their elemental nature, what the ear seeds are supporting, and a \
single encouraging thought about what this practice means for their wellbeing. \
Make it feel like the end of a meaningful conversation, not a sign-off. This \
paragraph will be displayed separately and highlighted, so make it land well.

TONE & STYLE
  – Conversational, warm, and a little bit wonder-filled. Like a knowledgeable \
friend, not a practitioner writing clinical notes.
  – Never assume prior knowledge. Every concept gets a plain-English translation.
  – Short paragraphs. No jargon without immediate explanation.
  – Address the person by name.
  – 400–550 words total — punchy, not exhaustive.
  – Always end with a blank line followed by the conclusion paragraph.
  – Weave in naturally: this is a complementary wellness practice, not a \
substitute for medical advice.\
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
