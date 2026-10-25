"""
LOGIC LAYER  (pipeline step 3)
==============================
Everything in this file runs BEFORE the AI is called. It is plain Python
rules - no AI - so it is fast, free, predictable and easy to explain.
(Form validation, step 2, lives in validation.py and runs just before this.)

What it does:
  1. redact_identifiers()   - removes phone numbers, emails, ID numbers, names
                              so private data never reaches the AI
  2. extract_vitals()       - pulls BP / pulse / temp / SpO2 / RR out with rules
                              and checks them against safe ranges
  3. find_red_flags()       - spots urgent words (e.g. "chest pain")
  4. detect_sections()      - guesses which of the 4 sections the notes cover
  5. check_completeness()   - lists critical info that is missing
  6. expand_abbreviations() - lists shorthand found (c/o, SOB, h/o ...)
  7. decide_route()         - decides: send to AI, or stop here (saves cost)

The AI later receives these findings as context, and the app uses them to
double-check what the AI returns.
"""

import re

from validation import ENCOUNTER_TYPES, CLINICIAN_ROLES, SEX_OPTIONS  # noqa: F401


# ------------------------------------------------------ 1. privacy redaction
REDACTION_RULES = [
    ("email", r"[\w.+-]+@[\w-]+\.[\w.]+"),
    ("ID number", r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),           # Aadhaar-style
    ("phone", r"(?<![\d/])(?:\+?91[\s-]?)?[6-9]\d{9}\b"),        # Indian mobile
    ("phone", r"(?<![\d/])\+?\d[\d\s-]{9,13}\d\b"),
    ("name", r"(?i)\b(?:patient name|name|pt name)\s*[:\-]\s*[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?"),
    ("name", r"\b(?:Mr|Mrs|Ms|Miss|Shri|Smt)\.?\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?"),
]


def redact_identifiers(text):
    """Replace personal identifiers with [REDACTED-...] tags. Returns (text, list_of_types)."""
    found = []
    for label, pattern in REDACTION_RULES:
        text, count = re.subn(pattern, f"[REDACTED-{label.upper()}]", text)
        found += [label] * count
    return text, found


# ------------------------------------------------------------ 2. vitals
VITAL_PATTERNS = {
    "Blood pressure": r"\bB\.?P\.?\s*[:\-]?\s*(\d{2,3})\s*/\s*(\d{2,3})",
    "Pulse / heart rate": r"\b(?:HR|PR|pulse|P)\s*[:\-]?\s*(\d{2,3})\b",
    "Temperature": r"\b(?:temp|T)\s*[:\-]?\s*(\d{2,3}(?:\.\d)?)\s*(°?\s*[CF])?",
    "Oxygen saturation (SpO2)": r"\b(?:SpO2|SPO2|sats?|O2 sat)\s*[:\-]?\s*(\d{2,3})\s*%?",
    "Respiratory rate": r"\b(?:RR|resp(?:iratory)? rate)\s*[:\-]?\s*(\d{1,2})\b",
}


def extract_vitals(text):
    """Find vital signs with rules and flag values outside a safe adult range."""
    vitals = []
    for name, pattern in VITAL_PATTERNS.items():
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        status, note = "normal", ""
        if name == "Blood pressure":
            sys_, dia = int(m.group(1)), int(m.group(2))
            value = f"{sys_}/{dia} mmHg"
            if sys_ >= 180 or dia >= 120:
                status, note = "critical", "Very high - hypertensive crisis range"
            elif sys_ >= 140 or dia >= 90:
                status, note = "abnormal", "High"
            elif sys_ < 90:
                status, note = "abnormal", "Low"
        elif name == "Pulse / heart rate":
            v = int(m.group(1)); value = f"{v} bpm"
            if v > 130 or v < 40:
                status, note = "critical", "Outside safe range"
            elif v > 100 or v < 50:
                status, note = "abnormal", "Fast" if v > 100 else "Slow"
        elif name == "Temperature":
            v = float(m.group(1))
            unit = (m.group(2) or "").replace("°", "").strip().upper() or ("F" if v > 50 else "C")
            c = (v - 32) * 5 / 9 if unit == "F" else v
            value = f"{v:g} °{unit}"
            if not 30 <= c <= 45:
                status, note = "check", "Value looks unusual - please re-check"
            elif c >= 39.5:
                status, note = "critical", "High fever"
            elif c >= 37.8:
                status, note = "abnormal", "Fever"
        elif name == "Oxygen saturation (SpO2)":
            v = int(m.group(1)); value = f"{v}%"
            if v > 100:
                status, note = "check", "Value above 100% - please re-check"
            elif v < 92:
                status, note = "critical", "Low oxygen"
            elif v < 95:
                status, note = "abnormal", "Slightly low"
        else:  # respiratory rate
            v = int(m.group(1)); value = f"{v} /min"
            if v > 30 or v < 8:
                status, note = "critical", "Outside safe range"
            elif v > 20:
                status, note = "abnormal", "Fast breathing"
        vitals.append({"name": name, "value": value, "status": status,
                       "note": note, "source": m.group(0).strip()})
    return vitals


# --------------------------------------------------------- 3. red flags
RED_FLAG_TERMS = {
    "chest pain": "Possible cardiac cause - needs urgent assessment",
    "breathless at rest": "Breathing difficulty at rest",
    "shortness of breath": "Breathing difficulty",
    "unconscious": "Reduced consciousness",
    "seizure": "Seizure reported",
    "suicid": "Risk to self - needs urgent safety assessment",
    "self harm": "Risk to self - needs urgent safety assessment",
    "self-harm": "Risk to self - needs urgent safety assessment",
    "blood in vomit": "Possible internal bleeding",
    "haematemesis": "Possible internal bleeding",
    "hematemesis": "Possible internal bleeding",
    "slurred speech": "Possible stroke sign",
    "facial droop": "Possible stroke sign",
    "anaphylaxis": "Severe allergic reaction",
    "worst headache": "Possible serious headache cause",
}


def find_red_flags(text):
    low = text.lower()
    hits, seen = [], set()
    for term, why in RED_FLAG_TERMS.items():
        if term in low:
            # skip if clearly negated, e.g. "no chest pain", "denies chest pain"
            idx = low.find(term)
            before = low[max(0, idx - 15):idx]
            if re.search(r"\b(no|denies|nil|without|not)\b", before):
                continue
            if why not in seen:
                hits.append({"term": term, "reason": why})
                seen.add(why)
    return hits


# ----------------------------------------------- 4. which sections are present
SECTION_CUES = {
    "symptoms": r"\b(c/o|complain|presents? with|pain|fever|cough|vomit|nausea|"
                r"headache|dizz|breathless|sob|rash|itch|swelling|fatigue|weakness)",
    "history": r"\b(h/o|history|k/c/o|known case|pmh|past|since|diabetic|dm|htn|"
               r"hypertensive|asthma|surgery|meds?|medication|allerg|nkda|family hx)",
    "observations": r"\b(o/e|on exam|examination|bp|pulse|hr|temp|spo2|rr|chest|"
                    r"abdomen|tender|clear|soft|afebrile|alert|gcs|crepitations)",
    "next_steps": r"\b(plan|advised?|rx|prescribed?|start|continue|review|follow[- ]?up|"
                  r"refer|investigat|test|cbc|x-?ray|ecg|usg|admit|discharge|f/u)",
}


def detect_sections(text):
    low = text.lower()
    return {name: bool(re.search(pattern, low)) for name, pattern in SECTION_CUES.items()}


# ------------------------------------------- 5. missing critical information
COMPLETENESS_CHECKS = [
    ("Allergy status", r"\b(allerg|nkda|nka)", "high",
     "Allergy status is not recorded - needed before any prescription."),
    ("Current medications", r"(\b(meds?|medication|tab|cap|inj|rx|taking|drug|no regular)\b|\bon\s+[a-z]+(in|ol|ide|pril|sartan|statin|ine|one|am)\b)", "medium",
     "Current medications are not mentioned."),
    ("Symptom duration", r"(\b\d+\s*(day|days|d|wk|week|weeks|hrs?|hours?|months?|yrs?|years?)\b|since|x\s*\d+)", "medium",
     "How long the symptoms have lasted is not stated."),
    ("Vital signs", r"\b(bp|pulse|hr|temp|spo2|rr|vitals)", "high",
     "No vital signs recorded."),
    ("Plan / follow-up", r"\b(plan|advised?|review|follow[- ]?up|f/u|refer|rx|prescrib|start)", "high",
     "No plan or follow-up is written."),
]


def check_completeness(text, age):
    low = text.lower()
    missing = []
    for item, pattern, severity, message in COMPLETENESS_CHECKS:
        if not re.search(pattern, low):
            missing.append({"item": item, "severity": severity, "message": message, "source": "rules"})
    if not age:
        missing.append({"item": "Patient age", "severity": "medium",
                        "message": "Age was not entered in the form.", "source": "rules"})
    return missing


# ------------------------------------------------------ 6. abbreviations
ABBREVIATIONS = {
    "c/o": "complains of", "h/o": "history of", "k/c/o": "known case of",
    "o/e": "on examination", "sob": "shortness of breath", "nkda": "no known drug allergies",
    "htn": "hypertension", "dm": "diabetes mellitus", "f/u": "follow-up",
    "rx": "prescription / treatment", "bd": "twice daily", "tds": "three times daily",
    "od": "once daily", "prn": "as needed", "pmh": "past medical history",
    "nad": "no abnormality detected", "cbc": "complete blood count", "ecg": "electrocardiogram",
    "usg": "ultrasound", "uti": "urinary tract infection", "urti": "upper respiratory tract infection",
}


def expand_abbreviations(text):
    low = text.lower()
    found = []
    for short, full in ABBREVIATIONS.items():
        if re.search(r"(?<![a-z])" + re.escape(short) + r"(?![a-z])", low):
            found.append({"short": short.upper() if "/" not in short else short, "meaning": full})
    return found


# ------------------------------------------------------------ 7. routing
def decide_route(sections, word_count):
    """Decide whether the AI is worth calling. Saves cost and stops junk input."""
    covered = sum(sections.values())
    if covered == 0:
        return "stop", ("These notes don't look like clinical notes (no symptoms, history, "
                        "examination or plan found). Nothing was sent to the AI.")
    if covered == 1 and word_count < 25:
        return "stop", ("These notes are too thin to structure safely - only one section "
                        "was detected. Please add more detail. Nothing was sent to the AI.")
    return "ai", "Notes passed all checks and were sent to the AI for structuring."


# --------------------------------------------------------- run everything
def run_logic_layer(data):
    """Runs all checks and returns one report dictionary."""
    redacted, redactions = redact_identifiers(data["raw_notes"])
    sections = detect_sections(redacted)
    word_count = len(redacted.split())
    route, route_reason = decide_route(sections, word_count)
    return {
        "redacted_notes": redacted,
        "redactions": redactions,
        "word_count": word_count,
        "vitals": extract_vitals(redacted),
        "red_flags": find_red_flags(redacted),
        "sections_detected": sections,
        "missing": check_completeness(redacted, data.get("age")),
        "abbreviations": expand_abbreviations(redacted),
        "route": route,
        "route_reason": route_reason,
    }
