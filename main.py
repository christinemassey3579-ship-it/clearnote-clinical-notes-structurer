"""
ClearNote - Clinical Notes Structurer
=====================================
STAGE 3 - Logic layer runs BEFORE any AI.
   1. Structured input form   -> templates/index.html
   2. Input validation        -> validation.validate_form
   3. Logic layer (rules)     -> logic.run_logic_layer
(AI, database and dashboard are added in later stages.)
"""

import os
import secrets

from flask import Flask, render_template, request

import logic
import validation

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
FEATURES = {"form", "logic"}

SECTIONS = [("symptoms", "Symptoms"), ("history", "History"),
            ("observations", "Observations"), ("next_steps", "Next steps")]
STEPS = ["Input form", "Validation", "Logic layer", "Prompt built", "Claude API call",
         "Output parsed", "Grounding & confidence", "Saved to database", "Structured output", "Admin dashboard"]


@app.context_processor
def inject_globals():
    return {"features": FEATURES, "sections": SECTIONS}


def build_trace(route):
    """Steps 1-3 are built. Later steps show as not built yet."""
    states = ["done", "done", "done"] + [""] * 7
    if route == "stop":
        states[3:7] = ["skip"] * 4
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
    return render_template("result.html", data=data, report=report, result=None, meta=None,
                           error=None, note=None, trace=build_trace(report["route"]))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
