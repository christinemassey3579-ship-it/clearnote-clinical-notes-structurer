# ClearNote — Clinical Notes Structurer

**Project:** From Workflow Pain to a Working AI Product · Track 5 — Healthcare
**Built with:** Python (Flask) · Claude API · SQLite / Replit PostgreSQL · Replit · GitHub

> ClearNote turns messy clinical shorthand into a clean, four-part note — **Symptoms, History, Observations, Next steps** — flags critical information that is missing, shows how confident it is, and **refuses to add anything the clinician didn't write.**

## Why this product exists

After a busy consultation, a clinician's notes look like this:

```
54M c/o chest pain radiating to L arm x 2 hrs, sweating ++. h/o HTN, DM on metformin.
O/E BP 186/110, HR 112, SpO2 94%. chest clear. plan ECG stat, trop, refer cardiology.
```

They are fast to write but hard to reuse: the next doctor, a referral letter or an insurance claim needs them structured and complete. Rewriting them by hand takes minutes per patient that nobody has. ClearNote does the re-organising in seconds — and makes gaps (like a missing allergy status) impossible to miss.

**JTBD:** *When I write clinical notes, I want them structured clearly, so they are useful for future care and communication.*

## How it works — the 10-step pipeline

| # | Step | Where in the code | Uses AI? |
|---|------|-------------------|----------|
| 1 | Structured input form | `templates/index.html` | No |
| 2 | Input validation | `validation.py` | No |
| 3 | **Logic layer** — redact names/phones/IDs, check vitals against safe ranges, detect red-flag words, detect which sections exist, check for missing critical info, decide whether the AI is needed at all | `logic.py` | No |
| 4 | Prompt construction (notes + logic findings + strict "do not invent" rules) | `ai.py → build_prompt` | — |
| 5 | Claude API call (temperature 0, timeout, retry) | `ai.py → call_claude` | **Yes** |
| 6 | Output parsing (JSON → clean data, format retry) | `ai.py → parse_output` | No |
| 7 | **Grounding & confidence check** — every AI item must quote the notes word-for-word; invented items, numbers or words are blocked; confidence score calculated | `ai.py → ground_and_score` | No |
| 8 | Database storage (every submission, incl. stopped + failed) | `db.py` | No |
| 9 | Structured UI output (cards, evidence quotes, flags — no raw JSON) | `templates/result.html` | No |
| 10 | Admin dashboard (quality, safety, cost) | `/admin` | No |

### The critical safety rule: no invented medical details
1. The prompt forbids adding anything not in the notes and requires an exact **evidence quote** for every item.
2. After the AI replies, the app checks every quote against the original notes. If the quote isn't there, the item is **blocked**.
3. Any number (dose, value, duration) not in the notes → **blocked**.
4. If most of an item's meaningful words can't be traced to the notes (e.g. an invented drug or diagnosis) → **blocked**.
5. Blocked items are shown struck-through to the clinician and counted on the admin dashboard.

## Build progress (one commit per stage)

- [x] Stage 1 — Product brief (`docs/Product_Brief.pdf`)
- [x] Stage 2 — Structured input form + validation
- [x] Stage 3 — Logic layer before AI
- [x] Stage 4 — Claude AI integration + grounding check + confidence
- [ ] Stage 5 — Database storage + saved notes
- [ ] Stage 6 — Admin dashboard

## Run it on Replit

1. Import this GitHub repository into Replit (**Create Repl → Import from GitHub**).
2. Open **Secrets** (padlock icon) and add:
   - `ANTHROPIC_API_KEY` — your Claude API key
   - `ADMIN_PASSWORD` — password for the admin dashboard (defaults to `admin123` if not set)
   - *(optional)* `CLAUDE_MODEL` — defaults to `claude-sonnet-5-5`; `claude-haiku-5-5` is cheaper
3. *(optional, recommended for the public link)* Add Replit's **PostgreSQL database** so saved notes survive deployments. Without it, notes are stored in a local `notes.db` file.
4. In the Shell run `pip install -r requirements.txt` once, then press **Run**.

## Disclaimer
ClearNote is a documentation aid built for a course project. It does not diagnose, recommend treatment, or replace clinical judgement. Use patient codes only — never real names or identifiers. All sample notes in this project are fictional.
