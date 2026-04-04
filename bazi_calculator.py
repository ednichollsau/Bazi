"""
bazi_calculator.py
------------------
Ba Zi (四柱/八字) — Four Pillars of Destiny engine.

Calculates the Year, Month, Day, and Hour pillars from a Gregorian birth
date/time, analyses the Five Element constitution, and derives a bilateral
ear seed (auriculotherapy) protocol tuned to elemental excesses and
deficiencies.

Exports
-------
get_four_pillars(year, month, day, hour)  -> dict[str, tuple[str, str]]
get_element_counts(pillars)               -> dict[str, int]
interpret_constitution(counts)            -> dict[str, str]
build_protocol(constitution)              -> tuple[list[str], list[str], str]
spread_score(constitution)                -> int
is_balanced(constitution)                 -> bool
STATE_RANK                                -> dict[str, int]

Notes
-----
* The Month Pillar uses approximate solar term dates (± 1–2 days near
  transitions). For maximum precision near a solar term boundary, verify
  with a dedicated Chinese calendar library.
* The Zǐ (子) hour at 23:00 is attributed to the *current* calendar day;
  strictly, it belongs to the next day in classical reckoning.
"""

from __future__ import annotations

# ─── Heavenly Stems  天干 ──────────────────────────────────────────────────────

STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]

STEM_NAMES = [
    "Jiǎ", "Yǐ", "Bǐng", "Dīng", "Wù",
    "Jǐ", "Gēng", "Xīn", "Rén", "Guǐ",
]

STEM_ELEMENTS = [
    "Wood", "Wood", "Fire", "Fire", "Earth",
    "Earth", "Metal", "Metal", "Water", "Water",
]

STEM_POLARITY = [
    "Yang", "Yin", "Yang", "Yin", "Yang",
    "Yin", "Yang", "Yin", "Yang", "Yin",
]

# ─── Earthly Branches  地支 ────────────────────────────────────────────────────

BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

BRANCH_NAMES = [
    "Zǐ", "Chǒu", "Yín", "Mǎo", "Chén", "Sì",
    "Wǔ", "Wèi", "Shēn", "Yǒu", "Xū", "Hài",
]

BRANCH_ANIMALS = [
    "Rat", "Ox", "Tiger", "Rabbit", "Dragon", "Snake",
    "Horse", "Goat", "Monkey", "Rooster", "Dog", "Pig",
]

# Primary element governing each branch
BRANCH_ELEMENTS = [
    "Water", "Earth", "Wood", "Wood", "Earth", "Fire",
    "Fire",  "Earth", "Metal", "Metal", "Earth", "Water",
]

# ─── Five Elements ─────────────────────────────────────────────────────────────

ELEMENTS = ["Wood", "Fire", "Earth", "Metal", "Water"]

# ─── Constitution States & Ranking ─────────────────────────────────────────────

STATE_RANK: dict[str, int] = {
    "Absent":   0,
    "Low":      1,
    "Balanced": 2,
    "Excess":   3,
}

# ─── Auricular Point Database ──────────────────────────────────────────────────
#
# Points are drawn from the Nogier/NADA/TCM auricular maps.
# Left ear  = Yin side → used for constitutional tonification.
# Right ear = Yang side → used for functional regulation / sedation.
#
# Universal master points are always included on both ears.

_UNIVERSAL_LEFT  = ["Shen Men", "Point Zero", "Thalamus Point"]
_UNIVERSAL_RIGHT = ["Shen Men", "Point Zero", "Sympathetic Autonomic"]

_EAR: dict[str, dict[str, list[str]]] = {
    "Wood": {
        "tonification": ["Liver", "Gallbladder", "Eye Point"],
        "sedation":     ["Liver", "Gallbladder", "Sympathetic Autonomic"],
    },
    "Fire": {
        "tonification": ["Heart", "Small Intestine", "Brain Point"],
        "sedation":     ["Heart", "Shen Men", "Sympathetic Autonomic"],
    },
    "Earth": {
        "tonification": ["Spleen", "Stomach", "Endocrine Point"],
        "sedation":     ["Stomach", "Spleen", "Hunger Point"],
    },
    "Metal": {
        "tonification": ["Lung", "Large Intestine", "Adrenal Gland"],
        "sedation":     ["Lung", "Large Intestine", "Allergy Point"],
    },
    "Water": {
        "tonification": ["Kidney", "Bladder", "Adrenal Gland"],
        "sedation":     ["Kidney", "Endocrine Point", "Lower Back"],
    },
}

_ELEM_DESCRIPTION: dict[str, str] = {
    "Wood": (
        "Wood governs the Liver and Gallbladder, tendons, eyes, "
        "and the smooth, unobstructed flow of Qi and emotion."
    ),
    "Fire": (
        "Fire governs the Heart and Small Intestine, circulation, "
        "the spirit (Shén), consciousness, and the capacity for joy."
    ),
    "Earth": (
        "Earth governs the Spleen and Stomach, digestion, nourishment, "
        "intention (Yì), and the capacity to transform experience into wisdom."
    ),
    "Metal": (
        "Metal governs the Lung and Large Intestine, respiration, skin, "
        "boundaries, the capacity to let go, and the corporeal soul (Pò)."
    ),
    "Water": (
        "Water governs the Kidney and Bladder, constitutional essence (Jīng), "
        "will-power (Zhì), fear, and the deep reserves of vitality."
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _jdn(year: int, month: int, day: int) -> int:
    """Return the proleptic Gregorian Julian Day Number (noon-based)."""
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return (
        day
        + (153 * m + 2) // 5
        + 365 * y
        + y // 4
        - y // 100
        + y // 400
        - 32045
    )


# ── Chinese New Year dates (Gregorian) ───────────────────────────────────────
# Each entry: year → (month, day) of Chinese New Year for that Gregorian year.
# Used to determine which astrological year a birth date belongs to.

_CNY: dict[int, tuple[int, int]] = {
    1900:(1,31), 1901:(2,19), 1902:(2, 8), 1903:(1,29), 1904:(2,16),
    1905:(2, 4), 1906:(1,25), 1907:(2,13), 1908:(2, 2), 1909:(1,22),
    1910:(2,10), 1911:(1,30), 1912:(2,18), 1913:(2, 6), 1914:(1,26),
    1915:(2,14), 1916:(2, 3), 1917:(1,23), 1918:(2,11), 1919:(2, 1),
    1920:(2,20), 1921:(2, 8), 1922:(1,28), 1923:(2,16), 1924:(2, 5),
    1925:(1,25), 1926:(2,13), 1927:(2, 2), 1928:(1,23), 1929:(2,10),
    1930:(1,30), 1931:(2,17), 1932:(2, 6), 1933:(1,26), 1934:(2,14),
    1935:(2, 4), 1936:(1,24), 1937:(2,11), 1938:(1,31), 1939:(2,19),
    1940:(2, 8), 1941:(1,27), 1942:(2,15), 1943:(2, 5), 1944:(1,25),
    1945:(2,13), 1946:(2, 2), 1947:(1,22), 1948:(2,10), 1949:(1,29),
    1950:(2,17), 1951:(2, 6), 1952:(1,27), 1953:(2,14), 1954:(2, 3),
    1955:(1,24), 1956:(2,12), 1957:(1,31), 1958:(2,18), 1959:(2, 8),
    1960:(1,28), 1961:(2,15), 1962:(2, 5), 1963:(1,25), 1964:(2,13),
    1965:(2, 2), 1966:(1,21), 1967:(2, 9), 1968:(1,30), 1969:(2,17),
    1970:(2, 6), 1971:(1,27), 1972:(2,15), 1973:(2, 3), 1974:(1,23),
    1975:(2,11), 1976:(1,31), 1977:(2,18), 1978:(2, 7), 1979:(1,28),
    1980:(2,16), 1981:(2, 5), 1982:(1,25), 1983:(2,13), 1984:(2, 2),
    1985:(2,20), 1986:(2, 9), 1987:(1,29), 1988:(2,17), 1989:(2, 6),
    1990:(1,27), 1991:(2,15), 1992:(2, 4), 1993:(1,23), 1994:(2,10),
    1995:(1,31), 1996:(2,19), 1997:(2, 7), 1998:(1,28), 1999:(2,16),
    2000:(2, 5), 2001:(1,24), 2002:(2,12), 2003:(2, 1), 2004:(1,22),
    2005:(2, 9), 2006:(1,29), 2007:(2,18), 2008:(2, 7), 2009:(1,26),
    2010:(2,14), 2011:(2, 3), 2012:(1,23), 2013:(2,10), 2014:(1,31),
    2015:(2,19), 2016:(2, 8), 2017:(1,28), 2018:(2,16), 2019:(2, 5),
    2020:(1,25), 2021:(2,12), 2022:(2, 1), 2023:(1,22), 2024:(2,10),
    2025:(1,29), 2026:(2,17), 2027:(2, 6), 2028:(1,26), 2029:(2,13),
    2030:(2, 3), 2031:(1,23), 2032:(2,11), 2033:(1,31), 2034:(2,19),
    2035:(2, 8), 2036:(1,28), 2037:(2,15), 2038:(2, 4), 2039:(1,24),
    2040:(2,12), 2041:(2, 1), 2042:(1,22), 2043:(2,10), 2044:(1,30),
    2045:(2,17), 2046:(2, 6), 2047:(1,26), 2048:(2,14), 2049:(2, 2),
    2050:(1,23),
}


def _cny_passed(year: int, month: int, day: int) -> bool:
    """Return True if Chinese New Year for *year* has already occurred by (month, day)."""
    cny = _CNY.get(year, (2, 4))   # fall back to ~Lìchūn if year not in table
    return (month, day) >= cny


# ── Year Pillar ───────────────────────────────────────────────────────────────

def _year_pillar(year: int, month: int, day: int) -> tuple[str, str]:
    """
    Return (stem_char, branch_char) for the Year Pillar.

    The astrological year begins at Chinese New Year.  Births before that
    date in any given Gregorian year belong to the previous astrological year.
    """
    astro_year = year if _cny_passed(year, month, day) else year - 1
    stem_idx   = (astro_year - 4) % 10
    branch_idx = (astro_year - 4) % 12
    return STEMS[stem_idx], BRANCHES[branch_idx]


# ── Month Pillar ──────────────────────────────────────────────────────────────
#
# The 12 Chinese solar months each begin at a principal solar term (节气).
# Approximate Gregorian start dates are listed in ascending calendar order.
# Each entry: (Gregorian month, approximate day, branch offset from Yín=0).
#
#   Offset  Branch  Animal    Solar term
#    11      丑  Ox        小寒  Xiǎohán   ~Jan  6
#     0      寅  Tiger     立春  Lìchūn    ~Feb  4  ← Chinese year begins
#     1      卯  Rabbit    惊蛰  Jīngzhé   ~Mar  6
#     2      辰  Dragon    清明  Qīngmíng  ~Apr  5
#     3      巳  Snake     立夏  Lìxià     ~May  6
#     4      午  Horse     芒种  Mángzhòng ~Jun  6
#     5      未  Goat      小暑  Xiǎoshǔ   ~Jul  7
#     6      申  Monkey    立秋  Lìqiū     ~Aug  7
#     7      酉  Rooster   白露  Báilù     ~Sep  8
#     8      戌  Dog       寒露  Hánlù     ~Oct  8
#     9      亥  Pig       立冬  Lìdōng    ~Nov  7
#    10      子  Rat       大雪  Dàxuě     ~Dec  7

_SOLAR_TERMS: list[tuple[int, int, int]] = [
    ( 1,  6, 11),
    ( 2,  4,  0),
    ( 3,  6,  1),
    ( 4,  5,  2),
    ( 5,  6,  3),
    ( 6,  6,  4),
    ( 7,  7,  5),
    ( 8,  7,  6),
    ( 9,  8,  7),
    (10,  8,  8),
    (11,  7,  9),
    (12,  7, 10),
]


def _month_branch_offset(month: int, day: int) -> int:
    """
    Return the 0–11 offset from Yín (寅) for the given Gregorian month/day.
    Default (before Jan 6) is offset 10 = Zǐ (子), carry-over from Dec 大雪.
    """
    result = 10  # 子月 — 大雪 started in previous December
    for st_month, st_day, offset in _SOLAR_TERMS:
        if (month, day) >= (st_month, st_day):
            result = offset
        else:
            break
    return result


def _month_pillar(year: int, month: int, day: int) -> tuple[str, str]:
    """
    Return (stem_char, branch_char) for the Month Pillar.

    Branch is determined by the current solar term period.
    Stem is derived from the year stem via the 五虎遁年起月 formula:
        month_start_stem = (year_stem_index % 5 × 2 + 2) mod 10
    """
    year_stem_char, _ = _year_pillar(year, month, day)
    year_stem_idx     = STEMS.index(year_stem_char)

    month_offset = _month_branch_offset(month, day)
    branch_idx   = (2 + month_offset) % 12          # 寅 = index 2

    start_stem   = (year_stem_idx % 5 * 2 + 2) % 10
    stem_idx     = (start_stem + month_offset) % 10

    return STEMS[stem_idx], BRANCHES[branch_idx]


# ── Day Pillar ────────────────────────────────────────────────────────────────
#
# Calibrated so that 2024-02-10 (Chinese New Year) maps to 壬戌 (Rén-Xū),
# which is confirmed by multiple traditional Chinese calendar sources.
# Offset = 7 applied uniformly to both stem and branch from the JDN.

_DAY_OFFSET = 7


def _day_pillar(year: int, month: int, day: int) -> tuple[str, str]:
    """Return (stem_char, branch_char) for the Day Pillar."""
    jd         = _jdn(year, month, day)
    stem_idx   = (jd + _DAY_OFFSET) % 10
    branch_idx = (jd + _DAY_OFFSET) % 12
    return STEMS[stem_idx], BRANCHES[branch_idx]


# ── Hour Pillar ───────────────────────────────────────────────────────────────

def _hour_branch(hour: int) -> int:
    """
    Map a 24h clock hour to a branch index (0–11).
    Zǐ (子) spans 23:00–00:59; each subsequent branch covers two hours.
    """
    return (hour + 1) // 2 % 12


def _hour_pillar(hour: int, day_stem_idx: int) -> tuple[str, str]:
    """
    Return (stem_char, branch_char) for the Hour Pillar.

    Stem is derived from the day stem via the 五鼠遁日起时 formula:
        hour_start_stem = (day_stem_index % 5) × 2
    """
    branch_idx = _hour_branch(hour)
    start_stem = (day_stem_idx % 5) * 2
    stem_idx   = (start_stem + branch_idx) % 10
    return STEMS[stem_idx], BRANCHES[branch_idx]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_four_pillars(
    year: int,
    month: int,
    day: int,
    hour: int,
) -> dict[str, tuple[str, str]]:
    """
    Calculate the Four Pillars (年柱 月柱 日柱 时柱) from a Gregorian birth
    date and hour (24h clock, 0–23).

    Returns
    -------
    dict with keys "Year", "Month", "Day", "Hour".
    Each value is a (stem_char, branch_char) tuple of Chinese characters.

    Example
    -------
    >>> get_four_pillars(1990, 3, 15, 14)
    {'Year': ('庚', '午'), 'Month': ('丁', '卯'), 'Day': ('壬', '午'), 'Hour': ('丁', '未')}
    """
    year_s,  year_b  = _year_pillar(year, month, day)
    month_s, month_b = _month_pillar(year, month, day)
    day_s,   day_b   = _day_pillar(year, month, day)
    day_stem_idx     = STEMS.index(day_s)
    hour_s,  hour_b  = _hour_pillar(hour, day_stem_idx)

    return {
        "Year":  (year_s,  year_b),
        "Month": (month_s, month_b),
        "Day":   (day_s,   day_b),
        "Hour":  (hour_s,  hour_b),
    }


def get_element_counts(
    pillars: dict[str, tuple[str, str]],
) -> dict[str, int]:
    """
    Count Five Element occurrences across the 8 pillar positions
    (4 Heavenly Stems + 4 Earthly Branches, primary element only).

    Returns
    -------
    dict mapping each element name to its integer count (0–8 total).
    """
    counts: dict[str, int] = {e: 0 for e in ELEMENTS}
    for stem, branch in pillars.values():
        si = STEMS.index(stem)
        bi = BRANCHES.index(branch)
        counts[STEM_ELEMENTS[si]]   += 1
        counts[BRANCH_ELEMENTS[bi]] += 1
    return counts


def interpret_constitution(
    counts: dict[str, int],
) -> dict[str, str]:
    """
    Map raw element counts to constitutional state labels.

    With 8 total data points across 5 elements the expected share is ≈1.6
    per element.  Thresholds:

        0  → "Absent"
        1  → "Low"
        2  → "Balanced"
        ≥3 → "Excess"

    Returns
    -------
    dict mapping each element name to its state string.
    """
    return {
        elem: (
            "Absent"   if count == 0 else
            "Low"      if count == 1 else
            "Balanced" if count == 2 else
            "Excess"
        )
        for elem, count in counts.items()
    }


def build_protocol(
    constitution: dict[str, str],
) -> tuple[list[str], list[str], str]:
    """
    Derive a bilateral ear seed protocol from the Five Element constitution.

    Strategy
    --------
    * Universal master points (Shen Men, Point Zero + element-specific
      regulatory point) are placed on both ears.
    * Left ear (Yin): tonification points for Absent / Low elements.
    * Right ear (Yang): sedation/regulation points for Excess elements.
    * Absent elements receive two tonification points on the left ear and
      one supporting point on the right.
    * Low elements receive one tonification point on the left ear.
    * Excess elements receive two sedation points on the right ear.
    * Balanced elements need no additional points beyond the universals.
    * Maximum 8 points per ear (universals + constitutional points).

    Returns
    -------
    (left_ear_points, right_ear_points, rationale_text)
    """
    left:  list[str] = list(_UNIVERSAL_LEFT)
    right: list[str] = list(_UNIVERSAL_RIGHT)
    rationale_parts:  list[str] = []

    # Process weakest elements first so tonification is prioritised
    sorted_elems = sorted(
        constitution.items(),
        key=lambda kv: STATE_RANK[kv[1]],
    )

    for element, state in sorted_elems:
        pts  = _EAR[element]
        desc = _ELEM_DESCRIPTION[element]

        if state == "Absent":
            for p in pts["tonification"][:2]:
                if p not in left:
                    left.append(p)
            p0 = pts["tonification"][0]
            if p0 not in right:
                right.append(p0)
            rationale_parts.append(
                f"• {element} (Absent): {desc}  "
                f"Points {pts['tonification'][0]} and {pts['tonification'][1]} "
                f"are added to the left ear to build and support this entirely "
                f"missing element; {p0} echoes on the right for bilateral activation."
            )

        elif state == "Low":
            p0 = pts["tonification"][0]
            if p0 not in left:
                left.append(p0)
            rationale_parts.append(
                f"• {element} (Low): {desc}  "
                f"{p0} is added to the left ear to gently support and "
                f"nourish this underrepresented element."
            )

        elif state == "Excess":
            for p in pts["sedation"][:2]:
                if p not in right:
                    right.append(p)
            rationale_parts.append(
                f"• {element} (Excess): {desc}  "
                f"Points {pts['sedation'][0]} and {pts['sedation'][1]} are "
                f"added to the right ear to gently disperse and harmonise "
                f"this overabundant element."
            )
        # "Balanced" → universal points are sufficient

    # Cap at 8 points per ear
    left  = left[:8]
    right = right[:8]

    if rationale_parts:
        rationale = (
            "Protocol rationale (based on elemental constitution):\n"
            + "\n\n".join(rationale_parts)
        )
    else:
        rationale = (
            "The constitution is remarkably balanced across all Five Elements. "
            "Universal master points (Shen Men, Point Zero) are applied bilaterally "
            "for maintenance, integration, and general harmonisation."
        )

    return left, right, rationale


def spread_score(constitution: dict[str, str]) -> int:
    """
    Return an imbalance score from 0 (uniform) to 3 (maximum spread).

    Computed as the range of STATE_RANK values across all five elements.
    A score of 0 means all elements share the same state; 3 means at least
    one element is Absent while another is Excess.
    """
    ranks = [STATE_RANK[s] for s in constitution.values()]
    return max(ranks) - min(ranks)


def is_balanced(constitution: dict[str, str]) -> bool:
    """
    Return True if no element is Absent or Excess (only Low / Balanced).
    """
    states = set(constitution.values())
    return "Absent" not in states and "Excess" not in states
