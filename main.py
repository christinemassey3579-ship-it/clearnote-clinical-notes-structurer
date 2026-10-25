"""
ClearNote - Clinical Notes Structurer
=====================================
STAGE 4 - Claude AI integration.
   1. Structured input form   -> templates/index.html
   2. Input validation        -> validation.validate_form
   3. Logic layer (before AI) -> logic.run_logic_layer
   4. Prompt construction     -> ai.build_prompt
   5. LLM API call            -> ai.call_claude
   6. Output parsing          -> ai.parse_output
   7. Confidence / flag check -> ai.ground_and_score
   9. Structured UI output    -> templates/result.html
(Database and dashboard are added in later stages.)
"""

import os
import secrets

from flask import Flask, render_template, request

import ai
import logic
import validation

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
FEATURES = {"form", "logic", "ai"}

SECTIONS = [(k, ai.SECTION_LABELS[k]) for k in ai.SECTIONS]
STEPS = ["Input form", "Validation", "Logic layer", "Prompt built", "Claude API call",
         "Output parsed", "Grounding & confidence", "Saved to database", "Structured output", "Admin dashboard"]


@app.context_processor
def inject_globals():
    return {"features": FEATURES, "sections": SECTIONS}


def build_trace(route, ai_ok):
    states = ["done", "done", "done"]
    if route == "stop":
        states += ["skip"] * 4
    elif ai_ok:
        states += ["done"] * 4
    else:
        states += ["done", "fail", "skip", "skip"]
    states += ["", "done", ""]          # database + dashboard not built yet
    return list(zip(STEPS, states))


def form_context(form=None, errors=None):
    return dict(form=form or {}, errors=errors, active="new",
                sex_options=validation.SEX_OPTIONS, encounter_types=validation.ENCOUNTER_TYPES,
                clinician_roles=validation.CLINICIAN_ROLES)


@app.route("/")
def index():
    return render_template("index.html", **form_context())


@app.route("/submit", methods=["POST"])
def submit():
    # Step 2 - validation
    data, errors = validation.validate_form(request.form)
    if errors:
        return render_template("index.html", **form_context(data, errors)), 400

    # Step 3 - logic layer (rules, no AI)
    report = logic.run_logic_layer(data)
    result = meta = error = None

    # Steps 4-7 - only if the logic layer says the AI is worth calling
    if report["route"] == "ai":
        try:
            result, meta = ai.structure_notes(data, report)
        except ai.AIError as e:
            error = str(e)
        except Exception:
            app.logger.exception("Unexpected AI failure")
            error = "Something unexpected went wrong while contacting the AI."

    # Step 9 - structured output
    return render_template("result.html", data=data, report=report, result=result, meta=meta,
                           error=error, note=None, trace=build_trace(report["route"], bool(result)))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
