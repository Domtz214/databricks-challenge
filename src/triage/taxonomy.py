"""Report categories, HumAID label mapping, and a bilingual keyword baseline.

The keyword classifier is (a) the zero-cost fallback when AI Functions are not
available, and (b) the *baseline* you beat in the video ("0.71 keywords vs 0.86
LLM").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

CATEGORIES = [
    "rescue_request",         # trapped, water rising, need evacuation NOW
    "medical_emergency",      # injured, insulin/dialysis/oxygen, pregnant in labor
    "shelter_need",           # displaced, need somewhere to go
    "supplies_need",          # food, water, formula, generator
    "infrastructure_damage",  # road/bridge out, power lines down
    "info_or_other",          # advice, volunteering, general updates
    "not_relevant",
]
LIFE_THREATENING = {"rescue_request", "medical_emergency"}

# HumAID (Alam et al., 2021) humanitarian labels -> our taxonomy.
# NOTE: HumAID's `requests_or_urgent_needs` (333 Harvey tweets) mixes rescue, medical
# and supplies requests. TODO(you): hand-label all of them plus a sample of
# `injured_or_dead_people` (notebook 02 exports relabel_todo.csv). That relabeled
# set is the life-threatening ground truth for the classifier and the back-test.
HUMAID_TO_TRIAGE = {
    "requests_or_urgent_needs": "rescue_request",
    # Mostly casualty *news* ("death toll rises"), not requests for help. Treat as info;
    # real medical emergencies come from hand relabels (notebook 02 exports a sample).
    "injured_or_dead_people": "info_or_other",
    "missing_or_found_people": "rescue_request",
    "displaced_people_and_evacuations": "shelter_need",
    "infrastructure_and_utility_damage": "infrastructure_damage",
    "rescue_volunteering_or_donation_effort": "info_or_other",
    "caution_and_advice": "info_or_other",
    "other_relevant_information": "info_or_other",
    "sympathy_and_support": "not_relevant",
    "not_humanitarian": "not_relevant",
    "dont_know_cant_judge": "not_relevant",
}


def normalize(text: str) -> str:
    """Lowercase and strip accents so 'Ático' matches 'atico'."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


# Order matters: first matching category wins.
_KEYWORDS: list[tuple[str, list[str]]] = [
    ("medical_emergency", [
        "insulin", "dialysis", "oxygen", "injured", "bleeding", "heart attack",
        "unconscious", "in labor", "seizure", "ambulance", "not breathing",
        "insulina", "dialisis", "oxigeno", "herido", "sangr", "infarto",
        "inconsciente", "de parto", "convulsion", "ambulancia", "no respira",
    ]),
    ("rescue_request", [
        "trapped", "stuck", "on the roof", "in the attic", "rescue", "water rising",
        "water is rising", "need a boat", "need boat", "help us", "sos",
        "chest deep", "waist deep", "can't get out", "cant get out",
        "atrapad", "en el techo", "en el atico", "rescate", "rescaten", "auxilio",
        "el agua sube", "el agua esta subiendo", "necesitamos un bote", "ayudenos",
        "ayuda por favor",
    ]),
    ("shelter_need", [
        "need shelter", "nowhere to go", "evacuat", "displaced", "lost our home",
        "refugio", "albergue", "no tenemos donde", "evacua", "perdimos la casa",
    ]),
    ("supplies_need", [
        "need water", "need food", "formula", "diapers", "generator", "no power",
        "necesitamos agua", "comida", "panales", "generador", "sin luz",
    ]),
    ("infrastructure_damage", [
        "road closed", "bridge", "power line", "downed line", "washed out",
        "sinkhole", "levee", "dam ",
        "calle cerrada", "puente", "cables caidos", "represa",
    ]),
    ("info_or_other", [
        "volunteer", "donate", "shelter open", "update", "advisory", "warning",
        "voluntari", "donar", "aviso", "alerta",
    ]),
]

_VULNERABLE = [
    "elderly", "grandma", "grandpa", "grandmother", "grandfather", "senior",
    "baby", "infant", "newborn", "toddler", "kids", "children", "child",
    "pregnant", "disabled", "wheelchair", "bedridden", "oxygen", "dialysis",
    "nursing home", "assisted living", "blind", "deaf",
    "anciano", "ancian", "abuela", "abuelo", "bebe", "ninos", "nino", "embarazada",
    "discapacitad", "silla de ruedas", "asilo", "ciego", "sordo",
]

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "uno": 1, "una": 1, "dos": 2, "tres": 3,
    "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}
_PEOPLE_RE = re.compile(
    r"\b(\d{1,3}|" + "|".join(_NUM_WORDS) + r")\s+"
    r"(people|persons|adults|kids|children|of us|family members|personas|adultos|ninos|de nosotros)\b"
)


@dataclass
class KeywordResult:
    category: str
    p_life_threat: float
    vulnerable: bool
    people_count: int | None


def extract_people_count(text: str) -> int | None:
    m = _PEOPLE_RE.search(normalize(text))
    if not m:
        return None
    tok = m.group(1)
    return int(tok) if tok.isdigit() else _NUM_WORDS[tok]


def is_vulnerable(text: str) -> bool:
    t = normalize(text)
    return any(k in t for k in _VULNERABLE)


def classify_keywords(text: str) -> KeywordResult:
    t = normalize(text)
    category = "not_relevant"
    for cat, words in _KEYWORDS:
        if any(w in t for w in words):
            category = cat
            break
    # Crude "probability": keyword hits are confident but not certain.
    p = {"rescue_request": 0.8, "medical_emergency": 0.85}.get(category, 0.05)
    return KeywordResult(category, p, is_vulnerable(text), extract_people_count(text))
