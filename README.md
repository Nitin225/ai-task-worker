# Autonomous AI Task Worker

A prototype AI worker that takes a natural-language task, completes it in a real browser (plus files), recovers from failures, asks the user when it cannot safely proceed, and verifies the outcome independently. Built for the AI Engineering Intern problem statement, with Claude as a coding assistant; I designed the architecture, ran the tests and debugged the failures listed below.

The "internal system" is a mock ERP I built (FastAPI + SQLite) with two sections: **Invoices** (create, edit amount/due date/status) and **Vendors** (create, edit email/phone). No real credentials or third-party systems are used.

- **Demo video:** https://drive.google.com/file/d/1hOSp2SmOmABOiyBE6RhaNPDoQTBrWjRH/view?usp=sharing
- **Evidence from real runs:** `examples/` (action trace JSON + final screenshot)

## Setup (Windows, PowerShell; tested with Python 3.14)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
playwright install chromium
copy .env.example .env          # put your API key in .env
python make_data.py             # creates sample invoice files in data/invoices
```

## Run

Terminal 1, the mock ERP (login `admin` / `admin123`):

```powershell
uvicorn mock_erp.main:app --port 8000
```

Terminal 2, the agent (it asks in the terminal for clarification and for approval before any submit/save; each run saves a trace and screenshot to `traces/`):

```powershell
python run_agent.py "Find the latest invoice from Acme Supplies and enter it into the ERP."
python run_agent.py "Change the amount of invoice INV-ACM-1087 to 50000 in the ERP."
```

Reset the ERP, optionally making the first invoice submit fail with a 500:

```powershell
Invoke-RestMethod -Method Post "http://localhost:8000/admin/reset?fail_first=1"   # use 0 for no failure
```

Evals (ERP must be running; each task resets the ERP first, results go to `evals/results.md`):

```powershell
python -m evals.run_evals 6        # one task; omit the number to run all
```

## How it works

```
task -> AGENT LOOP: LLM picks a tool -> tool runs -> observation (or ERROR) goes back to the LLM
        Guards in code, not in the prompt:
          empty-required-field check | pre-flight check (grounded? ambiguous?)
          human approval before submit/save/delete | loop detection + step limit
     -> finish(summary)
     -> INDEPENDENT VERIFIER: compares ERP state before vs now against the task,
        the files the agent really read and the user's answers
     -> summary + evidence (trace JSON, final screenshot)
```

- `agent/loop.py`: tool-calling loop, approval gate, snapshot trimming, loop detection, evidence saving
- `agent/tools.py`: Playwright browser (numbered text snapshot, click, type, fill several fields) and a file reader limited to `data/`
- `agent/preflight.py`: before a submit, a fresh LLM call checks that every value comes from the task, a document, a user answer or stored ERP data, and that a "latest"-style choice is not ambiguous
- `agent/verifier.py`: a fresh LLM call that judges the real ERP state; a failure is sent back to the agent
- `agent/llm.py`: one OpenAI-compatible client (Groq or Gemini via `.env`), retries with backoff, clear stop when a daily quota is gone
- `mock_erp/`: the ERP, with duplicate rejection, optional injected failure and a read-only `/api/state`
- `evals/run_evals.py`: 9 tasks with scripted answers; pass/fail comes from code comparing the database to the expected state

## Key design decisions

- **No agent framework.** The loop is plain Python so I can explain every step; the LLM chooses tools and nothing is hard-coded per task.
- **Text snapshots, not screenshots.** Elements are listed as "[3] button Submit" and the model answers "click 3": cheaper and less error-prone than vision. Only the latest snapshot stays in the history.
- **A `browser_fill` tool** that fills all form fields in one call. This took a typical invoice entry from hitting the 25-30 step limit to 12 steps in the demo run.
- **Safety in code, not in the prompt.** In an early run the prompt told the model to ask before submitting and it did not, so approval, empty-field checks and the ambiguity pre-flight are enforced by the harness.
- **Independent verifier, and an eval judge that is code.** The agent's "done" is never trusted; the eval runner compares the database to expected records instead of asking an LLM.

## Problems found while building (and fixed)

- After a failed submit the form reset and the agent repeatedly submitted it nearly empty. Fix: empty-field block, a "blocked by form validation" message, loop detection.
- The agent invented a due date, and silently picked one of two invoices dated the same day. Fix: the pre-flight check now blocks and the agent asks the user.
- The verifier wrongly failed a correct entry because the user's answers were never passed to it (a wiring bug).
- The agent created a duplicate record, then guessed delete URLs. Fix: the ERP rejects duplicates (409), loop detection catches interleaved cycles, and URL guessing is forbidden in the prompt.
- LLM API failures (rate limits, dropped connections, a deprecated model, exhausted quota) are handled with retries and a clear stop.

## Testing status

- **Run with the current code (in the demo video; evidence in `examples/`):** invoice entry with an injected 500 (recovered, verifier PASS, 12 steps) and an invoice amount update (verifier PASS, 7 steps), both via `run_agent.py`.
- **Run with earlier code only:** the two-invoice tie and the "Net 30" due date (the agent asked the user in both), and one eval task, whose result is in `evals/results.md`.
- **Implemented, not run yet:** the other eval tasks (duplicate, vendor update, mark Paid, read-only question) and the "approval denied" path. Free-tier token quotas did not allow more runs.

## Limitations

- One mock app: the agent loop and tools are generic, but the system prompt names the ERP and the verifier/pre-flight prompts are written for business-record tasks.
- Pre-flight and verifier are LLM calls and can be wrong; the human approval is the last line of defence (evals auto-approve).
- Source documents are plain text, not PDFs or emails. Risky buttons are found by a text regex.
- Not handled: long lists beyond one page of text, dropdowns, file uploads, captchas, multiple tabs.
- The agent accepts a user's answer as given (I once typed the invoice date as the due date and it was accepted).
- Free-tier quotas make runs slow (retries are visible in the demo) and evals limited to one run per task. No scratchpad memory; facts stay in the message history.

## What I would build next

- Scratchpad or summarization for long tasks, and a screenshot fallback when DOM text is not enough.
- PDF and email inputs, and a second, different mock app to measure generalization.
- Sanity checks on user answers, a risk classifier instead of the regex, and repeated eval runs with a success rate.

## Assumptions

- Sandbox only: localhost ERP with a fixed demo login, no real data.
- "Latest invoice" means the most recent invoice date per vendor; a tie counts as ambiguous. For updates the invoice number identifies the record.
- A human is available for approval and clarification.

## Models and tools

- LLMs on Groq through OpenAI-compatible APIs: `qwen/qwen3.8-27b` for the agent loop, `openai/gpt-oss-20b` for the verifier and pre-flight (`openai/gpt-oss-120b` in earlier runs). Gemini is supported through the same client. Set in `.env` (see `.env.example`).
- Python, Playwright (Chromium), FastAPI, Uvicorn, Jinja2, SQLite, `openai` SDK, python-dotenv. AI coding assistant: Claude.