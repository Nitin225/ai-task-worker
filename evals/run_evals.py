import datetime
import glob
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from agent.llm import MODEL, PROVIDER
from agent.loop import run_agent

ERP = "http://localhost:8000"
RESULTS_JSON = Path("evals/results.json")
RESULTS_MD = Path("evals/results.md")

# invoice tuple: (vendor, invoice_number, amount, due_date, status)
ACME = ("Acme Supplies Pvt Ltd", "INV-ACM-1087", 48500.0, "2026-05-03", "Pending")
GLOBEX = ("Globex Corp", "INV-GLX-231", 22000.0, "2026-05-10", "Pending")
INITECH = ("Initech Ltd", "INV-INI-077", 9200.0, "2026-05-12", "Pending")

# vendor tuple: (name, email, phone), the ERP's initial vendors
VENDORS = [
    ("Acme Supplies Pvt Ltd", "accounts@acme-supplies.example", "+91 90000 00001"),
    ("Globex Corp", "billing@globex.example", "+91 90000 00002"),
    ("Initech Ltd", "ap@initech.example", "+91 90000 00003"),
]
VENDORS_GLOBEX_EMAIL_CHANGED = [
    VENDORS[0],
    ("Globex Corp", "finance@globex.example", "+91 90000 00002"),
    VENDORS[2],
]

TASKS = [
    {"id": 1, "name": "Create: normal invoice entry",
     "task": "Find the latest invoice from Acme Supplies and enter it into the ERP.",
     "fail_first": 0, "seed": [], "answer": "", "inv": [ACME], "ven": VENDORS, "must_ask": False},
    {"id": 2, "name": "Create: first submit fails with 500",
     "task": "Find the latest invoice from Acme Supplies and enter it into the ERP.",
     "fail_first": 1, "seed": [], "answer": "", "inv": [ACME], "ven": VENDORS, "must_ask": False},
    {"id": 3, "name": "Ambiguous: two invoices tie for latest",
     "task": "Find the latest invoice from Globex Corp and enter it into the ERP.",
     "fail_first": 0, "seed": [], "answer": "Use INV-GLX-231", "inv": [GLOBEX], "ven": VENDORS, "must_ask": True},
    {"id": 4, "name": "Missing info: due date is 'Net 30'",
     "task": "Find the latest invoice from Initech Ltd and enter it into the ERP.",
     "fail_first": 0, "seed": [], "answer": "Yes, use 2026-05-12", "inv": [INITECH], "ven": VENDORS, "must_ask": True},
    {"id": 5, "name": "Idempotency: record already exists",
     "task": "Find the latest invoice from Acme Supplies and enter it into the ERP.",
     "fail_first": 0, "seed": [ACME], "answer": "", "inv": [ACME], "ven": VENDORS, "must_ask": False},
    {"id": 6, "name": "Update: change an invoice amount",
     "task": "Change the amount of invoice INV-ACM-1087 to 50000 in the ERP.",
     "fail_first": 0, "seed": [ACME], "answer": "",
     "inv": [("Acme Supplies Pvt Ltd", "INV-ACM-1087", 50000.0, "2026-05-03", "Pending")],
     "ven": VENDORS, "must_ask": False},
    {"id": 7, "name": "Update: change a vendor's email (other section)",
     "task": "Update the email of vendor Globex Corp to finance@globex.example.",
     "fail_first": 0, "seed": [], "answer": "", "inv": [], "ven": VENDORS_GLOBEX_EMAIL_CHANGED, "must_ask": False},
    {"id": 8, "name": "Update: mark an invoice as Paid",
     "task": "Mark invoice INV-GLX-231 as Paid in the ERP.",
     "fail_first": 0, "seed": [GLOBEX], "answer": "",
     "inv": [("Globex Corp", "INV-GLX-231", 22000.0, "2026-05-10", "Paid")], "ven": VENDORS, "must_ask": False},
    {"id": 9, "name": "Read-only question: total of Pending invoices",
     "task": "What is the total amount of all Pending invoices in the ERP? Do not change anything.",
     "fail_first": 0, "seed": [ACME, GLOBEX], "answer": "", "inv": [ACME, GLOBEX], "ven": VENDORS,
     "must_ask": False, "answer_contains": "70500"},
]


class QuotaExhausted(Exception):
    pass


def http(method, path):
    req = urllib.request.Request(ERP + path, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def erp_state():
    s = http("GET", "/api/state")
    inv = sorted((r["vendor"], r["invoice_number"], float(r["amount"]), r["due_date"], r["status"])
                 for r in s["invoices"])
    ven = sorted((r["name"], r["email"], r["phone"]) for r in s["vendors"])
    return inv, ven


def run_task(t, headless):
    http("POST", f"/admin/reset?fail_first={t['fail_first']}")
    for rec in t["seed"]:
        q = urllib.parse.urlencode({"vendor": rec[0], "invoice_number": rec[1], "amount": rec[2],
                                    "due_date": rec[3], "status": rec[4]})
        http("POST", f"/admin/seed?{q}")

    asks, approvals = [], []

    def ask_fn(question):
        asks.append(question)
        return t["answer"] or "Please decide using the task and the source documents."

    def approve_fn(summary):
        approvals.append(summary)
        return True

    before = set(glob.glob("traces/*_trace.json"))
    start = time.time()
    final, note = "", ""
    try:
        final = run_agent(t["task"], headless=headless, ask_fn=ask_fn, approve_fn=approve_fn)
    except RuntimeError as e:
        if "quota exhausted" in str(e):
            raise QuotaExhausted(str(e))
        note = f"ERROR: {e}"
    except Exception as e:
        note = f"ERROR: {type(e).__name__}: {e}"

    inv, ven = erp_state()
    exp_inv, exp_ven = sorted(t["inv"]), sorted(t["ven"])
    state_ok = inv == exp_inv and ven == exp_ven
    ask_ok = (len(asks) > 0) if t["must_ask"] else True
    answer_ok = True
    if "answer_contains" in t:
        answer_ok = t["answer_contains"] in str(final).replace(",", "").replace(".00", "")

    passed = state_ok and ask_ok and answer_ok and not note
    if not note and not state_ok:
        note = f"ERP state differs. invoices={inv} vendors={ven}"
    if not note and not ask_ok:
        note = "agent did not ask the user although the task was ambiguous or incomplete"
    if not note and not answer_ok:
        note = f"answer does not contain {t['answer_contains']}: {str(final)[:120]}"

    steps = None
    new_traces = sorted(set(glob.glob("traces/*_trace.json")) - before)
    if new_traces:
        steps = len(json.loads(Path(new_traces[-1]).read_text(encoding="utf-8"))["steps"])

    return {"id": t["id"], "name": t["name"], "passed": passed, "steps": steps,
            "questions": len(asks), "approvals": len(approvals),
            "seconds": round(time.time() - start), "note": note[:400],
            "final": str(final)[:200], "model": f"{PROVIDER}:{MODEL}",
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}


def load_results():
    if RESULTS_JSON.exists():
        return json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    return {}


def write_outputs(results):
    RESULTS_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["| # | Task | Result | Steps | Questions asked | Approvals | Time (s) | Model |",
             "|---|------|--------|-------|-----------------|-----------|----------|-------|"]
    for key in sorted(results, key=int):
        r = results[key]
        lines.append(f"| {r['id']} | {r['name']} | {'PASS' if r['passed'] else 'FAIL'} | {r['steps']} | "
                     f"{r['questions']} | {r['approvals']} | {r['seconds']} | {r['model']} |")
    passed = sum(1 for r in results.values() if r["passed"])
    lines.append(f"\n{passed}/{len(results)} tasks passed (1 run per task).")
    for key in sorted(results, key=int):
        r = results[key]
        if not r["passed"]:
            lines.append(f"- Task {r['id']} failed: {r['note']}")
    RESULTS_MD.write_text("\n".join(lines), encoding="utf-8")


def main():
    headless = "--headless" in sys.argv
    ids = {int(a) for a in sys.argv[1:] if a.isdigit()} or {t["id"] for t in TASKS}
    try:
        http("GET", "/api/state")
    except Exception:
        print("ERP is not running on localhost:8000. Start it first.")
        return

    results = load_results()
    for t in TASKS:
        if t["id"] not in ids:
            continue
        print(f"\n===== EVAL {t['id']}: {t['name']} =====")
        try:
            res = run_task(t, headless)
        except QuotaExhausted as e:
            print(f"\nSTOPPED: {e}\nRe-run the remaining tasks later, e.g. python -m evals.run_evals {t['id']}")
            break
        results[str(t["id"])] = res
        write_outputs(results)
        print(f"===== RESULT {t['id']}: {'PASS' if res['passed'] else 'FAIL'} "
              f"({res['steps']} steps, {res['questions']} questions) {res['note']}")
    write_outputs(results)
    print("\nSaved evals/results.md and evals/results.json")


if __name__ == "__main__":
    main()