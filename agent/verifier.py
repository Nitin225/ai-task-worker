import json
import re
import urllib.request

from agent.llm import generate, AUX_MODEL

STATE_URL = "http://localhost:8000/api/state"

VERIFIER_PROMPT = """You are an independent verifier. You did NOT perform the task and must not trust the agent's claim.
You get: the task, raw source documents the agent read (real tool output), the user's clarification answers, the ERP state BEFORE the agent started, and the ERP state NOW.
Decide whether the task was actually accomplished.
Rules:
- Compare BEFORE and NOW. For tasks that change data (create, update, mark paid, add a vendor), NOW must show exactly the requested change, with values matching the task text, the source documents, or the user's clarification answers. A user answer counts as the source of truth when a document is vague (e.g. 'Net 30 (see contract)').
- Unrequested changes are a FAIL: duplicate records, changed records the task did not mention, deleted records. Records that were untouched are fine.
- If the task asked to enter something that already existed BEFORE with identical values, the task is accomplished as long as nothing was duplicated or altered.
- For questions (read-only tasks) nothing should change, and the agent's claim must contain the correct answer, computed from the ERP state NOW.
- If NOW does not show the outcome, FAIL even if the agent claims success.
Reply with JSON only: {"verdict": "PASS" or "FAIL", "reason": "<one short sentence citing the values>"}"""


def fetch_state():
    with urllib.request.urlopen(STATE_URL, timeout=5) as r:
        return json.loads(r.read().decode())


def verify(task, claim, evidence, clarifications=(), before_state=None):
    try:
        state = fetch_state()
    except Exception as e:
        return "FAIL", f"could not read ERP state: {e}"

    docs = "\n\n".join(f"--- {p} ---\n{c}" for p, c in evidence) or "(no source documents were read)"
    answers = "\n".join(f"Q: {q}\nA: {a}" for q, a in clarifications) or "(none)"
    before = json.dumps(before_state, indent=2) if before_state else "(not available)"
    user = (f"TASK:\n{task}\n\nAGENT CLAIM:\n{claim}\n\n"
            f"SOURCE DOCUMENTS:\n{docs}\n\nUSER CLARIFICATIONS:\n{answers}\n\n"
            f"ERP STATE BEFORE:\n{before}\n\nERP STATE NOW:\n{json.dumps(state, indent=2)}")
    resp = generate([
        {"role": "system", "content": VERIFIER_PROMPT},
        {"role": "user", "content": user},
    ], model=AUX_MODEL)
    text = resp.choices[0].message.content or ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0))
        verdict = "PASS" if str(data.get("verdict", "")).upper() == "PASS" else "FAIL"
        return verdict, str(data.get("reason", ""))[:300]
    except Exception:
        return "FAIL", f"verifier gave unparseable output: {text[:150]}"