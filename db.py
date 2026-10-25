"""
DATABASE  (pipeline step 8)
===========================
Saves every submission - including ones the logic layer stopped and ones where
the AI failed - so the admin dashboard shows the full picture.

Uses Replit's built-in PostgreSQL if DATABASE_URL is set (recommended - data
survives deployments). Otherwise uses a local SQLite file called notes.db.
"""

import json
import os
from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, Float, Integer, MetaData, String, Table, Text,
                        create_engine, func, select, update)

try:
    import psycopg2  # noqa: F401  (tells Replit to install the PostgreSQL driver)
except ImportError:
    psycopg2 = None

SQLITE_URL = "sqlite:///notes.db"
DATABASE_URL = os.environ.get("DATABASE_URL", SQLITE_URL)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("postgresql") and psycopg2 is None:
    DATABASE_URL = SQLITE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
metadata = MetaData()

notes = Table(
    "clinical_notes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True)),
    Column("patient_ref", String(20)),
    Column("age", String(3)),
    Column("sex", String(20)),
    Column("encounter_type", String(40)),
    Column("clinician_role", String(30)),
    Column("redacted_notes", Text),          # original notes with identifiers removed
    Column("status", String(20)),            # structured | stopped | ai_error
    Column("logic_report", Text),            # JSON
    Column("structured", Text),              # JSON (AI result after safety check)
    Column("confidence_score", Integer),
    Column("confidence_level", String(10)),
    Column("flag_count", Integer),
    Column("blocked_count", Integer),
    Column("red_flag_count", Integer),
    Column("error_message", Text),
    Column("model", String(40)),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("cost_usd", Float),
    Column("seconds", Float),
    Column("review_status", String(20), default="Needs review"),
)


def init_db():
    """Create the table. If PostgreSQL can't be reached, fall back to SQLite."""
    global engine
    try:
        metadata.create_all(engine)
    except Exception as e:
        print(f"Database warning: {e.__class__.__name__} - falling back to local SQLite file.")
        engine = create_engine(SQLITE_URL)
        metadata.create_all(engine)


def db_kind():
    return "PostgreSQL" if engine.url.get_backend_name().startswith("postgres") else "SQLite"


def save_note(data, report, status, result=None, meta=None, error=None):
    result, meta = result or {}, meta or {}
    conf = result.get("confidence", {})
    row = {
        "created_at": datetime.now(timezone.utc),
        "patient_ref": data["patient_ref"], "age": data["age"], "sex": data["sex"],
        "encounter_type": data["encounter_type"], "clinician_role": data["clinician_role"],
        "redacted_notes": report["redacted_notes"],
        "status": status,
        "logic_report": json.dumps(report),
        "structured": json.dumps(result) if result else None,
        "confidence_score": conf.get("score"),
        "confidence_level": conf.get("level"),
        "flag_count": len(result.get("flags", report["missing"])),
        "blocked_count": len(result.get("blocked", [])),
        "red_flag_count": len(report["red_flags"]),
        "error_message": error,
        "model": meta.get("model"),
        "input_tokens": meta.get("input_tokens"), "output_tokens": meta.get("output_tokens"),
        "cost_usd": meta.get("cost_usd"), "seconds": meta.get("seconds"),
        "review_status": "Needs review",
    }
    with engine.begin() as conn:
        res = conn.execute(notes.insert().values(**row))
        return res.inserted_primary_key[0]


def _to_dict(row):
    d = dict(row._mapping)
    d["logic_report"] = json.loads(d["logic_report"]) if d["logic_report"] else None
    d["structured"] = json.loads(d["structured"]) if d["structured"] else None
    return d


def get_note(note_id):
    with engine.connect() as conn:
        row = conn.execute(select(notes).where(notes.c.id == note_id)).first()
        return _to_dict(row) if row else None


def list_notes(limit=50, patient_ref=None, status=None, level=None):
    q = select(notes).order_by(notes.c.id.desc()).limit(limit)
    if patient_ref:
        q = q.where(notes.c.patient_ref.ilike(f"%{patient_ref}%"))
    if status:
        q = q.where(notes.c.status == status)
    if level:
        q = q.where(notes.c.confidence_level == level)
    with engine.connect() as conn:
        return [_to_dict(r) for r in conn.execute(q)]


def set_review_status(note_id, value):
    if value not in ("Needs review", "Reviewed"):
        return
    with engine.begin() as conn:
        conn.execute(update(notes).where(notes.c.id == note_id).values(review_status=value))


def dashboard_stats():
    """Numbers for the admin dashboard."""
    with engine.connect() as conn:
        rows = [_to_dict(r) for r in conn.execute(select(notes).order_by(notes.c.id))]
        avg = lambda col: conn.execute(select(func.avg(col)).where(col.isnot(None))).scalar()
        avg_conf, avg_secs = avg(notes.c.confidence_score), avg(notes.c.seconds)
        total_cost = conn.execute(select(func.sum(notes.c.cost_usd))).scalar() or 0

    total = len(rows)
    by_status = {s: sum(1 for r in rows if r["status"] == s) for s in ("structured", "stopped", "ai_error")}
    by_level = {l: sum(1 for r in rows if r["confidence_level"] == l) for l in ("High", "Medium", "Low")}

    missing_counts = {}
    for r in rows:
        flags = (r["structured"] or {}).get("flags") or (r["logic_report"] or {}).get("missing") or []
        for f in flags:
            missing_counts[f["item"]] = missing_counts.get(f["item"], 0) + 1
    top_missing = sorted(missing_counts.items(), key=lambda x: -x[1])[:6]

    per_day = {}
    for r in rows:
        day = r["created_at"].strftime("%d %b") if r["created_at"] else "?"
        per_day[day] = per_day.get(day, 0) + 1

    return {
        "total": total,
        "by_status": by_status,
        "by_level": by_level,
        "avg_confidence": round(avg_conf) if avg_conf is not None else None,
        "avg_seconds": round(avg_secs, 1) if avg_secs is not None else None,
        "total_cost": round(total_cost, 4),
        "blocked_total": sum(r["blocked_count"] or 0 for r in rows),
        "red_flag_notes": sum(1 for r in rows if (r["red_flag_count"] or 0) > 0),
        "needs_review": sum(1 for r in rows if r["review_status"] == "Needs review" and r["status"] == "structured"),
        "top_missing": top_missing,
        "per_day": list(per_day.items())[-14:],
    }
