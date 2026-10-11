import datetime
import json
import re
from pathlib import Path

from agent import tools as T
from agent.llm import fn, generate
from agent.preflight import preflight
from agent.verifier import verify, fetch_state

SYSTEM_PROMPT = """You are an autonomous AI worker that completes tasks using a computer.
You have a browser, read-only files under data/ (source documents such as invoices are in data/invoices/), and the ability to ask the user.
The company ERP is at http://localhost:8000 (login: admin / admin123). It has Invoices and Vendors sections; navigate with the links shown in snapshots.

Rules:
- Decide the next action yourself from the task and the latest observation. Do not wait to be told steps.
- Browser pages are shown as a numbered element list plus page text. Use those numbers for click/type.
- Element numbers change after every page change. Use only numbers from the latest snapshot.
- To fill a form, use browser_fill with the fields you need in one call (fields_json like {"vendor": "...", "amount": "..."}) instead of many browser_type calls. After login or a failed submit, re-fill all required fields with a single browser_fill.
- To change an existing record, find its row, open its Edit link, and change only what the task says. Leave other fields as they are.
- If the task is a question, find the answer in the ERP or files, change nothing, and call finish with the answer.
- If an action returns ERROR, read it, then try a reasonable alternative or retry. Do not repeat the same failing action blindly.
- After a failed submit the form is usually reset: re-enter the fields, using only values from the task, the source documents, or the user. Never invent values.
- If a WARNING says a click did nothing, find out why (empty fields, errors) instead of clicking again.
- Only open URLs that you saw as links in a snapshot or that the task gave you. Never guess URLs.
- If the ERP says a record already exists, do not retry or create it again: check the list page, and if it matches what the task wants, call finish and say it already existed.
- If the task needs a choice among several candidates (e.g. 'latest') and they tie or are unclear, or a needed value is missing or vague (e.g. 'Net 30'), use ask_user BEFORE changing anything. Never guess.
- Clicks on submit/save/delete-type buttons are gated by the system (it checks your values and asks the user for approval). If a click is BLOCKED or DENIED, resolve it with ask_user, do not work around it.
- Call one tool at a time. Always respond with a tool call, never with plain text.
- Use ask_user only for clarification you truly need. Never ask whether the user wants anything else.
- When the work is done, call the finish tool. An independent verifier will check the real ERP state; if it fails, fix the problem and call finish again.
"""

TOOLS = [
    fn("list_files", "List files in a folder under data/.", {"path": "string"}, ["path"]),
    fn("read_file", "Read a text file under data/.", {"path": "string"}, ["path"]),
    fn("browser_open", "Open a URL in the browser. Returns page snapshot.", {"url": "string"}, ["url"]),
    fn("browser_click", "Click element by number from the latest snapshot. Returns new snapshot.", {"idx": "integer"}, ["idx"]),
    fn("browser_type", "Type text into an input by number from the latest snapshot.", {"idx": "integer", "text": "string"}, ["idx", "text"]),
    fn("browser_fill", "Fill several form inputs at once. fields_json is a JSON object mapping each input's name (as shown in the snapshot, e.g. vendor) to the value.", {"fields_json": "string"}, ["fields_json"]),
    fn("browser_snapshot", "Get a fresh snapshot of the current page.", {}, []),
    fn("ask_user", "Ask the user a question (clarification) and get their reply.", {"question": "string"}, ["question"]),
    fn("finish", "Finish the task with a concise summary of what was done (or the answer, for a question).", {"summary": "string"}, ["summary"]),
]

MAX_OBS_CHARS = 4000
RISKY = re.compile(r"submit|save|delete|remove|pay|confirm|approve|send", re.I)


def cli_ask(question):
    return input(f"\n[AGENT ASKS] {question}\n> ")


def cli_approve(summary):
    answer = input(f"\n[APPROVAL REQUIRED] {summary}\nApprove? (yes/no) > ")
    return answer.strip().lower() in ("y", "yes")


def trim_old_snapshots(messages):
    """Keep only the latest page snapshot in full; older ones are replaced by a stub."""
    idxs = [i for i, m in enumerate(messages)
            if m.get("role") == "tool" and "ELEMENTS:" in str(m.get("content", ""))]
    for i in idxs[:-1]:
        messages[i]["content"] = "[older page snapshot omitted]"


def _fields(a):
    v = a["fields_json"]
    return v if isinstance(v, dict) else json.loads(v)


def run_agent(task, max_steps=30, headless=False, ask_fn=cli_ask, approve_fn=cli_approve):
    browser = T.Browser(headless=headless)
    run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("traces")
    out_dir.mkdir(exist_ok=True)

    trace = []            # every action, approval, question and verdict of this run
    evidence = []         # (path, content) of files actually read: real tool output
    clarifications = []   # (question, answer) pairs from ask_user
    checked = set()       # field sets that already passed pre-flight

    try:
        before_state = fetch_state()   # ERP data before the agent touches anything
    except Exception:
        before_state = {}

    def end_run(result):
        """Save evidence (final screenshot + full trace) and return the result."""
        shot = None
        try:
            shot = browser.screenshot(str(out_dir / f"{run_id}_final.png"))
        except Exception:
            pass
        (out_dir / f"{run_id}_trace.json").write_text(
            json.dumps({"task": task, "result": str(result), "screenshot": shot,
                        "steps": trace}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"     [evidence saved: traces/{run_id}_trace.json and {run_id}_final.png]")
        return result

    def ask(args):
        answer = ask_fn(args["question"])
        clarifications.append((args["question"], answer))
        trace.append({"event": "ask_user", "question": args["question"], "answer": answer})
        return answer

    def approve(summary):
        ok = approve_fn(summary)
        trace.append({"event": "approval", "action": summary, "approved": ok})
        return ok

    def guarded_click(args):
        idx = int(args["idx"])
        info = browser.describe(idx)
        if info and info["tag"] == "button" and RISKY.search(info["text"]) and not info["has_password"]:
            if info["empty_required"]:
                return ("ERROR: cannot submit yet, required fields are empty: "
                        f"{', '.join(info['empty_required'])}. Fill them first "
                        "(the form may have been reset after an error).")

            key = json.dumps(info["fields"], sort_keys=True)
            if key not in checked:
                try:
                    current = fetch_state()
                except Exception:
                    current = {}
                ok, question = preflight(task, evidence, clarifications, info["fields"], current)
                if not ok:
                    print(f"     PREFLIGHT BLOCKED: {question}")
                    trace.append({"event": "preflight_blocked", "question": question})
                    return ("BLOCKED before submit: the values are ambiguous or not grounded in the "
                            f"task or source documents. Use ask_user to resolve this with the user: {question}")
                checked.add(key)

            fields = ", ".join(f"{k}={v}" for k, v in info["fields"])
            if not approve(f"Click '{info['text']}' with: {fields}"):
                return "DENIED by user: the action was NOT performed. Do not retry; use ask_user to ask what to change."
        return browser.click(idx)

    handlers = {
        "list_files": lambda a: T.list_files(a["path"]),
        "read_file": lambda a: T.read_file(a["path"]),
        "browser_open": lambda a: browser.open(a["url"]),
        "browser_click": guarded_click,
        "browser_type": lambda a: browser.type(int(a["idx"]), a["text"]),
        "browser_fill": lambda a: browser.fill(_fields(a)),
        "browser_snapshot": lambda a: browser.snapshot(),
        "ask_user": ask,
    }

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    recent = []
    verify_fails = 0

    try:
        for step in range(1, max_steps + 1):
            trim_old_snapshots(messages)
            resp = generate(messages, TOOLS)
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))

            if msg.content:
                print(f"[{step}] LLM says: {msg.content.strip()[:200]}")

            if not msg.tool_calls:
                messages.append({"role": "user", "content":
                                 "Respond with a tool call only. If the work is done, call the finish tool with your summary."})
                continue

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                print(f"[{step}] TOOL {name} {args}")

                if name == "finish":
                    claim = args.get("summary", "")
                    verdict, reason = verify(task, claim, evidence, clarifications, before_state)
                    print(f"     VERIFIER: {verdict} - {reason}")
                    trace.append({"step": step, "tool": "finish", "claim": claim,
                                  "verifier_verdict": verdict, "verifier_reason": reason})
                    if verdict == "PASS":
                        return end_run(f"{claim}\n[VERIFIED by independent check] {reason}")
                    verify_fails += 1
                    if verify_fails >= 2:
                        return end_run(f"FAILED VERIFICATION: {reason}\nAgent claim was: {claim}")
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": (
                        f"VERIFICATION FAILED: {reason}. The task is NOT complete. "
                        "Check the actual ERP state in the browser, fix the problem, then call finish again.")})
                    continue

                try:
                    obs = str(handlers[name](args))
                except KeyError as e:
                    obs = f"ERROR: unknown tool or missing argument: {e}"
                except Exception as e:
                    obs = f"ERROR: {type(e).__name__}: {str(e)[:300]}"

                if name == "read_file" and not obs.startswith("ERROR"):
                    evidence.append((args.get("path", ""), obs))

                # loop detection: same action + same result seen again and again, even if interleaved
                recent.append((name, json.dumps(args, sort_keys=True), obs[:200]))
                repeats = recent[-12:].count(recent[-1])
                if repeats >= 6:
                    return end_run("STOPPED: stuck repeating the same action with the same result")
                if repeats >= 3:
                    obs += (f"\nSYSTEM WARNING: you repeated this exact action {repeats} times "
                            "with the same result. Change your approach: re-check which fields "
                            "are filled, or use ask_user.")

                print(f"     -> {obs[:300]}")
                trace.append({"step": step, "tool": name, "args": args, "observation": obs[:400]})
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": obs[:MAX_OBS_CHARS]})

        return end_run("STOPPED: max steps reached without finish")
    finally:
        browser.close()