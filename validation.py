"""
INPUT VALIDATION  (pipeline step 2)
===================================
Checks the form before anything else happens. If something is wrong the
clinician sees a clear message and nothing is processed.
"""

import re

MIN_WORDS = 12          # fewer words than this = not enough to structure
MAX_CHARS = 6000        # longer than this = probably pasted the wrong thing

ENCOUNTER_TYPES = ["OPD consultation", "Follow-up visit", "Emergency", "Teleconsult", "Ward round"]
CLINICIAN_ROLES = ["Doctor", "Nurse", "Physiotherapist", "Other"]
SEX_OPTIONS = ["Female", "Male", "Other", "Not recorded"]


def validate_form(form):
    """Return (clean_data, errors). errors is a list of plain-English messages."""
    errors = []
    data = {
        "patient_ref": (form.get("patient_ref") or "").strip(),
        "age": (form.get("age") or "").strip(),
        "sex": (form.get("sex") or "").strip(),
        "encounter_type": (form.get("encounter_type") or "").strip(),
        "clinician_role": (form.get("clinician_role") or "").strip(),
        "raw_notes": (form.get("raw_notes") or "").strip(),
        "no_identifiers": form.get("no_identifiers") == "on",
    }

    if not re.fullmatch(r"[A-Za-z0-9\-]{3,20}", data["patient_ref"]):
        errors.append("Patient reference must be 3-20 letters, numbers or dashes "
                      "(use a code like PT-0042, never the patient's name).")

    if data["age"]:
        if not data["age"].isdigit() or not 0 <= int(data["age"]) <= 120:
            errors.append("Age must be a whole number between 0 and 120.")
    if data["sex"] not in SEX_OPTIONS:
        errors.append("Please choose a sex option.")
    if data["encounter_type"] not in ENCOUNTER_TYPES:
        errors.append("Please choose the type of visit.")
    if data["clinician_role"] not in CLINICIAN_ROLES:
        errors.append("Please choose your role.")

    words = len(data["raw_notes"].split())
    if not data["raw_notes"]:
        errors.append("Clinical notes are empty.")
    elif words < MIN_WORDS:
        errors.append(f"Notes are too short to structure ({words} words). "
                      f"Please add at least {MIN_WORDS} words.")
    if len(data["raw_notes"]) > MAX_CHARS:
        errors.append(f"Notes are too long ({len(data['raw_notes'])} characters). "
                      f"Please keep them under {MAX_CHARS}.")
    if not data["no_identifiers"]:
        errors.append("Please confirm the notes contain no patient names or contact details.")

    return data, errors
