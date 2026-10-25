"""
ClearNote - Clinical Notes Structurer
=====================================
STAGE 2 - Structured input form + input validation.
   1. Structured input form   -> templates/index.html
   2. Input validation        -> validation.validate_form
(Logic layer, AI, database and dashboard are added in later stages.)
"""

import os
import secrets

from flask import Flask, render_template, request

import validation

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
FEATURES = {"form"}


@app.context_processor
def inject_globals():
    return {"features": FEATURES}


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
    return render_template("result.html", data=data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
