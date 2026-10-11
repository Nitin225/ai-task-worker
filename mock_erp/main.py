import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).parent
DB_PATH = BASE / "erp.db"
templates = Jinja2Templates(directory=str(BASE / "templates"))
app = FastAPI()

state = {"submit_count": 0, "fail_first": os.getenv("FAIL_FIRST_SUBMIT") == "1"}

SEED_VENDORS = [
    ("Acme Supplies Pvt Ltd", "accounts@acme-supplies.example", "+91 90000 00001"),
    ("Globex Corp", "billing@globex.example", "+91 90000 00002"),
    ("Initech Ltd", "ap@initech.example", "+91 90000 00003"),
]


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def seed_vendors(conn):
    conn.executemany("INSERT INTO vendors (name, email, phone) VALUES (?, ?, ?)", SEED_VENDORS)


def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor TEXT NOT NULL,
            invoice_number TEXT NOT NULL,
            amount REAL NOT NULL,
            due_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            phone TEXT NOT NULL
        )
    """)
    if conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] == 0:
        seed_vendors(conn)
    conn.commit()
    conn.close()


init_db()


def logged_in(request: Request) -> bool:
    return request.cookies.get("session") == "ok"


def error_page(request, message, code):
    return templates.TemplateResponse(request, "error.html", {"message": message}, status_code=code)


@app.get("/")
def root():
    return RedirectResponse("/invoices")


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == "admin" and password == "admin123":
        resp = RedirectResponse("/invoices", status_code=303)
        resp.set_cookie("session", "ok")
        return resp
    return templates.TemplateResponse(
        request, "login.html", {"error": "Invalid credentials"}, status_code=401
    )


# ---------- invoices ----------

@app.get("/invoices")
def invoices(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login")
    conn = db()
    rows = conn.execute("SELECT * FROM invoices ORDER BY id DESC").fetchall()
    conn.close()
    return templates.TemplateResponse(request, "invoices.html", {"rows": rows})


@app.get("/invoices/new")
def new_invoice_page(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "invoice_form.html", {})


@app.post("/invoices/new")
def create_invoice(
    request: Request,
    vendor: str = Form(...),
    invoice_number: str = Form(...),
    amount: float = Form(...),
    due_date: str = Form(...),
):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)

    state["submit_count"] += 1
    # Failure injection: the first submit fails when the flag is on
    if state["fail_first"] and state["submit_count"] == 1:
        return error_page(request, "500 Internal Server Error: could not save invoice. Please try again.", 500)

    conn = db()
    exists = conn.execute(
        "SELECT id FROM invoices WHERE vendor = ? AND invoice_number = ?",
        (vendor, invoice_number),
    ).fetchone()
    if exists:
        conn.close()
        return error_page(
            request,
            f"409 Conflict: invoice {invoice_number} for {vendor} already exists "
            f"(ID {exists['id']}). Duplicate was not saved.",
            409,
        )
    conn.execute(
        "INSERT INTO invoices (vendor, invoice_number, amount, due_date) VALUES (?, ?, ?, ?)",
        (vendor, invoice_number, amount, due_date),
    )
    conn.commit()
    conn.close()
    return RedirectResponse("/invoices", status_code=303)


@app.get("/invoices/{inv_id}/edit")
def edit_invoice_page(request: Request, inv_id: int):
    if not logged_in(request):
        return RedirectResponse("/login")
    conn = db()
    inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (inv_id,)).fetchone()
    conn.close()
    if not inv:
        return error_page(request, f"404 Not Found: no invoice with ID {inv_id}.", 404)
    return templates.TemplateResponse(request, "invoice_edit.html", {"inv": inv})


@app.post("/invoices/{inv_id}/edit")
def edit_invoice(
    request: Request,
    inv_id: int,
    amount: float = Form(...),
    due_date: str = Form(...),
    status: str = Form(...),
):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if status not in ("Pending", "Paid"):
        return error_page(request, "400 Bad Request: status must be exactly 'Pending' or 'Paid'.", 400)
    conn = db()
    cur = conn.execute(
        "UPDATE invoices SET amount = ?, due_date = ?, status = ? WHERE id = ?",
        (amount, due_date, status, inv_id),
    )
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return error_page(request, f"404 Not Found: no invoice with ID {inv_id}.", 404)
    return RedirectResponse("/invoices", status_code=303)


# ---------- vendors ----------

@app.get("/vendors")
def vendors(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login")
    conn = db()
    rows = conn.execute("SELECT * FROM vendors ORDER BY id").fetchall()
    conn.close()
    return templates.TemplateResponse(request, "vendors.html", {"rows": rows})


@app.get("/vendors/new")
def new_vendor_page(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request, "vendor_form.html",
        {"action": "/vendors/new", "v": {"name": "", "email": "", "phone": ""}, "editing": False},
    )


@app.post("/vendors/new")
def create_vendor(request: Request, name: str = Form(...), email: str = Form(...), phone: str = Form(...)):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    try:
        conn.execute("INSERT INTO vendors (name, email, phone) VALUES (?, ?, ?)", (name, email, phone))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return error_page(request, f"409 Conflict: vendor '{name}' already exists.", 409)
    conn.close()
    return RedirectResponse("/vendors", status_code=303)


@app.get("/vendors/{vid}/edit")
def edit_vendor_page(request: Request, vid: int):
    if not logged_in(request):
        return RedirectResponse("/login")
    conn = db()
    v = conn.execute("SELECT * FROM vendors WHERE id = ?", (vid,)).fetchone()
    conn.close()
    if not v:
        return error_page(request, f"404 Not Found: no vendor with ID {vid}.", 404)
    return templates.TemplateResponse(
        request, "vendor_form.html", {"action": f"/vendors/{vid}/edit", "v": v, "editing": True}
    )


@app.post("/vendors/{vid}/edit")
def edit_vendor(request: Request, vid: int, name: str = Form(...), email: str = Form(...), phone: str = Form(...)):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    conn = db()
    cur = conn.execute("UPDATE vendors SET email = ?, phone = ? WHERE id = ?", (email, phone, vid))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return error_page(request, f"404 Not Found: no vendor with ID {vid}.", 404)
    return RedirectResponse("/vendors", status_code=303)


# ---------- harness endpoints (evals and verifier) ----------

@app.get("/api/state")
def api_state():
    """Read-only ground truth for the verifier (harness use only)."""
    conn = db()
    inv = conn.execute(
        "SELECT id, vendor, invoice_number, amount, due_date, status FROM invoices ORDER BY id"
    ).fetchall()
    ven = conn.execute("SELECT id, name, email, phone FROM vendors ORDER BY id").fetchall()
    conn.close()
    return {"invoices": [dict(r) for r in inv], "vendors": [dict(r) for r in ven]}


@app.post("/admin/reset")
def reset(fail_first: int | None = None):
    """For evals: restore the initial data and reset the failure counter.
    fail_first=1 makes the first invoice submit fail, 0 disables it, omitted keeps the current setting."""
    conn = db()
    conn.execute("DELETE FROM invoices")
    conn.execute("DELETE FROM vendors")
    seed_vendors(conn)
    conn.commit()
    conn.close()
    state["submit_count"] = 0
    if fail_first is not None:
        state["fail_first"] = bool(fail_first)
    return {"status": "reset", "fail_first": state["fail_first"]}


@app.post("/admin/seed")
def seed(vendor: str, invoice_number: str, amount: float, due_date: str, status: str = "Pending"):
    """For evals: pre-insert an invoice."""
    conn = db()
    conn.execute(
        "INSERT INTO invoices (vendor, invoice_number, amount, due_date, status) VALUES (?, ?, ?, ?, ?)",
        (vendor, invoice_number, amount, due_date, status),
    )
    conn.commit()
    conn.close()
    return {"status": "seeded"}