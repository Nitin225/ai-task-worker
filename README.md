# Autonomous AI Task Worker

A prototype AI worker that takes a natural-language task, uses a real browser and files to complete it, handles failures, asks for clarification or approval when it cannot safely proceed, and verifies the outcome independently.

Built for the AI Engineering Intern problem statement. I used Claude as a coding assistant; I designed the architecture, ran the tests, and debugged the failure cases described below.

## What it does

Example task from the problem statement:

> "Find the latest invoice from Company X, extract the amount and due date, enter it into the internal system."

The "internal system" is a small mock ERP (FastAPI + SQLite) that I built so the agent has a real web app to operate. It has two sections:

- **Invoices:** create, edit (amount, due date, status Pending/Paid)
- **Vendors:** create, edit (email, phone)

So the agent can be evaluated on creating records, updating records, and answering questions. No real credentials or third-party systems are used.

- **Demo video:** https://drive.google.com/file/d/1lrnp4LuafWfLdqe1APRPJu2lIULJcvh1/view?usp=sharing
- Evidence from real runs (action trace JSON + final screenshot): `examples/`
- Eval runner and results file: `evals/`

## Setup (Windows, PowerShell)

Tested on Windows with Python 3.14.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
copy .env.example .env      # then put your API key in .env
python make_data.py         # creates sample invoice files in data/invoices
```

If PowerShell blocks the activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## Run

Terminal 1, the mock ERP (login `admin` / `admin123`):

```powershell
uvicorn mock_erp.main:app --port 8000
```

Terminal 2, the agent:

```powershell
python run_agent.py "Find the latest invoice from Acme Supplies and enter it into the ERP."
python run_agent.py "Change the amount of invoice INV-ACM-1087 to 50000 in the ERP."
python run_agent.py "Update the email of vendor Globex Corp to finance@globex.example."
```

The agent asks in the terminal when it needs clarification, and asks for your approval before any submit/save. Each run saves evidence to `traces/` (action trace JSON and a final screenshot).

Reset the ERP to its initial data, with or without an injected failure (the first invoice submit then returns a 500):

```powershell
Invoke-RestMethod -Method Post "http://localhost:8000/admin/reset?fail_first=0"   # normal
Invoke-RestMethod -Method Post "http://localhost:8000/admin/reset?fail_first=1"   # first submit fails
```

Evals (results accumulate in `evals/results.json` and `evals/results.md`; the ERP must be running; each task resets the ERP first):

```powershell
python -m evals.run_evals 6        # one task by number
python -m evals.run_evals          # all tasks
```

Evals use scripted answers for clarification questions and auto-approve, so they run without typing.

## Architecture

```
task (natural language)
      |
      v
+--------------------------------------------------+
| AGENT LOOP (agent/loop.py)                       |
|  LLM picks the next tool -> tool runs ->         |
|  observation (or ERROR) goes back to the LLM     |
|                                                  |
|  Harness guards (code, not prompts):             |
|   - empty-required-field check before submit     |
|   - pre-flight check (grounded? ambiguous?)      |
|   - human approval before submit/save/delete     |
|   - loop detection + step budget                 |
+--------------------------------------------------+
      | finish(summary)
      v
INDEPENDENT VERIFIER (agent/verifier.py)
  compares ERP state BEFORE vs NOW against the task,
  the files the agent really read and the user's answers
      |
      v
summary + evidence (traces/<run>_trace.json, final screenshot)
```

| Part | File | What it does |
|---|---|---|
| Tools | `agent/tools.py` | Playwright browser (numbered text snapshot of the page, click, type, fill several fields at once); file reader sandboxed to `data/` |
| Agent loop | `agent/loop.py` | Tool-calling loop, approval gate, trimming of old snapshots, loop detection, evidence saving |
| Pre-flight | `agent/preflight.py` | Fresh LLM call before a submit: is every value grounded in the task, a document, a user answer or stored ERP data? Is the choice ambiguous? |
| Verifier | `agent/verifier.py` | Fresh LLM call that sees the task, the files the agent really read, the user's answers, and the ERP state before and after |
| LLM layer | `agent/llm.py` | One OpenAI-compatible client; provider (Groq or Gemini) chosen in `.env`; retries with backoff; stops with a clear message when a daily quota is exhausted |
| Mock ERP | `mock_erp/` | Login, invoices (create/edit), vendors (create/edit), duplicate rejection, optional injected failure, read-only `/api/state` |
| Evals | `evals/run_evals.py` | 9 tasks with scripted user answers; pass/fail decided by code comparing the ERP database with the expected state |

## Important design decisions

**No agent framework.** The loop is plain Python, so I can explain every step. The LLM chooses tools; nothing in the task flow is hard-coded per task.

**Text snapshots instead of screenshots.** The browser tool returns interactive elements as a numbered list ("[3] button Submit") plus the page text. The model answers with "click 3". This is cheaper in tokens and less error-prone than vision. Only the latest snapshot is kept in the history; older ones are replaced by a stub.

**A higher-level `browser_fill` tool.** Filling a form with one call per field cost many LLM round trips. One call that fills all fields reduced a typical invoice-entry run from hitting the 25-30 step limit to 12 steps in the run recorded in the demo.

**Safety rules live in code, not in the prompt.** In an early run the system prompt told the model to ask before submitting, and it did not. So approval is enforced by the harness: a click on a button whose text looks like submit/save/delete is intercepted, and the user sees the exact field values. The same lesson led to the pre-flight check for ambiguity and invented values.

**Independent verifier with before/after state.** The agent's "done" is never trusted. After `finish`, a separate call compares the ERP state before the run with the state now. It covers creates, updates and read-only questions, and catches unrequested changes such as duplicates. A failure is sent back to the agent.

**Eval judge is code, not an LLM.** The eval runner compares the real database with the expected records, so it cross-checks the LLM verifier instead of depending on it.

**Tool errors are written for the agent.** Errors say what to do next (for example "only paths under data/ are allowed, e.g. data/invoices"), because the agent recovers from what the error tells it.

## Failures found while building (and fixed)

- After a failed submit the form was reset, and the agent re-entered only one field and submitted an empty form repeatedly. Fixes: the harness blocks submits with empty required fields; the browser tool reports "submit was blocked by form validation"; loop detection catches repeated actions.
- The agent invented a due date after getting stuck. Fix: the pre-flight check and the "never invent values" rule.
- With two invoices dated the same day, the agent silently picked one. Fix: the pre-flight check now blocks and the agent asks the user.
- The verifier wrongly failed a correct entry because the user's clarification answers were never passed to it (a wiring bug). Fixed.
- The agent created a duplicate of an existing record and then wasted steps guessing delete URLs. Fixes: the ERP rejects duplicates (409), loop detection also catches interleaved cycles, and the prompt forbids guessing URLs.
- The LLM API itself fails (rate limits, dropped connections, a deprecated model name, exhausted daily quota). The LLM layer retries transient errors with backoff and stops with a clear message when a daily quota is gone.

## Testing status

What has and has not been run, to be explicit:

**Run with the current code (shown in the demo video; evidence in `examples/`):**
- Create an invoice from a source file with an injected 500 on the first submit: the agent re-filled all fields and retried; verifier PASS; 12 steps.
- Update an invoice amount in the ERP (a workflow the agent had never run before): the agent found the row, opened Edit, changed only the amount after approval; verifier PASS (before/after: 48500.0 -> 50000.0); 7 steps.

These were run with `run_agent.py`, not through the eval runner.

**Run with an earlier version of the code (before the verifier and pre-flight were generalized):**
- Two invoices tie for "latest" (the agent asked the user), and a due date of "Net 30" (the agent asked the user instead of inventing a date).
- The eval runner was run for one task during development; `evals/results.md` is from that earlier run.

**Implemented but not run yet with the current code:**
- Eval tasks 3, 4, 5, 7, 8, 9 through the eval runner (ambiguity, missing info, duplicate, vendor update, mark Paid, read-only question), and the "approval denied" path. Free-tier token quotas did not allow more runs.

## Eval tasks

The eval runner defines nine tasks. Pass/fail is decided by code comparing the ERP database to the expected state, not by an LLM.

| # | Task | What it tests |
|---|------|---------------|
| 1 | Normal invoice entry | basic flow |
| 2 | First submit returns 500 | failure recovery |
| 3 | Two invoices tie for "latest" | must ask the user |
| 4 | Due date is "Net 30" in the source | must ask the user |
| 5 | Record already exists | no duplicate |
| 6 | Change an invoice amount | update workflow (edit page) |
| 7 | Change a vendor's email | different ERP section |
| 8 | Mark an invoice as Paid | update one field, others unchanged |
| 9 | "Total of Pending invoices?" | read-only question, nothing may change |

## Known limitations

- Generalization is only partly measured. The agent loop and tools are task-agnostic and the ERP has two sections, but it is still one mock app. The system prompt names the ERP URL and login, and the verifier and pre-flight prompts are written for business-record tasks. A different app would need those prompts adjusted.
- Pre-flight and verifier are LLM calls, so they can be wrong. The approval gate is the last line of defence, and it is a human decision. Evals auto-approve, so the "approval denied" path is not covered by them.
- Source documents are plain text files, not PDFs, images or emails.
- Risky buttons are detected by their text (regex), which is a heuristic.
- Update tasks rely on the agent finding the right row in a list from a text snapshot. Long lists (more than a page of text) are not handled. Dropdowns, file uploads, captchas, 2FA and multiple tabs are not supported.
- The agent accepts a user's answer as given. In one test I typed the invoice date instead of the due date and it was accepted. A sanity check ("due date equals invoice date?") is not implemented.
- Models run on free tiers with strict token quotas (per minute and per day). Runs are slow because of rate-limit retries (visible in the demo), evals are limited, and results can vary between runs.
- No separate scratchpad memory: facts stay in the message history, with old page snapshots trimmed. This works for short tasks; long tasks would need summarization.

## What I would build next

- A scratchpad / summarization layer for long tasks, and a screenshot-based fallback when the DOM text is not enough.
- PDF and email inputs, and a second, different mock app to measure generalization properly.
- Sanity checks on user answers, and repeated eval runs with a success-rate report.
- Replace the regex risk detection with an explicit action-risk classifier.
- Run the agent and the verifier on separate, stronger models when quota allows.

## Assumptions

- Sandbox only: the ERP runs on localhost with a fixed demo login. No real company data or credentials.
- "Latest invoice" means the most recent invoice date for that vendor. A tie is treated as ambiguous.
- For update tasks the invoice number identifies the record. If several records could match, the agent should ask.
- A human is available for approval and clarification.

## Models, frameworks and services

- LLMs through OpenAI-compatible APIs on Groq: `qwen/qwen3.8-27b` for the agent loop and `openai/gpt-oss-20b` for the verifier and pre-flight check (`openai/gpt-oss-120b` was used in earlier development runs). Google Gemini is supported through the same client. Configured in `.env` (see `.env.example`).
- Python, Playwright (Chromium), FastAPI, Uvicorn, Jinja2, SQLite, `openai` SDK, python-dotenv.
- AI coding assistance: Claude.