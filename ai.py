"""
AI LAYER  (pipeline steps 4 - 7)
================================
  4. build_prompt()      - builds the instructions + notes sent to Claude
  5. call_claude()       - the real API call (with timeout + one retry)
  6. parse_output()      - turns Claude's reply into clean Python data
  7. ground_and_score()  - THE SAFETY CHECK: every item Claude returns must
                           quote the original notes word-for-word. If the quote
                           is not in the notes, the item is removed and counted
                           as a "blocked invention". Then a confidence score is
                           calculated from facts, not from the AI's opinion alone.
"""

import json
import os
import re
import time

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
# Price per million tokens (USD) - used for the cost estimate on the dashboard
PRICES = {"claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}

SECTIONS = ["symptoms", "history", "observations", "next_steps"]
SECTION_LABELS = {"symptoms": "Symptoms", "history": "History",
                  "observations": "Observations", "next_steps": "Next steps"}


class AIError(Exception):
    """A friendly error we can show to the user."""


# ------------------------------------------------------- 4. prompt construction
SYSTEM_PROMPT = """You are a clinical documentation assistant. You re-organise a \
clinician's raw notes into four sections. You are a FORMATTER, not a doctor.

ABSOLUTE RULES
1. Use ONLY information written in the notes. Never add a diagnosis, drug, dose, \
test, value, duration or plan that is not written there.
2. Every item MUST include "evidence": an exact, word-for-word copy of the shortest \
part of the notes that supports it (copy spelling and abbreviations exactly). \
If you cannot quote it, do not include the item.
3. You may expand abbreviations in "text" (e.g. "c/o" -> "complains of"), but do not \
add meaning that is not there.
4. If a section has nothing in the notes, return an empty list for it. An empty \
section is correct; an invented one is dangerous.
5. Put anything unclear, contradictory or illegible in "ambiguities".
6. List critical information a clinician would expect but which is NOT in the \
notes in "missing_information". Never guess what it might be.
7. [REDACTED-...] tags are removed private data. Ignore them.

Return ONLY valid JSON, no other text, in exactly this shape:
{
  "symptoms":     [{"text": "...", "evidence": "..."}],
  "history":      [{"text": "...", "evidence": "..."}],
  "observations": [{"text": "...", "evidence": "..."}],
  "next_steps":   [{"text": "...", "evidence": "..."}],
  "missing_information": [{"item": "...", "why_it_matters": "..."}],
  "ambiguities": ["..."],
  "section_confidence": {"symptoms": "high|medium|low", "history": "high|medium|low",
                         "observations": "high|medium|low", "next_steps": "high|medium|low"},
  "one_line_summary": "one neutral sentence using only facts from the notes"
}"""


def build_prompt(data, report):
    """Combine the form fields, the logic-layer findings and the notes."""
    abbrev = ", ".join(f"{a['short']} = {a['meaning']}" for a in report["abbreviations"]) or "none"
    already_missing = ", ".join(m["item"] for m in report["missing"]) or "none"
    vitals = ", ".join(f"{v['name']} {v['value']}" for v in report["vitals"]) or "none found"
    return f"""CONTEXT FROM THE FORM (for orientation only - do not copy into sections)
- Visit type: {data['encounter_type']}
- Written by: {data['clinician_role']}
- Age: {data['age'] or 'not given'} | Sex: {data['sex']}

CHECKS ALREADY DONE BY RULES
- Abbreviations found: {abbrev}
- Vitals detected: {vitals}
- Rules say these are missing: {already_missing}

RAW CLINICAL NOTES (the ONLY source of facts)
<<<
{report['redacted_notes']}
>>>

Structure these notes now. Remember: every item needs exact "evidence" from the notes."""


# ----------------------------------------------------------- 5. LLM API call
def call_claude(system_prompt, user_prompt):
    """Call the Claude API. Returns (text, usage_dict, seconds). Raises AIError."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AIError("The AI key is not set up. Add ANTHROPIC_API_KEY in Replit Secrets.")
    try:
        import anthropic
    except ImportError:
        raise AIError("The 'anthropic' package is not installed. Run: pip install -r requirements.txt")

    client = anthropic.Anthropic(api_key=api_key, timeout=60.0, max_retries=1)
    start = time.time()
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            temperature=0,               # most predictable output
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.AuthenticationError:
        raise AIError("The AI key was rejected. Please check ANTHROPIC_API_KEY in Secrets.")
    except anthropic.RateLimitError:
        raise AIError("The AI service is busy right now. Please try again in a minute.")
    except anthropic.APITimeoutError:
        raise AIError("The AI took too long to answer. Please try again.")
    except anthropic.APIConnectionError:
        raise AIError("Could not reach the AI service. Check the internet connection.")
    except anthropic.APIStatusError as e:
        raise AIError(f"The AI service returned an error ({e.status_code}). Please try again.")

    text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
    usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
    return text, usage, round(time.time() - start, 2)


def estimate_cost(usage):
    price_in, price_out = PRICES.get(MODEL, PRICES["claude-sonnet-5-5"])
    return round(usage["input_tokens"] / 1e6 * price_in + usage["output_tokens"] / 1e6 * price_out, 6)


# ------------------------------------------------------------ 6. parsing
def parse_output(text):
    """Pull the JSON out of Claude's reply and check its shape. Raises AIError."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise AIError("The AI reply was not in the expected format.")
    try:
        raw = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        raise AIError("The AI reply could not be read (broken format).")

    result = {}
    for section in SECTIONS:
        items = raw.get(section) or []
        result[section] = [
            {"text": str(i.get("text", "")).strip(), "evidence": str(i.get("evidence", "")).strip()}
            for i in items if isinstance(i, dict) and i.get("text")
        ]
    result["missing_information"] = [
        {"item": str(m.get("item", "")), "why_it_matters": str(m.get("why_it_matters", ""))}
        for m in (raw.get("missing_information") or []) if isinstance(m, dict) and m.get("item")
    ]
    result["ambiguities"] = [str(a) for a in (raw.get("ambiguities") or []) if a]
    conf = raw.get("section_confidence") or {}
    result["section_confidence"] = {
        s: conf.get(s, "low") if conf.get(s) in ("high", "medium", "low") else "low" for s in SECTIONS
    }
    result["one_line_summary"] = str(raw.get("one_line_summary", "")).strip()
    return result


# --------------------------------------------- 7. grounding + confidence check
def _norm(s):
    return re.sub(r"[^a-z0-9/%.]+", " ", s.lower()).strip()


def is_grounded(evidence, notes):
    """True if the evidence quote really appears in the notes."""
    ev, src = _norm(evidence), _norm(notes)
    if len(ev) < 2:
        return False
    if ev in src:
        return True
    # allow small differences (e.g. dropped word): 80% of evidence words, in the notes
    ev_words = ev.split()
    src_words = set(src.split())
    if len(ev_words) >= 3:
        return sum(w in src_words for w in ev_words) / len(ev_words) >= 0.8
    return False


NUMBER = re.compile(r"\d+(?:\.\d+)?")


def numbers_ok(text, notes):
    """Every number in the AI's text must also appear in the notes (stops invented doses/values)."""
    src_numbers = set(NUMBER.findall(notes))
    return all(n in src_numbers for n in NUMBER.findall(text))


FILLER = set("""the and with for from that this was were has have had been not any also into
onto over under than then when while there their they them patient reports reported states
stated noted complains complaint presents presenting left right both side hours hour days
day weeks week months month years year minutes immediately urgently urgent currently current
known case history examination exam blood pressure heart rate pulse temperature oxygen
saturation respiratory degrees about around approximately mild moderate severe some most
review advised advise plan planned continue follow should will needs need test tests""".split())


def unsupported_words(text, notes, abbreviations):
    """Content words in the AI's text that cannot be traced to the notes."""
    note_words = set(re.findall(r"[a-z]+", notes.lower()))
    for a in abbreviations:
        note_words |= set(re.findall(r"[a-z]+", a["meaning"].lower()))
    missing = []
    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) < 4 or w in FILLER or w in note_words:
            continue
        if any(len(n) >= 3 and (w.startswith(n) or n.startswith(w)) for n in note_words):
            continue  # e.g. "trop" -> "troponin", "radiating" -> "radiates"
        missing.append(w)
    return missing


def ground_and_score(result, report):
    """Remove anything not supported by the notes, merge flags, and score confidence."""
    notes = report["redacted_notes"]
    blocked = []
    kept_total = 0
    for section in SECTIONS:
        kept = []
        for item in result[section]:
            if not is_grounded(item["evidence"], notes):
                blocked.append({"section": SECTION_LABELS[section], "text": item["text"],
                                "reason": "Quote not found in the original notes"})
            elif not numbers_ok(item["text"], notes):
                blocked.append({"section": SECTION_LABELS[section], "text": item["text"],
                                "reason": "Contains a number that is not in the notes"})
            else:
                extra = unsupported_words(item["text"], notes, report["abbreviations"])
                content = [w for w in re.findall(r"[a-z]+", item["text"].lower())
                           if len(w) >= 4 and w not in FILLER]
                if content and len(extra) / len(content) > 0.5:
                    blocked.append({"section": SECTION_LABELS[section], "text": item["text"],
                                    "reason": "Words not found in the notes: " + ", ".join(extra)})
                else:
                    kept.append(item)
        result[section] = kept
        kept_total += len(kept)

    # Merge missing-info flags: rules first, then extra ones the AI noticed
    flags = list(report["missing"])
    known = {f["item"].lower() for f in flags}
    for m in result["missing_information"]:
        if not any(word in m["item"].lower() for word in known) and m["item"].lower() not in known:
            flags.append({"item": m["item"], "severity": "medium",
                          "message": m["why_it_matters"] or "Mentioned as missing by the AI.",
                          "source": "AI"})
    result["flags"] = flags
    result["blocked"] = blocked

    # Confidence score (0-100) built from measurable signals
    total_returned = kept_total + len(blocked)
    grounding_rate = kept_total / total_returned if total_returned else 0
    level_points = {"high": 1.0, "medium": 0.6, "low": 0.2}
    ai_self = sum(level_points[result["section_confidence"][s]] for s in SECTIONS) / 4
    coverage = sum(1 for s in SECTIONS if result[s]) / 4
    high_missing = sum(1 for f in flags if f["severity"] == "high")

    score = 100 * (0.45 * grounding_rate + 0.25 * ai_self + 0.30 * coverage)
    score -= 8 * high_missing
    score -= 5 * len(blocked)
    score -= 5 * len(result["ambiguities"])
    score = max(0, min(100, round(score)))
    level = "High" if score >= 75 else "Medium" if score >= 50 else "Low"

    reasons = [f"{kept_total} of {total_returned} AI items were verified against the notes"
               if total_returned else "The AI returned no items"]
    if blocked:
        reasons.append(f"{len(blocked)} unsupported item(s) were blocked")
    reasons.append(f"{int(coverage * 4)} of 4 sections have content")
    if high_missing:
        reasons.append(f"{high_missing} critical item(s) missing")
    if result["ambiguities"]:
        reasons.append(f"{len(result['ambiguities'])} unclear point(s) found")

    result["confidence"] = {"score": score, "level": level, "reasons": reasons,
                            "grounding_rate": round(grounding_rate * 100)}
    return result


# ----------------------------------------------------------- full AI step
def structure_notes(data, report):
    """Run steps 4-7. Returns (result, meta). Raises AIError on failure."""
    prompt = build_prompt(data, report)
    text, usage, seconds = call_claude(SYSTEM_PROMPT, prompt)
    try:
        result = parse_output(text)
    except AIError:
        # one retry with a stricter reminder if the format was broken
        text, usage2, s2 = call_claude(SYSTEM_PROMPT, prompt + "\n\nReturn ONLY the JSON object.")
        usage = {k: usage[k] + usage2[k] for k in usage}
        seconds += s2
        result = parse_output(text)
    result = ground_and_score(result, report)
    meta = {"model": MODEL, "seconds": seconds, "cost_usd": estimate_cost(usage), **usage}
    return result, meta
