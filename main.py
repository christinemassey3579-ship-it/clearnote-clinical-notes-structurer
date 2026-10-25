"""
ClearNote - Clinical Notes Structurer
=====================================
The 10-step pipeline, in order:
   1. Structured input form   -> templates/index.html
   2. Input validation        -> validation.validate_form
   3. Logic layer (before AI) -> logic.run_logic_layer
   4. Prompt construction     -> ai.build_prompt
   5. LLM API call            -> ai.call_claude
   6. Output parsing          -> ai.parse_output
   7. Confidence / flag check -> ai.ground_and_score
   8. Database storage        -> db.save_note
   9. Structured UI output    -> templates/result.html
(Admin dashboard is added in the next stage.)
"""

import os
import secrets
from datetime import timedelta, timezone

from flask import Flask, redirect, render_template, request, url_for, abort

import ai
import db
import logic
import validation

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(16)
FEATURES = {"form", "logic", "ai", "db"}

db.init_db()

SECTIONS = [(k, ai.SECTION_LABELS[k]) for k in ai.SECTIONS]
STEPS = ["Input form", "Validation", "Logic layer", "Prompt built", "Claude API call",
         "Output parsed", "Grounding & confidence", "Saved to database", "Structured output", "Admin dashboard"]


@app.context_processor
def inject_globals():
    return {"features": FEATURES, "sections": SECTIONS}


@app.template_filter("ist")
def to_ist(dt):
    if not dt:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y, %H:%M")


def build_trace(route, ai_ok, saved):
    """Shows which pipeline steps ran for this note."""
    states = ["done", "done", "done"]
    if route == "stop":
        states += ["skip", "skip", "skip", "skip"]
    elif ai_ok:
        states += ["done", "done", "done", "done"]
    else:
        states += ["done", "fail", "skip", "skip"]
    states += ["done" if saved else "skip", "done", ""]   # dashboard not built yet
    return list(zip(STEPS, states))


def form_context(form=None, errors=None):
    return dict(form=form or {}, errors=errors, active="new",
                sex_options=validation.SEX_OPTIONS, encounter_types=validation.ENCOUNTER_TYPES,
                clinician_roles=validation.CLINICIAN_ROLES)


# ------------------------------------------------------------------ routes
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
            status = "structured"
        except ai.AIError as e:
            error, status = str(e), "ai_error"
        except Exception as e:  # never show a crash page to the clinician
            app.logger.exception("Unexpected AI failure")
            error, status = "Something unexpected went wrong while contacting the AI.", "ai_error"
    else:
        status = "stopped"

    # Step 8 - save every submission
    try:
        note_id = db.save_note(data, report, status, result, meta, error)
    except Exception:
        app.logger.exception("Database save failed")
        note_id = None
        error = (error + " " if error else "") + "Note could not be saved to the database."

    if note_id:
        return redirect(url_for("view_note", note_id=note_id))
    # Step 9 - show result even if the save failed
    return render_template("result.html", data=data, report=report, result=result, meta=meta,
                           error=error, note=None, trace=build_trace(report["route"], bool(result), False))


@app.route("/note/<int:note_id>")
def view_note(note_id):
    note = db.get_note(note_id)
    if not note:
        abort(404)
    meta = None
    if note["model"]:
        meta = {k: note[k] for k in ("model", "seconds", "input_tokens", "output_tokens", "cost_usd")}
    return render_template("result.html", data=note, report=note["logic_report"], result=note["structured"],
                           meta=meta, error=note["error_message"], note=note, active="history",
                           trace=build_trace(note["logic_report"]["route"], bool(note["structured"]), True))


@app.route("/note/<int:note_id>/review", methods=["POST"])
def review(note_id):
    db.set_review_status(note_id, request.form.get("value"))
    return redirect(url_for("view_note", note_id=note_id))


@app.route("/notes")
def history():
    notes = db.list_notes(patient_ref=request.args.get("patient_ref") or None,
                          level=request.args.get("level") or None)
    return render_template("history.html", notes=notes, active="history")


@app.errorhandler(404)
def not_found(_):
    return render_template("base.html", active=""), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
