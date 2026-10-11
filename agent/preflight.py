import json
import re

from agent.llm import generate, AUX_MODEL

PREFLIGHT_PROMPT = """You are a pre-submission checker for an AI worker about to submit a form in a business app.
You get: the task, the raw source documents it read, the user's clarification answers, the ERP data currently stored, and the exact values about to be submitted.
Check two things:
1. GROUNDED: every submitted value must come from one of: the task text itself, a source document, a user clarification, or the stored ERP data (for example fields of an existing record that are left unchanged). Values that were guessed, invented, or derived from vague text (e.g. 'Net 30 (see contract)') are NOT grounded.
2. AMBIGUOUS CHOICE: if the task asks for one item chosen among several candidates (e.g. 'the latest invoice', or a record to change when several could match) and nothing clearly singles one out and no user clarification resolved it, the choice is ambiguous.
Reply with JSON only: {"ok": true} or {"ok": false, "question": "<short question to ask the user>"}"""


def preflight(task, evidence, clarifications, fields, state=None):
    docs = "\n\n".join(f"--- {p} ---\n{c}" for p, c in evidence) or "(no documents read)"
    answers = "\n".join(f"Q: {q}\nA: {a}" for q, a in clarifications) or "(none)"
    values = "\n".join(f"{k} = {v}" for k, v in fields)
    stored = json.dumps(state, indent=2) if state else "(not available)"
    user = (f"TASK:\n{task}\n\nSOURCE DOCUMENTS:\n{docs}\n\n"
            f"USER CLARIFICATIONS:\n{answers}\n\nSTORED ERP DATA:\n{stored}\n\n"
            f"VALUES ABOUT TO BE SUBMITTED:\n{values}")
    try:
        resp = generate([
            {"role": "system", "content": PREFLIGHT_PROMPT},
            {"role": "user", "content": user},
        ], model=AUX_MODEL)
        text = resp.choices[0].message.content or ""
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        if data.get("ok") is True:
            return True, ""
        return False, str(data.get("question", "Please confirm the values to submit."))[:300]
    except Exception:
        return True, ""   # fail open: the human approval gate still follows