# Discharge Copilot — bed board + AI discharge recommendation

A small hospital bed-board web app for a hackathon demo. It runs on your own machine and
uses only **made-up patient data**. The AI recommendation uses your OpenAI API key; everything
else works without it.

What you can do right now:

- See 10 beds on ward "4 West", all full, in your browser
- Send a new patient to the hospital with `curl` (they go to the **waiting list** because it's full)
- **Ask the AI who to discharge** when the hospital is full; get its reasoning plus a ready-to-paste curl command
- Discharge a patient; their bed goes to the most urgent waiting patient automatically
- See every step in the **event log**
- Press **Reset demo** to start over

Coming in later phases: a cross-check that finds pending results, med mismatches and
missing follow-ups in the discharge draft, and a plain-language summary for the patient.

---

## 1. One-time setup

You need **Python 3.10 or newer**. Check with:

```bash
python3 --version
```

Then copy-paste this block into a terminal:

```bash
cd ~/workspace/interview_question_oct_2026
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

What that did:

| Command | Meaning |
|---|---|
| `python3 -m venv .venv` | Make a private Python install in the folder `.venv/` so packages don't touch your system Python |
| `source .venv/bin/activate` | Use that private Python in this terminal. Your prompt now starts with `(.venv)` |
| `pip install -r requirements.txt` | Install FastAPI, the web server, the database library and the test tools |

## 2. Start the app

Every time you open a **new** terminal, activate the venv again, then start the server:

```bash
cd ~/workspace/interview_question_oct_2026
source .venv/bin/activate
uvicorn app.main:app --reload
```

You should see `Uvicorn running on http://127.0.0.1:8000`. Leave this terminal running.
(`--reload` restarts the server automatically when you edit Python files.)

## 3. Open the web app

Paste this into Chrome:

```
http://127.0.0.1:8000
```

You'll see 10 bed cards (Maria Gonzalez, James Whitfield, ...), occupancy **10 / 10**, a
"Hospital full" badge, an empty waiting list, and the event log. Click a patient's name to see
their full chart: results, medications, follow-ups, and the doctor's discharge draft.

The page refreshes by itself every few seconds when something changes, so you can keep it
open while you run `curl` in another terminal.

## 4. Send a new patient with curl

Open a **second terminal** (the first one is busy running the server):

```bash
cd ~/workspace/interview_question_oct_2026
curl -X POST http://127.0.0.1:8000/api/patients \
  -H "Content-Type: application/json" \
  -d @data/incoming/PT-0011.json
```

Expected answer:

```json
{"patient_id":"PT-0011","status":"incoming","bed_id":null,"message":"Hospital full. PT-0011 is on the waiting list (position 1). Ask the AI who to discharge: run the next_step command.","notes":[],"next_step":"curl -X POST http://127.0.0.1:8000/api/patients/PT-0011/recommendation"}
```

If you run this on a day after 2026-10-09, `notes` will say the file's `arrived_at` was ignored
(it's more than 24 hours old). That's expected; see [The `notes` field](#the-notes-field-in-the-post-answer).

`-d @file` means "send the contents of this file as the request body". The browser now shows
Grace Liu in **Waiting for a bed**, and the event log shows `arrived` and `bed_pressure`.

Because the hospital is full, the answer includes `next_step`: the command that asks the AI who to
discharge. See [step 5](#5-ask-the-ai-who-to-discharge).

There's a second ready-made patient (hip fracture, less urgent):

```bash
curl -X POST http://127.0.0.1:8000/api/patients \
  -H "Content-Type: application/json" \
  -d @data/incoming/PT-0012.json
```

### Make up your own patient

Only a few fields are required. **Change both `patient_id` and `mrn` each time.** `patient_id` must be
`PT-` + 4 digits. The same `mrn` can't be waiting or in a bed twice, because it's the same person:

```bash
curl -X POST http://127.0.0.1:8000/api/patients \
  -H "Content-Type: application/json" \
  -d @- <<'EOF'
{
  "patient_id": "PT-0013",
  "mrn": "MRN-100013",
  "demographics": {"name": "Alex Rivera", "age": 31, "sex": "X"},
  "encounter": {"encounter_id": "E-5013", "status": "incoming", "reason_for_admission": "Asthma exacerbation"},
  "triage": {"esi_level": 3, "chief_complaint": "Wheezing"},
  "clinical_status": {"stability": "improving", "on_iv_therapy": false, "oxygen": "room air"}
}
EOF
```

The full format, with a comment on every field, is in [data/schema_example.jsonc](data/schema_example.jsonc).

## 5. Ask the AI who to discharge

When the hospital is full and someone is waiting, copy the `next_step` command from the POST
answer, or type it:

```bash
curl -X POST http://127.0.0.1:8000/api/patients/PT-0011/recommendation
```

It takes about 5–10 seconds and prints plain text for the terminal (shortened here):

```
==============================================================================
DISCHARGE RECOMMENDATION  EV-0004   2026-10-09 19:42:58
Source: OpenAI gpt-5.4-mini
==============================================================================

Waiting:  PT-0011 Grace Liu · ESI 2 · Fever, confusion, dysuria · waiting 0.2 h

DECISION: DISCHARGE PT-0005 Linda Okafor (bed 4W-05)

Summary: PT-0005 is the safest straightforward discharge now, with no pending results ...

Why:
  - Expected discharge date has arrived (0 days).
  - Stable on room air and not on IV therapy.
  ...
Eligible patients, best to discharge first:
  1. PT-0005 Linda Okafor (4W-05) [rule score 80]: Most straightforward discharge ...
  2. PT-0002 James Whitfield (4W-02) [rule score 80]: ... warfarin and metoprolol changes plus low INR need monitoring.
  ...
Not eligible (safety rules, never discharged by this tool):
  - PT-0006 David Kowalski (4W-06): clinically improving, on IV therapy, needs oxygen (2L nasal cannula)
  - PT-0009 Fatima Haddad (4W-09): clinically unstable, on IV therapy, needs oxygen (BiPAP)

------------------------------------------------------------------------------
COMMANDS (copy and paste one)
------------------------------------------------------------------------------

# Accept: discharge PT-0005 Linda Okafor (bed 4W-05 then goes to PT-0011)
curl -X POST http://127.0.0.1:8000/api/patients/PT-0005/discharge \
  -H "Content-Type: application/json" \
  -d '{"reason": "EV-0004: PT-0005 is the safest ...", "disposition": "home", "clinician": "Dr. Samuel Reyes", "recommendation_id": "EV-0004"}'

# Don't accept: PT-0011 keeps waiting. Nothing to run. To ask again:
curl -X POST http://127.0.0.1:8000/api/patients/PT-0011/recommendation
```

**Nothing changes until you run a command.** Copy the whole `curl ...` block under
`# Accept` (all 3 lines) and paste it into the terminal. PT-0005 is discharged and PT-0011 gets bed 4W-05.
In the event log you'll see `ai_recommendation` → `discharged` → `bed_assigned`, all linked by `EV-0004`.

In the browser, the same thing is behind the **Ask AI** button on each waiting-list row.

### How it decides

| Layer | Who | What |
|---|---|---|
| 1. Safety rules | Python | Only patients who are **stable, off IV therapy and on room air** can be candidates. Everyone else is listed as "Not eligible" and is never sent to the AI as an option |
| 2. Rank and explain | OpenAI | Ranks the eligible patients and picks **discharge one** or **keep waiting**. It gets structured facts only (no names, no free-text notes) |
| 3. Check | Python | If the AI names a patient who isn't eligible, invents an ID or returns garbage, its answer is thrown away and the **rules-only** ranking is used instead |
| 4. Commands | Python | The server writes the curl commands from checked values. The AI never writes commands |

If OpenAI is unreachable (no key, no internet, out of credit, over 20 seconds), you still get an
answer: the header says `Source: RULES ONLY (...)` and gives the reason.

**Keep waiting** means nobody is safe or ready enough. The waiting patient stays on the list (there's
no command to remove them), and the commands offer "ask again later" plus an override to discharge
the top-ranked patient anyway.

Options:

| You want | Do this |
|---|---|
| JSON instead of text | `curl -X POST "http://127.0.0.1:8000/api/patients/PT-0011/recommendation?format=json"` |
| A different model | Start the server with `OPENAI_MODEL=gpt-5.5 uvicorn app.main:app --reload` (default `gpt-5.4-mini`) |
| The key from an env var instead of `apikey.txt` | `export OPENAI_API_KEY=sk-...` before starting the server |

## 6. Discharge a patient

**In the browser:** on a bed card click **Discharge…**, type a reason, then **Confirm discharge**.

**Or with curl:**

```bash
curl -X POST http://127.0.0.1:8000/api/patients/PT-0005/discharge \
  -H "Content-Type: application/json" \
  -d '{"reason": "Clinically ready, no open gaps", "disposition": "home", "clinician": "Dr. Samuel Reyes"}'
```

Expected answer, if PT-0011 was waiting:

```json
{"patient_id":"PT-0005","status":"discharged","bed_id":"4W-05","bed_given_to":"PT-0011","message":"PT-0005 discharged (home). Bed 4W-05 given to PT-0011."}
```

Who gets the free bed: the waiting patient with the **lowest ESI level** (1 = most urgent),
and if that's a tie, whoever arrived first. A reason is always required, because it goes into
the audit log.

## 7. Reset the demo

Click **Reset demo** (top right of the page), or:

```bash
curl -X POST http://127.0.0.1:8000/api/reset
```

This deletes the database and reloads the 10 patients from `data/patients/`. Do this before
every rehearsal.

## 8. Run the tests

With the venv active (you don't need the server running):

```bash
pytest
```

Expected: `80 passed`. Tests **never call OpenAI** (a fake AI is used), so they're free and work offline. The tests use their own temporary database, so they never touch your demo.

| File | What it checks |
|---|---|
| [tests/test_robustness.py](tests/test_robustness.py) | Weird input: `None`/`null`, empty strings and lists, divide by zero (0 beds), timezones, future dates. **Add a test here whenever you find a crash.** |
| [tests/test_api.py](tests/test_api.py) | Normal flow: seed → POST → waiting list → discharge → bed handover → reset, plus error codes |
| [tests/test_recommendation.py](tests/test_recommendation.py) | AI recommendation: unsafe patients never offered, bad or invented AI answers rejected, fallback when OpenAI is down, the printed curl command really works |
| [tests/test_edge_cases.py](tests/test_edge_cases.py) | Fixed bugs: 8 identical POSTs at once, same person under a new ID, `NaN`/`Infinity`, fake arrival times, discharge fields sent on admission, form error messages |

Run one file or one test:

```bash
pytest tests/test_robustness.py -v
pytest -k divide -v
```

---

## API cheat sheet

Interactive docs (try any endpoint from the browser): **http://127.0.0.1:8000/docs**

| Method | URL | Does |
|---|---|---|
| GET | `/api/beds` | All beds and who is in them |
| GET | `/api/patients?status=incoming` | Patient list; `status` is optional (`incoming`, `admitted`, `discharged`) |
| GET | `/api/patients/PT-0001` | One patient's full record (JSON) |
| POST | `/api/patients` | Add a patient (body = patient JSON) |
| POST | `/api/patients/PT-0011/recommendation` | AI recommendation for a waiting patient (text; `?format=json` for JSON). Only when the hospital is full |
| POST | `/api/patients/PT-0001/discharge` | Discharge (body = `reason`, `disposition`, `clinician`, optional `recommendation_id`) |
| GET | `/api/events?limit=20` | Event log, newest first |
| POST | `/api/reset` | Reset the demo |

Status codes you might see:

| Code | Meaning | Usual fix |
|---|---|---|
| 201 | Patient created | — |
| 404 | No such patient | Check the ID |
| 409 | Conflict: that `patient_id` already exists, that `mrn` is already waiting or in a bed, the patient isn't currently admitted, or (recommendation) the patient isn't waiting / a bed is free | Use a new `patient_id` **and** `mrn`, or press Reset demo |
| 422 | Your JSON doesn't match the schema; the response says which field | Fix that field. Unknown fields, `NaN` and `Infinity` are rejected on purpose |

### The `notes` field in the POST answer

Some fields belong to the app, not the caller. If you send them, they're ignored and listed in `notes`:

- `encounter.bed_id`, `admit_at`, `discharged_at`, `disposition`, `bed_released_for`: the app sets these.
- `triage.arrived_at`: kept only if it's within the last 24 hours and not in the future. Otherwise the
  server's clock is used, so a typo can't move someone up the waiting list.

## Known limitations: don't do these during the demo

This is a hackathon demo, not hospital software. The things below **will break it or give wrong
results**. They're known and deliberately left unfixed.

| Don't | What happens | Do this instead |
|---|---|---|
| Re-send a POST that already succeeded (e.g. you're not sure it went through) | `409 already exists`, even though the patient **was** added | Check the board or `GET /api/patients/PT-xxxx` first. Never re-send under a new ID |
| POST a patient who was already discharged (readmission) | `409` forever. One `patient_id` = one stay | Use a new `patient_id` (the same `mrn` is fine once they're discharged) |
| Save a broken or invalid JSON file in `data/patients/` or `data/beds.json` | The app **won't start**, or **Reset demo empties the board** (0 beds) | Check the file first: `python -m json.tool data/patients/PT-0003.json`, then `pytest`. Fix the file and press Reset again |
| POST a patient by mistake when the hospital is full | They stay on the waiting list; nothing can remove them, and they'll get the next free bed | Press **Reset demo** |
| Discharge the wrong patient | There's no undo | Press **Reset demo** |
| Edit `data/*.json` and expect the board to change | Nothing happens; the app reads `hospital.db` | Press **Reset demo** |
| Pull new code that changes the database tables, then start with your old `hospital.db` | Errors like `no such column` | Delete `hospital.db` (or press Reset demo) |
| Press Reset while someone else is POSTing | Their request fails with `500` | Reset only when nothing else is running |
| Run with `--host 0.0.0.0` or several `--workers` | Anyone on your network can reset or discharge (there's no login). With several workers, beds can be double-booked | Keep the default: one process on `127.0.0.1` |
| Send huge payloads (MBs, thousands of results) | Accepted, then every page gets slow | Keep patients realistic |
| Run an old recommendation's command after things changed | The recommendation isn't re-checked. If that patient was already discharged you get `409`; if their condition changed, the command still runs | Ask again right before acting |
| Ask the AI for two waiting patients and accept both | Both may recommend the same patient; the second command gets `409` | Act on one recommendation at a time |
| Treat the AI's answer as a clinical decision | It sees only a few structured facts (no notes, no exam) and can be wrong | It's decision support. A clinician decides |

Also good to know, though it won't crash anything:

- **The "clinician" field is free text,** so the audit log can't prove who did what. The recommendation's commands fill in the patient's attending doctor.
- **Every recommendation is an OpenAI call** (about 5–10 seconds, and it uses your API credit). Each one is saved in the event log.
- **The recommendation always suggests disposition `home`.** Edit the command if the patient needs `home_with_services` etc.
- **Reset demo erases the audit log too.**
- **Bed features are ignored:** a telemetry patient can get a non-telemetry bed, and freed beds are reassigned instantly, with no cleaning time.
- **Priority is ESI first, then arrival time,** with no aging. A level-3 patient can wait forever if level-2 patients keep arriving.
- **The patient page only shows that patient's events from the latest 500 events overall.**
- **Banner messages come from the URL:** `/?msg=anything` shows "anything". Script tags are escaped, so this is spoofing, not code execution.

## Project layout

```
app/
  main.py        routes: /api/* returns JSON, / and /patients/* return HTML pages
  services.py    bed logic: admit, discharge, waiting list, reset
  recommend.py   AI discharge recommendation: safety rules, AI ranking, answer checks, curl commands
  llm.py         the only file that talks to OpenAI (key loading, timeout, errors)
  schemas.py     patient JSON format (Pydantic) — validates every POST
  db.py          SQLite tables: beds, patients, events
  metrics.py     None-safe math helpers (length of stay, occupancy)
  templates/     HTML pages (Jinja2)
  static/        CSS
data/
  beds.json             the 10 beds
  patients/*.json       the 10 patients loaded on reset
  incoming/*.json       patients to send with curl
  schema_example.jsonc  the patient format, with comments
  event_example.jsonc   the event-log format, with comments
  expected_gaps.json    answer key for later phases (never sent to the AI)
tests/
hospital.db      created on first run (ignored by git)
```

How data flows: the JSON files are only the **starting point**. On first run (and on every
reset) they're copied into `hospital.db` (SQLite). After that, the app reads and writes the
database only, so your edits to `data/` show up after the next **Reset demo**.

## Troubleshooting

| Problem | Fix |
|---|---|
| `command not found: uvicorn` or `pytest` | Activate the venv: `source .venv/bin/activate` |
| `address already in use` | Something is already on port 8000. Stop it with Ctrl+C in its terminal, or run on another port: `uvicorn app.main:app --reload --port 8001` |
| `curl: (7) Failed to connect` | The server isn't running. Start it (step 2) |
| `curl: Failed to open data/incoming/...` | You're in the wrong folder. `cd ~/workspace/interview_question_oct_2026` first |
| The page looks out of date | Refresh the page, or press Reset demo |
| `{"detail":"Not Found"}` for a URL in this README | The server is running old code (started before an update, without `--reload`). Press Ctrl+C in the server terminal and run `uvicorn app.main:app --reload` again. Check with `curl -s http://127.0.0.1:8000/openapi.json` — the URL should be listed |

## A note on secrets

The OpenAI key goes in `apikey.txt` in the project folder (one line, nothing else), or in the
`OPENAI_API_KEY` environment variable. `apikey.txt` is listed in `.gitignore`, so it never gets
committed. The app never prints or logs the key. **Never paste the key into code or into this README.**
