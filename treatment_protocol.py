"""
treatment_protocol.py
---------------------
Derives a TCM treatment principle from a Four Pillars constitution and
generates a corresponding bilateral auricular ear seed protocol.

The module is intentionally self-contained — it imports only from
bazi_calculator.py and the standard library.

Auricular point laterality — key distinctions
----------------------------------------------
This system distinguishes two classes of ear point, each with its own
laterality rule:

  1. ORGAN POINTS (anatomical / ipsilateral)
     Each organ point is found on the same ear as the organ it represents
     in the body — regardless of the client's handedness or of whether
     the treatment intent is tonification or sedation.

       Spleen        → Left
       Heart         → Left
       Liver         → Right   (exception: always with Silver)
       Lung          → Bilateral
       Kidney        → Bilateral
       Pericardium   → Bilateral (Center)
       Stomach       → Left (Center/Left)
       Small Intestine → Bilateral (Center)
       Gallbladder   → Right
       Large Intestine → Right (Bilateral/Right)
       Bladder       → Bilateral (Center)
       Triple Burner / San Jiao → Bilateral

  2. FUNCTIONAL POINTS (handedness-dependent, Gold/Silver)
     Functional points do not represent a specific organ; they regulate
     systemic functions. They are divided into Gold Points (stimulating /
     tonifying) and Silver Points (sedating / regulating) at the same
     anatomical location on opposite ears.

     Default rule for RIGHT-HANDED clients:
       Gold Point  = RIGHT ear
       Silver Point = LEFT ear

     Default rule for LEFT-HANDED clients:
       Gold Point  = LEFT ear
       Silver Point = RIGHT ear

     Where a functional point has a known exception to this rule
     (e.g. the Diazepam Analogue Point has its Gold Point on the
     contralateral ear), that is captured in the point's `gold_exception`
     flag and handled explicitly.

  KNOWN POINT-SPECIFIC EXCEPTIONS
  ─────────────────────────────────
  • Liver point: always placed on the RIGHT ear with a Silver designation,
    regardless of treatment principle, because clinical experience shows
    this is the reliably effective placement even when tonification is
    the intent (cf. LR3 convention in auricular practice).

  • Adrenal Gland, Beta-1-Receptor: Gold Point on LEFT ear for right-handed
    (Silver on the dominant right ear, Gold on non-dominant left). Confirmed
    in Bahr/Nogier practitioner notes. The Gold (left) ear ACTIVATES the
    point's function; Silver (right) REDUCES it.

  • Diazepam Analogue: same Gold-left exception. Use 'regulate' intent to
    engage Gold (left) ear and activate the full anxiolytic/sedating function.

  • Anxiety Point, Worry Point, Aggression Point, Nicotine Analogue Point,
    Nervous Liver Point: always use Silver Point (even with steel needles /
    non-metal seeds). These are marked silver_always=True in the database.

  HANDEDNESS
  ──────────
  Pass handedness="right" (default) or handedness="left" to get_protocol().
  If handedness is unknown, the system defaults to right-handed and notes
  the assumption in the protocol rationale.

Five Element treatment principle logic
---------------------------------------
  • Generating (Sheng 生) cycle: Wood → Fire → Earth → Metal → Water → Wood
  • Controlling (Ke 克) cycle:   Wood → Earth → Water → Fire → Metal → Wood
  • Compound inter-elemental patterns take priority over single-element
    imbalances (e.g. Water not nourishing Wood → Liver Yang Rising beats
    treating Water deficiency in isolation).
  • Day Master element and Yin/Yang polarity inform which sub-pattern
    (e.g. Kidney Yang vs Kidney Yin deficiency) applies.

Exports
-------
ProtocolPoint        dataclass  — one resolved ear seed point
TreatmentPrinciple   dataclass  — derived TCM treatment principle
EarProtocol          dataclass  — full bilateral protocol
derive_treatment_principle(pillars, constitution) -> TreatmentPrinciple
build_ear_protocol(principle, constitution, handedness) -> EarProtocol
get_protocol(pillars, constitution, handedness)   -> (TreatmentPrinciple, EarProtocol)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Literal

from bazi_calculator import STEMS, STEM_ELEMENTS, STEM_POLARITY, STATE_RANK


# ─── Five Element Relationships ────────────────────────────────────────────────

_GENERATES: dict[str, str] = {
    "Wood": "Fire", "Fire": "Earth", "Earth": "Metal",
    "Metal": "Water", "Water": "Wood",
}
_MOTHER: dict[str, str] = {child: mother for mother, child in _GENERATES.items()}

_CONTROLS: dict[str, str] = {
    "Wood": "Earth", "Earth": "Water", "Water": "Fire",
    "Fire": "Metal", "Metal": "Wood",
}
_CONTROLLER: dict[str, str] = {target: source for source, target in _CONTROLS.items()}

_ORGANS: dict[str, str] = {
    "Wood":  "Liver and Gallbladder",
    "Fire":  "Heart and Small Intestine",
    "Earth": "Spleen and Stomach",
    "Metal": "Lung and Large Intestine",
    "Water": "Kidney and Bladder",
}


# ─── Auricular Point Database ──────────────────────────────────────────────────
#
# Each entry documents:
#   type          : "organ" | "functional"
#   organ_side    : for organ points — the anatomical ear side
#                   "left" | "right" | "bilateral" | "bilateral/right" |
#                   "bilateral/center" | "center/left"
#   gold_ear      : for functional points — which ear holds the Gold Point
#                   for a RIGHT-HANDED person. Reversed for left-handed.
#                   "right" (default) | "left" (exception)
#   silver_always : True if this point should always use Silver regardless
#                   of tonification intent (e.g. Anxiety Point)
#   liver_exception: True for Liver point (always right, always Silver)
#   action        : primary TCM therapeutic function
#   location      : brief anatomical landmark
#   tcm           : list of TCM indications / pattern keywords

AURICULAR_POINTS: dict[str, dict] = {

    # ══════════════════════════════════════════════════════════════════════════
    # ORGAN POINTS  (ipsilateral — same side as the organ in the body)
    # ══════════════════════════════════════════════════════════════════════════

    # ── Wood — Liver and Gallbladder ────────────────────────────────────────

    "Liver": {
        "type": "organ",
        "organ_side": "right",       # liver is on the right side of the body
        "liver_exception": True,     # always Silver, always Right — see module docstring
        "body_point_tonify": "LR-3 (Tai Chong — Liver Yuan Source)",
        "body_point_sedate": "LR-5 (Li Gou — Liver Luo connecting point)",
        # Wood is tonified through movement — LR-3 with Silver promotes free flow of Liver Qi.
        # This IS the tonification: Wood element receives nourishment by virtue of its movement,
        # not through substance accumulation as Earth or Water does.
        "action": "Moves Liver Qi, restores free flow, supports Liver Blood; Wood is nourished through movement",
        "location": "Concha cymba, posterior section",
        "tcm": ["Liver Qi stagnation", "Liver Blood", "smooth Qi", "emotional regulation", "tendons"],
    },
    "Gallbladder": {
        "type": "organ",
        "organ_side": "right",       # gallbladder is on the right side of the body
        "body_point_tonify": "GB-40 (Qiu Xu — Gallbladder Yuan Source)",
        "body_point_sedate": "GB-37 (Guang Ming — Gallbladder Luo connecting point)",
        "action": "Supports decision and courage, harmonises the Liver-Gallbladder axis",
        "location": "Concha cymba, anterior section",
        "tcm": ["Gallbladder Qi", "decision", "harmonise Wood", "bile"],
    },
    "Liver Yang 1": {
        "type": "organ",
        "organ_side": "right",       # Liver Yang points follow Liver laterality
        "action": "Calms Liver Yang, relieves temporal headache, reduces ascending heat",
        "location": "Helix, between HX5-HX6",
        "tcm": ["Liver Yang rising", "ascending heat", "temporal headache", "dizziness"],
    },
    "Liver Yang 2": {
        "type": "organ",
        "organ_side": "right",
        "action": "Supplements Liver Yang 1 for pronounced ascending Yang patterns",
        "location": "Helix, slightly superior to Liver Yang 1",
        "tcm": ["Liver Yang rising", "hypertension", "headache", "flushing"],
    },
    "Eye Point": {
        "type": "organ",
        "organ_side": "bilateral",   # eyes are bilateral
        "action": "Supports the Liver-eye connection, clears Liver heat from the eyes",
        "location": "Earlobe, lateral section (LO5)",
        "tcm": ["Liver opens to eyes", "eye disorders", "clear Liver heat"],
    },

    # ── Fire — Heart and Small Intestine ────────────────────────────────────

    "Heart": {
        "type": "organ",
        "organ_side": "left",        # heart is on the left side of the body
        "body_point_tonify": "HT-7 (Shen Men — Heart Yuan Source)",
        "body_point_sedate": "HT-5 (Tong Li — Heart Luo connecting point)",
        "action": "Calms the Shen, nourishes Heart Blood, regulates Heart rhythm",
        "action_sedate": "Clears Heart Fire, disperses excess Joy, calms agitation and restlessness",
        "location": "Central concha cavum",
        "tcm": ["Heart Qi", "Shen", "insomnia", "anxiety", "palpitations", "joy"],
    },
    "Small Intestine": {
        "type": "organ",
        "organ_side": "bilateral",   # bilateral/center
        "body_point_tonify": "SI-4 (Wan Gu — Small Intestine Yuan Source)",
        "body_point_sedate": "SI-7 (Zhi Zheng — Small Intestine Luo connecting point)",
        "action": "Supports discernment and assimilation, regulates the Fire-Water axis",
        "location": "Concha cymba, lateral-inferior section",
        "tcm": ["assimilation", "Fire-Water axis", "digestion", "Heart support"],
    },
    "Pericardium": {
        "type": "organ",
        "organ_side": "bilateral",   # center — Pericardium wraps the Heart centrally
        "body_point_tonify": "PC-7 (Da Ling — Pericardium Yuan Source)",
        "body_point_sedate": "PC-6 (Nei Guan — Pericardium Luo; opens Yin Wei vessel)",
        "action": "Protects the Heart Shen, regulates circulation, calms chest oppression",
        "location": "Concha cavum, adjacent to Heart",
        "tcm": ["Heart protector", "chest oppression", "circulation", "calm Shen"],
    },
    "Occiput": {
        "type": "functional",
        "gold_ear": "right",         # functional, default Gold on right for right-handed
        "action": "Calms the mind, relieves posterior head tension, supports sleep",
        "location": "Antitragus, posterior-superior surface",
        "tcm": ["calm mind", "posterior headache", "neck tension", "sleep", "Shen"],
    },
    "Brain Point": {
        "type": "functional",
        "gold_ear": "right",
        "action": "Supports neurological function, memory, consciousness and the Shen",
        "location": "Antitragus, upper border",
        "tcm": ["Shen", "consciousness", "memory", "neurological integration"],
    },

    # ── Earth — Spleen and Stomach ───────────────────────────────────────────

    "Spleen": {
        "type": "organ",
        "organ_side": "left",        # spleen is on the left side of the body
        "body_point_tonify": "SP-3 (Tai Bai — Spleen Yuan Source)",
        "body_point_sedate": "SP-4 (Gong Sun — Spleen Luo; corresponds to Interferon Point)",
        "action": "Tonifies Spleen Qi, supports transformation and transportation, nourishes Blood",
        "action_sedate": "Regulates Spleen Qi, disperses Earth accumulation, resolves Dampness and Phlegm",
        "location": "Concha cymba, medial-superior section",
        "tcm": ["Spleen Qi", "digestion", "Blood production", "intention/Yi", "transformation"],
    },
    "Stomach": {
        "type": "organ",
        "organ_side": "left",        # center/left — stomach is predominantly left/center
        "body_point_tonify": "ST-42 (Chong Yang — Stomach Yuan Source)",
        "body_point_sedate": "ST-40 (Feng Long — Stomach Luo; corresponds to Beta-2-Receptor Point)",
        "action": "Harmonises the Stomach, descends rebellious Qi, supports appetite and grounding",
        "action_sedate": "Disperses Stomach Fire and excess accumulation, resolves Phlegm, redirects rebellious Qi",
        "location": "Concha at antihelix inferior crus / cavum junction",
        "tcm": ["Stomach Qi", "appetite", "nourishment", "descend Qi", "grounding"],
    },

    # ── Metal — Lung and Large Intestine ────────────────────────────────────

    "Lung": {
        "type": "organ",
        "organ_side": "bilateral",   # lungs are bilateral
        "body_point_tonify": "LU-9 (Tai Yuan — Lung Yuan Source; influential point for Blood vessels)",
        "body_point_sedate": "LU-7 (Lie Que — Lung Luo; opens Ren Mai)",
        "action": "Tonifies Lung Qi, supports respiration, strengthens Wei Qi, releases held grief",
        "action_sedate": "Disperses Lung Qi excess, opens chest oppression, releases rigid boundary and suppressed grief",
        "location": "Concha cavum, superior section (CO14)",
        "tcm": ["Lung Qi", "Wei Qi", "grief", "boundary", "respiration", "Corporeal Soul/Po"],
    },
    "Large Intestine": {
        "type": "organ",
        "organ_side": "right",       # bilateral/right — conventionally right-dominant
        "body_point_tonify": "LI-4 (He Gu — Large Intestine Yuan Source; command point for face/head)",
        "body_point_sedate": "LI-6 (Pian Li — Large Intestine Luo connecting point)",
        "action": "Supports letting go and elimination, disperses Metal Qi stagnation",
        "location": "Concha cymba, superior-lateral section",
        "tcm": ["letting go", "elimination", "Metal regulation", "descend Lung Qi"],
    },
    "Allergy Point": {
        "type": "functional",
        "gold_ear": "right",
        "action": "Regulates Wei Qi and immune response; supports Lung boundary function",
        "location": "Helix, near apex (HX4)",
        "tcm": ["Wei Qi", "immune regulation", "Lung boundary", "allergy", "protective Qi"],
    },

    # ── Water — Kidney and Bladder ───────────────────────────────────────────

    "Kidney": {
        "type": "organ",
        "organ_side": "bilateral",   # kidneys are bilateral
        "body_point_tonify": "KI-3 (Tai Xi — Kidney Yuan Source; nourishes Kidney Yin and Yang)",
        "body_point_sedate": "KI-4 (Da Zhong — Kidney Luo connecting point)",
        "action": "Tonifies Kidney Qi, nourishes Kidney Yin and Yang, replenishes Jing essence",
        "action_sedate": "Anchors floating Kidney Qi, clears Empty Heat from Kidney Yin deficiency, subdues excess Water",
        "location": "Antihelical body, superior crus, medial section",
        "tcm": ["Kidney Qi", "Kidney Yin", "Kidney Yang", "Jing", "will-power/Zhi", "fear"],
    },
    "Bladder": {
        "type": "organ",
        "organ_side": "bilateral",   # bilateral/center
        "body_point_tonify": "BL-64 (Jing Gu — Bladder Yuan Source)",
        "body_point_sedate": "BL-58 (Fei Yang — Bladder Luo connecting point)",
        "action": "Supports the Kidney-Bladder axis, nervous system and Jing circulation",
        "location": "Concha cymba, posterior section",
        "tcm": ["Bladder Qi", "nervous system", "Kidney support", "purification"],
    },
    "Lower Back": {
        "type": "organ",
        "organ_side": "bilateral",   # lumbar spine is central/bilateral
        "action": "Addresses lumbar Kidney back-shu zone, strengthens the constitutional root",
        "location": "Antihelix, lumbar section",
        "tcm": ["Kidney root", "lumbar support", "Kidney Yang", "back pain"],
    },

    # ── Triple Burner / San Jiao ─────────────────────────────────────────────

    "San Jiao": {
        "type": "organ",
        "organ_side": "bilateral",   # Triple Burner spans the entire torso — bilateral
        "body_point_tonify": "TB-4 (Yang Chi — Triple Burner Yuan Source; corresponds to Endocrine Pancreas Point)",
        "body_point_sedate": "TB-5 (Wai Guan — Triple Burner Luo; corresponds to Thymus Gland Point)",
        "action": "Regulates the Three Jiao, fluid metabolism, temperature harmonisation",
        "location": "Concha cymba-cavum junction (CO17)",
        "tcm": ["fluid metabolism", "Three Jiao regulation", "temperature balance", "Qi circulation"],
    },

    # ══════════════════════════════════════════════════════════════════════════
    # FUNCTIONAL POINTS  (handedness-dependent, Gold / Silver)
    # ══════════════════════════════════════════════════════════════════════════
    # gold_ear below is always given for a RIGHT-HANDED person.
    # For a LEFT-HANDED person, flip: "right" ↔ "left".

    "Shen Men": {
        "type": "functional",
        "gold_ear": "right",         # Gold on right for right-handed (stimulating/settling)
        "action": "Calms the mind, relieves anxiety, modulates pain, settles the Shen",
        "location": "Triangular fossa, medial two-thirds",
        "tcm": ["calm Shen", "anxiety", "pain", "insomnia", "stress"],
    },
    "Point Zero": {
        "type": "functional",
        "gold_ear": "right",
        "action": "Restores homeostasis, supports autonomic regulation and central equilibrium",
        "location": "Helix root, at junction with antihelix body",
        "tcm": ["homeostasis", "constitutional support", "regulate Qi", "autonomic balance"],
    },
    "Sympathetic Autonomic": {
        "type": "functional",
        "gold_ear": "right",
        "action": "Activates parasympathetic response, relaxes smooth muscle, reduces stress",
        "location": "Antihelix, tip of inferior crus",
        "tcm": ["calm nervous system", "harmonise Yin-Yang", "regulate Qi and Blood"],
    },
    "Thalamus Point": {
        "type": "functional",
        "gold_ear": "right",
        "action": "Neurological integration, emotional regulation, pain modulation",
        "location": "Antitragus, inner surface",
        "tcm": ["calm Shen", "body-mind integration", "regulate emotions", "pain"],
    },
    "Endocrine Point": {
        "type": "functional",
        "gold_ear": "right",
        "action": "Regulates hormonal balance and Yin metabolism; supports Jing",
        "location": "Intertragic notch (CO18)",
        "tcm": ["Yin regulation", "hormonal balance", "Jing", "metabolic function", "endocrine"],
    },
    "Adrenal Gland": {
        "type": "functional",
        # EXCEPTION: Silver on dominant (right) ear, Gold on non-dominant (left) ear.
        # Confirmed in Bahr/Nogier practitioner notes (PDF p.3).
        "gold_ear": "left",
        "body_correspondence": "TB-3 (Triple Burner 3)",
        "action": "Warms Kidney Yang and Wei Qi, anti-inflammatory, supports vitality and cortisol response",
        "location": "Antihelical wall (T12–L1)",
        "tcm": ["Kidney Yang", "Wei Qi", "adrenal vitality", "anti-inflammatory", "warming", "TB-3"],
    },
    "Hunger Point": {
        "type": "functional",
        "gold_ear": "right",
        "action": "Regulates appetite and reduces excessive food cravings (Earth excess patterns)",
        "location": "Tragus, medial-superior surface",
        "tcm": ["regulate appetite", "Earth excess", "Stomach heat", "cravings"],
    },

    # ══════════════════════════════════════════════════════════════════════════
    # FUNCTIONAL POINTS — PHARMACEUTICAL ANALOGUES & SPECIFIC RECEPTORS
    # (from Bahr/Nogier system; body acupuncture correspondences noted)
    # gold_ear values below are given for a RIGHT-HANDED person.
    # ══════════════════════════════════════════════════════════════════════════

    "Interferon Point": {
        "type": "functional",
        "gold_ear": "right",
        "body_correspondence": "SP-4 (Gong Sun — Spleen Luo connecting point)",
        "action": "Boosts immune response and Wei Qi; SP-4 is the Spleen Luo point used in Luo-channel sedation",
        "location": "Cardinal Point (SP-4 auricular zone)",
        "tcm": ["immune support", "Wei Qi", "Spleen Luo", "SP-4", "Blood production"],
    },
    "Diazepam Analogue": {
        "type": "functional",
        # EXCEPTION: Gold on left (non-dominant) for right-handed — activates anxiolytic function.
        # Silver on dominant right = modulating/reducing. Use 'regulate' intent to get Gold (left).
        "gold_ear": "left",
        "body_correspondence": "KI-6 (Zhao Hai — Kidney 6)",
        "action": "Powerful sedative and anxiolytic; expands Kidney Yin and calms the Shen; KI-6 opens the Yin Qiao vessel",
        "location": "Cardinal Point KI-6",
        "tcm": ["sedation", "anxiety", "insomnia", "Kidney Yin", "Heart-Kidney axis", "KI-6", "Yin Qiao"],
    },
    "Pineal Gland Point": {
        "type": "functional",
        "gold_ear": "right",
        "body_correspondence": "BL-62 (Shen Mai — Bladder 62)",
        "action": "Regulates sleep-wake cycle and circadian rhythm; BL-62 opens the Yang Qiao vessel",
        "location": "Cardinal Point BL-62",
        "tcm": ["sleep", "circadian", "insomnia", "melatonin", "Kidney-Heart axis", "BL-62", "Yang Qiao"],
    },
    "Thymus Gland Point": {
        "type": "functional",
        "gold_ear": "right",
        "body_correspondence": "TB-5 (Wai Guan — Triple Burner Luo connecting point)",
        "action": "Immune modulation and Yang Qi activation; TB-5 is the Triple Burner Luo point, opens Yang Wei vessel",
        "location": "Cardinal Point TB-5",
        "tcm": ["immune modulation", "Wei Qi", "Triple Burner Luo", "TB-5", "Yang Wei", "Defensive Qi"],
    },
    "Endocrine Pancreas": {
        "type": "functional",
        "gold_ear": "right",
        "body_correspondence": "TB-4 (Yang Chi — Triple Burner Yuan Source point)",
        "action": "Regulates insulin and blood sugar; supports metabolic function; TB-4 is the Triple Burner Yuan Source",
        "location": "Antihelical wall (T10–T12)",
        "tcm": ["insulin", "blood sugar", "metabolism", "Triple Burner Yuan", "TB-4", "Earth regulation"],
    },
    "Beta-1-Receptor": {
        "type": "functional",
        # EXCEPTION: Same laterality as Adrenal — Silver on dominant (right), Gold on non-dominant (left).
        "gold_ear": "left",
        "action": "Reduces beta-1 receptor activity; antihypertensive; cardiac regulation (metoprolol analogue)",
        "location": "Groove of ascending helix",
        "tcm": ["hypertension", "cardiac", "blood pressure", "Liver Yang", "Heart Yang"],
    },
    "Beta-2-Receptor": {
        "type": "functional",
        "gold_ear": "right",
        "body_correspondence": "ST-40 (Feng Long — Stomach Luo connecting point)",
        "action": "Stimulates beta-2 receptors; broncholytic effect; ST-40 is the Stomach Luo point for resolving Phlegm",
        "location": "Per Bahr/ST-40 auricular correspondence",
        "tcm": ["bronchospasm", "asthma", "Lung Qi descend", "Stomach Luo", "ST-40", "resolve Phlegm"],
    },
    "Caffeine Analogue Point": {
        "type": "functional",
        "gold_ear": "right",
        "body_correspondence": "ST-36 (Zu San Li — Stomach He-Sea, major tonification point)",
        "action": "Stimulatory and energising; tonifies post-heaven Qi; ST-36 is the great tonification point",
        "location": "Identical location to Barbiturate Analogue (opposite metal produces opposite effect)",
        "tcm": ["energy", "stimulation", "Stomach Qi", "post-heaven Qi", "ST-36", "tonify Middle Jiao"],
    },
    "Barbiturate Analogue": {
        "type": "functional",
        "gold_ear": "right",
        "action": "Sedative effect; calms the Shen and reduces agitation; opposite of Caffeine Analogue at same location",
        "location": "Outer helix (same location as Caffeine Analogue; opposite metal)",
        "tcm": ["sedation", "calm Shen", "sleep", "agitation", "reduce over-stimulation"],
    },
    "Frustration Point": {
        "type": "functional",
        "gold_ear": "right",
        "action": "Releases emotional frustration and stagnant Liver Qi; directly addresses the Liver-emotion axis",
        "location": "Near Tragus",
        "tcm": ["frustration", "Liver Qi stagnation", "anger", "emotional stagnation", "Wood regulation"],
    },
    "Psychotherapy Point": {
        "type": "functional",
        "gold_ear": "right",
        "action": "Supports deep psychological processing and mental-emotional integration (Bourdiol point)",
        "location": "Outer helix rim",
        "tcm": ["mental health", "emotional integration", "Shen", "psychological processing", "Heart"],
    },
    "Phosphate Analogue": {
        "type": "functional",
        "gold_ear": "right",
        "action": "Supports mineral metabolism and constitutional energy; metabolic balancing",
        "location": "Functional point per Bahr system",
        "tcm": ["metabolism", "constitutional energy", "mineral balance", "Jing support"],
    },

    # ══════════════════════════════════════════════════════════════════════════
    # SILVER-ALWAYS FUNCTIONAL POINTS
    # These points always use Silver regardless of treatment intent.
    # They are intrinsically regulating/moderating — Gold activation would
    # over-stimulate patterns that need gentle, consistent regulation.
    # ══════════════════════════════════════════════════════════════════════════

    "Anxiety Point": {
        "type": "functional",
        "gold_ear": "right",
        "silver_always": True,
        "action": "Calms acute anxiety and fear; settles the Shen and grounds the Kidney Zhi (Will)",
        "location": "Ear lobe",
        "tcm": ["anxiety", "fear", "Kidney Zhi", "calm Shen", "acute stress"],
    },
    "Worry Point": {
        "type": "functional",
        "gold_ear": "right",
        # Note: PDF specifies Worry Point on left ear — for right-handed, Silver ear = left, consistent.
        "silver_always": True,
        "action": "Releases chronic worry and Spleen overthinking; calms the Yi (Spleen-mind)",
        "location": "Ear lobe (left ear for right-handed)",
        "tcm": ["worry", "Spleen Yi", "overthinking", "rumination", "Earth regulation"],
    },
    "Aggression Point": {
        "type": "functional",
        "gold_ear": "right",
        "silver_always": True,
        "action": "Reduces aggression, irritability, and reactive anger; calms Liver Yang excess",
        "location": "Lower lobe area",
        "tcm": ["aggression", "anger", "Liver Yang", "emotional regulation", "irritability"],
    },
    "Nicotine Analogue Point": {
        "type": "functional",
        "gold_ear": "right",
        "silver_always": True,
        "action": "Reduces cravings for nicotine and stimulants; supports Lung boundary and Po (Corporeal Soul)",
        "location": "Per Bahr system",
        "tcm": ["craving", "addiction", "Lung Po", "boundary", "Lung Qi"],
    },
    "Nervous Liver Point": {
        "type": "functional",
        "gold_ear": "right",
        "silver_always": True,
        "action": "Calms nervous Liver tension; reduces irritability and Wood-generated systemic nervous stress",
        "location": "Antihelical fold",
        "tcm": ["nervous tension", "Liver Qi", "Wood excess", "irritability", "Gallbladder"],
    },
}


# ─── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class ProtocolPoint:
    """
    A single resolved ear seed point, with all placement decisions made.

    ear     : "left" | "right" | "bilateral"
               For bilateral organ points, seeds are placed on both ears.
               For functional points, this is the computed ear (Gold or Silver)
               based on handedness and treatment intent.
    metal   : "gold" | "silver"
               Gold = stimulating / tonifying
               Silver = sedating / regulating
    intent  : "tonify" | "sedate" | "regulate"
    """
    name:       str
    ear:        str    # "left", "right", "bilateral"
    metal:      str    # "gold", "silver"
    intent:     str    # "tonify", "sedate", "regulate"
    action:     str    # human-readable TCM action
    point_type: str    # "organ", "functional"
    note:       str = ""  # exception or application note


@dataclass
class TreatmentPrinciple:
    """
    Treatment principle derived directly from the Five Element chart states.

    No TCM pattern intermediary — the Ba Zi chart shows the elemental imbalances;
    the protocol corrects them via Yuan Source (tonify) and Luo (sedate) auricular
    representations, bolstered by relevant functional points.
    """
    deficient:   list[str]   # elements to tonify via Yuan Source auricular point (Absent or Low)
    excess:      list[str]   # elements to regulate via Luo auricular point (Excess)
    principle:   str         # e.g. "Nourish Water · Tonify Metal · Regulate Wood"
    description: str         # plain-English description of the constitutional picture
    day_master:  str         # e.g. "Wood Yang (Jiǎ)"


@dataclass
class EarProtocol:
    """
    Bilateral ear seed protocol with full placement detail.

    points      : resolved list of ProtocolPoint objects (one per distinct name,
                  bilateral points appear once with ear="bilateral")
    left_ear    : derived list of point names for the left ear
    right_ear   : derived list of point names for the right ear
    bilateral   : derived list of point names on both ears
    handedness  : "right" | "left" — assumption used for functional point assignment
    principle   : treatment principle text
    rationale   : full clinical rationale
    note        : primary practitioner application note
    """
    points:    list[ProtocolPoint]
    left_ear:  list[str]
    right_ear: list[str]
    bilateral: list[str]
    handedness: str
    principle:  str
    rationale:  str
    note:       str


# ─── Laterality Resolution ─────────────────────────────────────────────────────

def _resolve_ear(
    point_name: str,
    intent: Literal["tonify", "sedate", "regulate"],
    handedness: Literal["right", "left"],
) -> tuple[str, str, str]:
    """
    Resolve the correct ear, metal, and any exception note for a given point.

    Returns (ear, metal, note).
      ear   : "left" | "right" | "bilateral"
      metal : "gold" | "silver"
      note  : empty string, or a practitioner note about exceptions
    """
    info = AURICULAR_POINTS.get(point_name, {})
    ptype = info.get("type", "functional")

    # ── Organ points: ipsilateral rule ────────────────────────────────────────
    if ptype == "organ":
        side = info.get("organ_side", "bilateral")

        # Map "bilateral/right", "center/left", etc. to canonical values
        if side in ("bilateral", "bilateral/center", "bilateral/right"):
            ear = "bilateral"
        elif side in ("left", "center/left"):
            ear = "left"
        elif side == "right":
            ear = "right"
        else:
            ear = "bilateral"

        # Known exception: Liver point — always right, always Silver
        if info.get("liver_exception"):
            return "right", "silver", (
                "Liver point placed on right ear with Silver (sedating) designation "
                "per established clinical convention, regardless of treatment intent."
            )

        # Determine metal based on intent
        metal = "gold" if intent == "tonify" else "silver"
        return ear, metal, ""

    # ── Functional points: handedness-based Gold/Silver ───────────────────────
    # gold_ear in the database is given for a RIGHT-HANDED person.
    # For left-handed, flip it.
    gold_ear_rh = info.get("gold_ear", "right")  # Gold ear for right-handed

    if handedness == "right":
        gold_ear = gold_ear_rh
    else:
        # Left-handed: flip the Gold/Silver sides
        gold_ear = "left" if gold_ear_rh == "right" else "right"

    silver_ear = "left" if gold_ear == "right" else "right"

    # Points that must always use Silver — driven by silver_always field in the database.
    # These are intrinsically regulating points; Gold activation would over-stimulate.
    if info.get("silver_always", False):
        return silver_ear, "silver", (
            f"{point_name} always uses Silver designation per clinical protocol "
            "(silver_always point — Gold activation contraindicated)."
        )

    # General functional point: Gold for tonify/regulate, Silver for sedate
    if intent in ("tonify", "regulate"):
        return gold_ear, "gold", ""
    else:  # sedate
        return silver_ear, "silver", ""


def _make_protocol_point(
    name: str,
    intent: Literal["tonify", "sedate", "regulate"],
    handedness: Literal["right", "left"],
) -> Optional[ProtocolPoint]:
    """
    Create a fully resolved ProtocolPoint for a given point name and intent.
    Returns None if the point name is not in the database.
    """
    info = AURICULAR_POINTS.get(name)
    if not info:
        return None
    ear, metal, note = _resolve_ear(name, intent, handedness)
    # Pick action text based on intent — sedate/regulate uses action_sedate if available
    if intent in ("sedate", "regulate") and "action_sedate" in info:
        action = info["action_sedate"]
    else:
        action = info.get("action", "")
    return ProtocolPoint(
        name       = name,
        ear        = ear,
        metal      = metal,
        intent     = intent,
        action     = action,
        point_type = info.get("type", "functional"),
        note       = note,
    )


# ─── Element → Point Mappings ─────────────────────────────────────────────────
#
# Protocol selection is driven DIRECTLY by element states in the Ba Zi chart.
# No TCM pattern intermediary — the chart shows the imbalance; the protocol
# corrects it using Yuan Source (tonification) and Luo (sedation) auricular
# representations, bolstered by relevant functional points.
#
# TONIFICATION (Absent / Low element):
#   → Primary Zang organ point with Gold (Yuan Source body-point correspondence)
#   → Secondary organ added for Absent severity
#   → One functional bolster that reinforces the Yuan tonification
#
# SEDATION (Excess element):
#   → Primary Zang organ point with Silver (Luo body-point correspondence)
#   → One functional bolster that reinforces the Luo dispersal

# Universal master points — two anchor points in every protocol.
# "regulate" intent → Gold ear for functional points, harmonising rather than
# strongly tonifying or sedating.
_UNIVERSAL_POINTS: list[tuple[str, str]] = [
    ("Shen Men",   "regulate"),
    ("Point Zero", "regulate"),
]

# Primary Zang (Yin) organ per element — the deepest constitutional point.
# Tonification → Yuan Source body correspondence.  Sedation → Luo body correspondence.
_PRIMARY_ORGAN: dict[str, str] = {
    "Wood":  "Liver",
    "Fire":  "Heart",
    "Earth": "Spleen",
    "Metal": "Lung",
    "Water": "Kidney",
}

# Secondary organ per element — added for Absent severity or to fill remaining budget.
# Chosen for the richest auricular representation and clinical utility.
_SECONDARY_ORGAN: dict[str, str] = {
    "Wood":  "Gallbladder",
    "Fire":  "Pericardium",      # deeper Heart-protector; richer auricular zone than Small Intestine
    "Earth": "Stomach",
    "Metal": "Large Intestine",
    "Water": "Lower Back",       # lumbar Kidney root; more constitutional than Bladder
}

# Functional bolsters for TONIFICATION — ordered by priority.
# First available bolster that fits within the 10-point budget is used.
# These correspond to Yuan Source body-point representations or powerful
# tonifying functional analogues for the element.
_TONIFY_BOLSTERS: dict[str, list[str]] = {
    "Wood":  [],                          # LR-3 Yuan expressed through Liver organ point itself
    "Fire":  ["Pineal Gland Point"],      # BL-62 — Heart-Kidney Shen axis and circadian rhythm
    "Earth": ["Endocrine Pancreas",       # TB-4 Yuan Source — metabolic and Earth tonification
              "Caffeine Analogue Point"], # ST-36 — great Middle Jiao tonification point
    "Metal": ["Thymus Gland Point",       # TB-5 Luo — immune modulation, Yang Wei, Wei Qi
              "Allergy Point"],           # Wei Qi boundary support
    "Water": ["Endocrine Point",          # Yin / Jing substrate support
              "Adrenal Gland"],           # Kidney Yang warming (Gold on non-dominant ear)
}

# Functional bolsters for SEDATION — ordered by priority.
# These correspond to Luo body-point representations or emotionally releasing
# functional points for the element.
_SEDATE_BOLSTERS: dict[str, list[str]] = {
    "Wood":  ["Frustration Point",        # LR-5 Luo emotional axis — releases held Wood frustration
              "Nervous Liver Point"],     # silver_always — calms Wood-driven systemic tension
    "Fire":  ["Anxiety Point",            # silver_always — direct Shen settling
              "Diazepam Analogue"],       # KI-6 — deep Yin calm; use "regulate" → Gold on non-dominant ear
    "Earth": ["Interferon Point",         # SP-4 Luo — sedates Spleen Excess via Luo channel
              "Hunger Point"],            # regulates accumulation drive
    "Metal": ["Aggression Point",         # silver_always — releases held Metal anger / rigidity
              "Psychotherapy Point"],     # opens deeper grief and emotional processing
    "Water": [],                          # Water excess is rare; organ point sedation is sufficient
}


# ─── Internal Helpers ──────────────────────────────────────────────────────────

def _rank(state: str) -> int:
    return STATE_RANK.get(state, 2)

def _is_weak(state: str) -> bool:
    return _rank(state) <= 1

def _is_excess(state: str) -> bool:
    return _rank(state) >= 3

def _extremity(state: str) -> int:
    """Return a sort key: higher = more imbalanced and therefore higher priority."""
    if state == "Absent":  return 4
    if state == "Excess":  return 3
    if state == "Low":     return 2
    return 1   # Balanced

def _day_master_info(pillars: dict) -> tuple[str, str, str]:
    from bazi_calculator import STEM_NAMES
    day_stem = pillars["Day"][0]
    idx = STEMS.index(day_stem)
    return STEM_ELEMENTS[idx], STEM_POLARITY[idx], STEM_NAMES[idx]


_ORGANS: dict[str, str] = {
    "Wood":  "Liver / Gallbladder",
    "Fire":  "Heart / Pericardium",
    "Earth": "Spleen / Stomach",
    "Metal": "Lung / Large Intestine",
    "Water": "Kidney / Bladder",
}


# ─── Public API ────────────────────────────────────────────────────────────────

def get_protocol(
    pillars:      dict[str, tuple[str, str]],
    constitution: dict[str, str],
    handedness:   Literal["right", "left"] = "right",
) -> tuple[TreatmentPrinciple, EarProtocol]:
    """
    High-level entry point.

    Derives a treatment principle and ear seed protocol directly from the
    elemental constitution returned by the Ba Zi calculator.

    Deficient elements (Absent / Low) → tonified via Yuan Source auricular
    representation (Gold).
    Excess elements → sedated via Luo auricular representation (Silver).
    Functional bolsters reinforce the primary organ work.

    handedness : "right" (default) | "left"

    Returns (TreatmentPrinciple, EarProtocol).
    """
    dm_element, dm_polarity, dm_name = _day_master_info(pillars)
    principle = _derive_principle(constitution, dm_element, dm_polarity, dm_name)
    protocol  = _build_protocol(principle, constitution, handedness)
    return principle, protocol


def _derive_principle(
    constitution: dict[str, str],
    dm_element:   str,
    dm_polarity:  str,
    dm_name:      str,
) -> TreatmentPrinciple:
    """
    Build a TreatmentPrinciple by reading element states directly.
    No pattern matching — the chart IS the prescription.
    """
    ELEMENTS  = ["Wood", "Fire", "Earth", "Metal", "Water"]
    deficient = [(e, constitution[e]) for e in ELEMENTS if constitution[e] in ("Absent", "Low")]
    excess    = [e for e in ELEMENTS if constitution[e] == "Excess"]

    # ── Principle string ──────────────────────────────────────────────────────
    parts: list[str] = []
    for elem, state in deficient:
        parts.append(f"Nourish {elem}" if state == "Absent" else f"Tonify {elem}")
    for elem in excess:
        parts.append(f"Regulate {elem}")
    if not parts:
        parts = ["Harmonise Five Elements"]
    principle_str = " · ".join(parts)

    # ── Plain-English description ─────────────────────────────────────────────
    desc_parts: list[str] = []

    if deficient:
        for elem, state in deficient:
            organs   = _ORGANS[elem]
            severity = "absent from the chart" if state == "Absent" else "below strength"
            desc_parts.append(f"{elem} ({organs}) is {severity}")
        desc = "; ".join(desc_parts) + ". "

        if len(deficient) == 1:
            elem, state = deficient[0]
            if state == "Absent":
                desc += (
                    f"When an element is entirely absent, the meridians it governs carry no "
                    f"constitutional representation. The protocol prioritises deep nourishment "
                    f"of the {elem} element through its Yuan Source auricular point. "
                )
            else:
                desc += (
                    "The protocol tonifies this element through its Yuan Source auricular "
                    "representation, stimulating the root of the meridian with Gold. "
                )
        else:
            desc += (
                "The protocol addresses each deficiency through the Yuan Source auricular "
                "representation of the relevant meridians, working from the most depleted outward. "
            )
    else:
        desc = ""

    if excess:
        excess_str = " and ".join(f"{e} ({_ORGANS[e]})" for e in excess)
        verb = "is" if len(excess) == 1 else "are"
        desc += (
            f"{excess_str} {verb} in excess. "
            "The protocol sedates via the Luo-channel auricular representation, "
            "dispersing what has accumulated and restoring flow. "
        )

    if not deficient and not excess:
        desc = (
            "The chart shows a well-distributed elemental constitution — no element absent "
            "or in excess. The universal master points support homeostasis and maintain "
            "the harmonious flow already present."
        )

    # Note if Day Master element is itself imbalanced
    dm_state = constitution.get(dm_element, "Balanced")
    if dm_state in ("Absent", "Low"):
        desc += (
            f"Notably, the Day Master element ({dm_element}, {dm_polarity} — {dm_name}) "
            f"is itself {dm_state.lower()}, making this a core constitutional priority. "
        )
    elif dm_state == "Excess":
        desc += (
            f"The Day Master element ({dm_element}, {dm_polarity} — {dm_name}) is dominant, "
            "which is a characteristic constitutional signature rather than a pathology "
            "in isolation, though its regulation supports overall balance. "
        )

    return TreatmentPrinciple(
        deficient   = [e for e, _ in deficient],
        excess      = excess,
        principle   = principle_str,
        description = desc.strip(),
        day_master  = f"{dm_element} {dm_polarity} ({dm_name})",
    )


def _build_protocol(
    principle:    TreatmentPrinciple,
    constitution: dict[str, str],
    handedness:   Literal["right", "left"] = "right",
) -> EarProtocol:
    """
    Build the bilateral ear seed protocol from the treatment principle.

    Budget: 4 universal master points + up to 6 constitutional points = 10 total.

    Phased allocation — ensures every imbalanced element gets at least its
    primary organ addressed before secondaries or bolsters are added:

      Phase 1 — Universal master points (always 4):
                 Shen Men, Point Zero, Thalamus Point, Sympathetic Autonomic

      Phase 2 — Primary Zang organ for each imbalanced element in priority order:
                 Absent (4) > Excess (3) > Low (2)
                 Tonified with Gold (Yuan Source correspondence) or
                 Sedated with Silver (Luo channel correspondence)

      Phase 3 — Secondary organ for Absent elements only:
                 Added from most extreme element downward, budget permitting

      Phase 4 — One functional bolster per element (most extreme first):
                 Reinforces the primary organ work; uses the first available
                 bolster from the element's list that fits within the budget

    Organ points tonified with Gold correspond to the Yuan Source in body acupuncture.
    Organ points sedated with Silver correspond to the Luo point in body acupuncture.
    """
    seen:  set[str]               = set()
    specs: list[tuple[str, str]]  = []   # (point_name, intent)

    def add(name: str, intent: str) -> bool:
        if len(specs) >= 8:
            return False
        if name not in seen and name in AURICULAR_POINTS:
            seen.add(name)
            specs.append((name, intent))
            return True
        return False

    # Phase 1 — Two universal anchor points (always present)
    for name, intent in _UNIVERSAL_POINTS:
        add(name, intent)

    # Sort imbalanced elements by extremity descending; skip Balanced
    ELEMENTS = ["Wood", "Fire", "Earth", "Metal", "Water"]
    sorted_elems = [
        (e, constitution[e]) for e in ELEMENTS
        if constitution[e] != "Balanced"
    ]
    sorted_elems.sort(key=lambda kv: _extremity(kv[1]), reverse=True)

    # Phase 2 — Primary Zang organ for every imbalanced element.
    # Wood uses "regulate" (Silver/LR-3) because Wood tonification IS movement —
    # free flow of Liver Qi nourishes the Wood element. Other elements tonify
    # via Yuan Source (Gold).
    for element, state in sorted_elems:
        if state in ("Absent", "Low"):
            intent = "regulate" if element == "Wood" else "tonify"
            add(_PRIMARY_ORGAN[element], intent)
        elif state == "Excess":
            add(_PRIMARY_ORGAN[element], "sedate")

    # Phase 3 — Secondary organ only when there is exactly one Absent element
    # (focused protocol: deeper support where the deficiency is most severe).
    absent_elems = [e for e, s in sorted_elems if s == "Absent"]
    if len(absent_elems) == 1:
        add(_SECONDARY_ORGAN[absent_elems[0]], "tonify")

    # Phase 4 — One functional bolster for the single highest-priority element only.
    # Keeps the protocol focused rather than adding bolsters for every imbalance.
    if sorted_elems:
        top_element, top_state = sorted_elems[0]
        if top_state in ("Absent", "Low"):
            for bolster in _TONIFY_BOLSTERS[top_element]:
                if add(bolster, "tonify"):
                    break
        elif top_state == "Excess":
            for bolster in _SEDATE_BOLSTERS[top_element]:
                intent = "regulate" if bolster == "Diazepam Analogue" else "sedate"
                if add(bolster, intent):
                    break

    # 3. Resolve each (name, intent) pair into a ProtocolPoint
    resolved: list[ProtocolPoint] = []
    for name, intent in specs:
        pp = _make_protocol_point(name, intent, handedness)
        if pp:
            resolved.append(pp)

    # 4. Derive left / right / bilateral convenience lists
    left_ear  = [p.name for p in resolved if p.ear in ("left",  "bilateral")]
    right_ear = [p.name for p in resolved if p.ear in ("right", "bilateral")]
    bilateral = [p.name for p in resolved if p.ear == "bilateral"]

    # 5. Build practitioner note and rationale
    deficient_str = ", ".join(principle.deficient) if principle.deficient else "none"
    excess_str    = ", ".join(principle.excess)    if principle.excess    else "none"
    handedness_note = (
        f"Handedness: {handedness.capitalize()}-handed — "
        f"functional Gold ear = {'right' if handedness == 'right' else 'left'}."
    )
    note = (
        f"Deficient elements (tonified via Yuan Source auricular point): {deficient_str}. "
        f"Excess elements (regulated via Luo auricular point): {excess_str}. "
        f"{handedness_note} "
        "Liver point always placed on right ear with Silver per auricular convention (ipsilateral + clinical rule). "
        "Adrenal Gland, Beta-1-Receptor, and Diazepam Analogue carry Gold on the non-dominant ear."
    )
    rationale = (
        f"Treatment principle: {principle.principle}\n"
        f"Day Master: {principle.day_master}\n\n"
        f"Deficient → tonify via Yuan Source auricular representation (Gold): {deficient_str}\n"
        f"Excess    → sedate via Luo auricular representation (Silver): {excess_str}\n\n"
        + handedness_note
    )

    return EarProtocol(
        points     = resolved,
        left_ear   = left_ear,
        right_ear  = right_ear,
        bilateral  = bilateral,
        handedness = handedness,
        principle  = principle.principle,
        rationale  = rationale,
        note       = note,
    )


# Preserve backward-compatible aliases used by tests / api_server
derive_treatment_principle = _derive_principle
build_ear_protocol         = _build_protocol

