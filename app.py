"""Local classroom web app. Run with Python 3.10 or newer."""
import argparse
from contextlib import closing
from contextvars import ContextVar
from hashlib import pbkdf2_hmac, sha256
from html import escape
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
import re
import secrets
import sqlite3
from time import time
from urllib.parse import parse_qs, urlencode
import webbrowser
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "chits.sqlite3"
FORM_TOKEN = secrets.token_urlsafe(32)
csrf = ContextVar("csrf", default=FORM_TOKEN)
SESSION_AGE = 7 * 24 * 60 * 60


def connect():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize():
    with closing(connect()) as db, db:
        db.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        db.execute("BEGIN IMMEDIATE")
        columns = {column["name"] for column in db.execute("PRAGMA table_info(chits)")}
        if "owner_id" not in columns:
            db.execute("ALTER TABLE chits ADD COLUMN owner_id INTEGER REFERENCES users(id)")
        db.execute("CREATE INDEX IF NOT EXISTS chits_owner ON chits(owner_id)")


def money(value):
    return f"₹{value:,}"


def layout(title, content, message="", user=None):
    alert = f'<p class="alert" role="status">{escape(message)}</p>' if message else ""
    nav = ('<a href="/dashboard">Dashboard</a>' + form("/logout", "", "Sign out", css="inline subtle")
           if user else '<a href="/login">Sign in</a><a href="/register">Sign up</a>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)} | SmoothFinance</title><link rel="stylesheet" href="/style.css">
    </head><body><header><div class="shell nav"><a href="/" class="brand"><span class="brand-icon" aria-hidden="true">◈</span> SmoothFinance</a>
    <nav aria-label="Main navigation">{nav}<span class="demo">Demo mode</span></nav></div></header><main class="shell">{alert}{content}</main>
    <footer class="shell">SmoothFinance · Halal chit fund school project · Simulated funds only</footer></body></html>"""


def form(path, fields, label, action="", css=""):
    return f"""<form method="post" action="{path}" class="{css}">
    <input type="hidden" name="token" value="{csrf.get()}">
    <input type="hidden" name="action" value="{action}">{fields}
    <button type="submit">{label}</button></form>"""


def hidden(name, value):
    return f'<input type="hidden" name="{name}" value="{value}">'


def chit_fields(chit=None, locked=False):
    name = escape(chit["name"], quote=True) if chit else ""
    contribution = chit["contribution"] if chit else ""
    capacity = chit["capacity"] if chit else ""
    readonly = " readonly" if locked else ""
    return f"""<label>Chit name<input name="name" required maxlength="80" value="{name}"
    placeholder="e.g. Class Savings Circle"></label>
    <div class="fields"><label>Contribution per round (₹)<input type="number" name="contribution"
    min="1" max="10000000" required value="{contribution}"{readonly}></label>
    <label>Member slots<input type="number" name="capacity" min="2" max="60"
    required value="{capacity}"{readonly}></label></div>"""


def homepage(user):
    primary = ('<a class="hero-button" href="/dashboard">Open dashboard →</a>' if user
               else '<a class="hero-button" href="/register">Create an account →</a>'
                    '<a class="home-signin" href="/login">Sign in</a>')
    return f"""<section class="hero home-hero"><small>HALAL CHIT FUND · SCHOOL PROJECT</small>
    <h1>SmoothFinance</h1>
    <p>A rotating savings simulator where members contribute equally and take turns receiving the full pool.</p>
    <div class="home-actions">{primary}</div></section>
    <div class="grid home-grid">
    <section class="card"><h2>About the project</h2>
    <p>SmoothFinance is a Python and SQLite web application that demonstrates how a fixed-order savings circle works without interest, fees, or bidding.</p>
    <ol class="project-steps"><li>Create a chit and enroll members in payout order.</li>
    <li>Record each member’s contribution through a simulated UPI or card checkout.</li>
    <li>Record the full payout once everyone contributes, then repeat until every member has received it once.</li></ol>
    <p>All contributions and payouts use simulated funds.</p></section>
    <section class="card"><h2>Project team</h2>
    <ul class="team-list"><li>Shehzeen Fatima</li><li>Shaik Asefa</li>
    <li>Nabbehunnisa</li><li>Maryam Mohd</li></ul></section></div>"""


def dashboard(db, user):
    chits = db.execute("""
        SELECT c.*, (SELECT COUNT(*) FROM members WHERE chit_id = c.id) AS enrolled,
        (SELECT COUNT(*) FROM payouts WHERE chit_id = c.id) AS rounds
        FROM chits c WHERE c.owner_id = ? ORDER BY c.id DESC
    """, (user["id"],)).fetchall()
    cards = []
    for chit in chits:
        status = ("Completed" if chit["rounds"] == chit["capacity"] else
                  "Enrolling" if chit["enrolled"] < chit["capacity"] else "In progress")
        cards.append(f"""<article class="card item"><div><small>{status}</small>
        <h3><a href="/chits/{chit['id']}">{escape(chit['name'])}</a></h3>
        <p>{chit['enrolled']}/{chit['capacity']} members · {money(chit['contribution'])} per round</p>
        </div><a class="link-button" href="/chits/{chit['id']}">Open →</a></article>""")
    listing = "".join(cards) or '<div class="card"><p>No chits yet. Create one to get started.</p></div>'
    return f"""<div class="hero"><small>ROTATING SAVINGS</small><h1>Manage your savings circles</h1>
    <p class="account-email">Signed in as {escape(user["email"])}</p>
    <p>Set up a chit, track member contributions, and manage payouts in a fixed rotation with zero interest, fees, or bidding.</p>
    <a class="hero-button" href="#create">Create a chit <span aria-hidden="true">→</span></a></div>
    <div class="metrics" aria-label="Dashboard totals">
    <div><span>Total chits</span><strong>{len(chits)}</strong></div>
    <div><span>Enrolled members</span><strong>{sum(chit["enrolled"] for chit in chits)}</strong></div>
    <div><span>Payouts recorded</span><strong>{sum(chit["rounds"] for chit in chits)}</strong></div>
    </div>
    <div class="grid"><section><h2>Your chits <span class="count">{len(chits)}</span></h2>{listing}</section>
    <section id="create"><h2>Create a chit</h2><div class="card">{form("/dashboard", chit_fields(), "Create chit", "create")}</div></section></div>"""


def chit_state(db, chit):
    chit_id = chit["id"]
    members = db.execute("SELECT * FROM members WHERE chit_id = ? ORDER BY id", (chit_id,)).fetchall()
    history = db.execute("""
        SELECT p.round, m.name FROM payouts p JOIN members m ON m.id = p.member_id
        WHERE p.chit_id = ? ORDER BY p.round
    """, (chit_id,)).fetchall()
    round_number = len(history) + 1
    paid = {row["member_id"] for row in db.execute(
        "SELECT member_id FROM contributions WHERE chit_id = ? AND round = ?",
        (chit_id, round_number))}
    return members, history, round_number, paid


def detail(db, chit, confirm_delete=False):
    members, history, round_number, paid = chit_state(db, chit)
    capacity = chit["capacity"]
    path = f"/chits/{chit['id']}"
    amount = chit["contribution"]
    complete = round_number > capacity
    full = len(members) == capacity
    locked = bool(history or paid)
    pot = money(capacity * amount)
    rows = []
    for order, member in enumerate(members, start=1):
        member_id = member["id"]
        member_field = hidden("member", member_id)
        action = ""
        if full and not complete:
            is_paid = member_id in paid
            if is_paid:
                action += form(path, member_field + hidden("round", round_number),
                               "Undo payment", "undo", "inline subtle")
            else:
                action += f'<a class="link-button pay" href="{path}?checkout={member_id}">Pay {money(amount)}</a>'
        if not locked:
            action += form(path, member_field, "Remove", "remove", "inline subtle")
        status = "Complete" if complete else "Paid" if member_id in paid else "Due" if full else "Enrolled"
        rows.append(f"<tr><td>{order}</td><td>{escape(member['name'])}</td>"
                    f"<td>{status}</td><td>{action}</td></tr>")
    roster = ("<div class='table-wrap'><table><thead><tr><th>Turn</th><th>Member</th>"
              "<th>Status</th><th>Actions</th></tr></thead><tbody>" + "".join(rows) +
              "</tbody></table></div>") if rows else "<p>No members enrolled yet.</p>"
    add = ""
    if not full:
        add = form(path, '<label>Member name<input name="name" maxlength="80" required></label>',
                   "Add member", "add", "add")
    if complete:
        round_panel = "<h2>Chit completed</h2><p>All members have received their payout.</p>"
    elif not full:
        round_panel = f"<h2>Enrollment</h2><p>Add {capacity - len(members)} more member(s) to start round 1.</p>"
    else:
        recipient = escape(members[round_number - 1]["name"])
        round_panel = f"""<h2>Round {round_number} of {capacity}</h2>
        <p>Scheduled recipient <strong>{recipient}</strong> · Pool <strong>{pot}</strong></p>
        <progress value="{len(paid)}" max="{capacity}"></progress>
        <p>{len(paid)}/{capacity} contributions recorded · {money(len(paid) * amount)} collected</p>"""
        if len(paid) == capacity:
            round_panel += form(path, hidden("round", round_number), "Record payout", "payout")
        else:
            round_panel += "<p>The payout opens when every member has contributed.</p>"
    log = "".join(
        f"<li>Round {entry['round']} · <strong>{escape(entry['name'])}</strong> · {pot}</li>"
        for entry in history
    )
    payout_history = f"<ol>{log}</ol>" if log else "<p>No payouts recorded yet.</p>"
    deletion = f'<a class="danger-link" href="{path}?confirm=delete">Delete chit</a>'
    if confirm_delete:
        deletion = ("<p>Permanently delete this chit, its members, and all round records?</p>"
                    + form(path, "", "Yes, delete chit", "delete", "danger")
                    + f'<p><a href="{path}">Cancel</a></p>')
    return f"""<div class="hero compact"><a href="/dashboard">← All chits</a>
    <h1>{escape(chit['name'])}</h1><p>{money(amount)} per member per round · {pot} total pool</p></div>
    <div class="grid"><div><section class="card round">{round_panel}</section>
    <section class="card"><h2>Members <span class="count">{len(members)}/{capacity}</span></h2>
    <p>Payouts follow the member order shown here.</p>{roster}{add}</section></div>
    <aside><section class="card"><h2>Payout history</h2>{payout_history}</section>
    <section class="card"><h2>Edit chit</h2>
    {"<p>Contribution and slots are locked once payments are recorded.</p>" if locked else ""}
    {form(path, chit_fields(chit, locked), "Save changes", "edit")}</section>
    <section class="card">{deletion}</section></aside></div>"""


def checkout(db, chit, member_value):
    members, _, round_number, paid = chit_state(db, chit)
    member_id = bounded_int({"member": member_value}, "member", 1, 2**63 - 1)
    member = next((person for person in members if person["id"] == member_id), None)
    if (member is None or len(members) != chit["capacity"]
            or round_number > chit["capacity"] or member_id in paid):
        raise ValueError("Checkout is unavailable for this contribution.")

    path = f"/chits/{chit['id']}"
    fields = (hidden("member", member_id) + hidden("round", round_number)
              + hidden("amount", chit["contribution"]))
    fields += """<label>Demo payment method<select name="method" required>
    <option value="upi">Test UPI</option><option value="card">Test card</option></select></label>
    <label>Simulated outcome<select name="outcome" required>
    <option value="success">Approve payment</option>
    <option value="decline">Decline payment</option></select></label>"""
    return f"""<div class="checkout"><a href="{path}">← Back to chit</a>
    <section class="card"><span class="demo">Sandbox checkout</span>
    <h1>Pay contribution</h1>
    <p>Choose a test method and outcome. No money moves and no payment details are collected.</p>
    <dl class="summary"><div><dt>Chit</dt><dd>{escape(chit['name'])}</dd></div>
    <div><dt>Member</dt><dd>{escape(member['name'])}</dd></div>
    <div><dt>Round</dt><dd>{round_number} of {chit['capacity']}</dd></div>
    <div class="total"><dt>Total</dt><dd>{money(chit['contribution'])}</dd></div></dl>
    {form(path, fields, "Run test payment", "pay")}
    <p><a href="{path}">Cancel checkout</a></p></section></div>"""


def bounded_int(data, field, minimum, maximum):
    try:
        value = int(data.get(field, ""))
    except ValueError:
        raise ValueError(f"Enter a valid whole number for {field}.") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{field.capitalize()} must be between {minimum} and {maximum}.")
    return value


def read_name(data):
    name = data.get("name", "").strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("Enter a name between 1 and 80 characters.")
    return name


def chit_values(data):
    return (read_name(data), bounded_int(data, "contribution", 1, 10000000),
            bounded_int(data, "capacity", 2, 60))


def update(db, chit, data, user_id):
    action = data.get("action")
    if chit is None:
        if action != "create":
            raise ValueError("Unknown action.")
        result = db.execute("INSERT INTO chits (name, contribution, capacity, owner_id) VALUES (?, ?, ?, ?)",
                            (*chit_values(data), user_id))
        return f"/chits/{result.lastrowid}", "Chit created."

    chit_id = chit["id"]
    path = f"/chits/{chit_id}"
    members, history, round_number, paid = chit_state(db, chit)
    capacity = chit["capacity"]
    locked = bool(history or paid)

    if action == "delete":
        db.execute("DELETE FROM chits WHERE id = ?", (chit_id,))
        return "/dashboard", "Chit deleted."
    if action == "edit":
        name, contribution, new_capacity = chit_values(data)
        if locked and (contribution, new_capacity) != (chit["contribution"], capacity):
            raise ValueError("Contribution and slots are locked once payments are recorded.")
        if new_capacity < len(members):
            raise ValueError("Member slots cannot be fewer than enrolled members.")
        db.execute("UPDATE chits SET name = ?, contribution = ?, capacity = ? WHERE id = ?",
                   (name, contribution, new_capacity, chit_id))
        return path, "Chit updated."
    if action == "add":
        if len(members) >= capacity:
            raise ValueError("All member slots are filled.")
        db.execute("INSERT INTO members (chit_id, name) VALUES (?, ?)", (chit_id, read_name(data)))
        return path, "Member added."

    if action not in {"remove", "pay", "undo", "payout"}:
        raise ValueError("Unknown action.")
    if action != "remove":
        submitted_round = bounded_int(data, "round", 1, 60)
        if submitted_round != round_number or round_number > capacity or len(members) != capacity:
            raise ValueError("This round has changed. Refresh the page and try again.")
    if action == "payout":
        if len(paid) != capacity:
            raise ValueError("Record every member's contribution before paying out.")
        db.execute("INSERT INTO payouts (chit_id, round, member_id) VALUES (?, ?, ?)",
                   (chit_id, round_number, members[round_number - 1]["id"]))
        return path, "Payout recorded."

    member_id = bounded_int(data, "member", 1, 2**63 - 1)
    if member_id not in {member["id"] for member in members}:
        raise ValueError("This member does not belong to the chit.")
    if action == "remove":
        if locked:
            raise ValueError("Members are locked once payments are recorded.")
        db.execute("DELETE FROM members WHERE id = ?", (member_id,))
        return path, "Member removed."
    params = chit_id, round_number, member_id
    if action == "pay":
        if member_id in paid:
            return path, "This contribution has already been paid."
        if bounded_int(data, "amount", 1, 10000000) != chit["contribution"]:
            raise ValueError("The contribution amount changed. Open checkout again.")
        method, outcome = data.get("method"), data.get("outcome")
        if method not in {"upi", "card"} or outcome not in {"success", "decline"}:
            raise ValueError("Choose a valid test payment method and outcome.")
        if outcome == "decline":
            return f"{path}?checkout={member_id}", "Test payment declined. Try again or cancel checkout."
        db.execute("INSERT INTO contributions VALUES (?, ?, ?)", params)
        method_label = {"upi": "UPI", "card": "card"}[method]
        return path, f"Test {method_label} payment approved. Contribution recorded."
    db.execute("DELETE FROM contributions WHERE chit_id = ? AND round = ? AND member_id = ?", params)
    return path, "Contribution removed."


def respond(start_response, body, status=200, headers=(), content_type="text/html; charset=utf-8"):
    start_response(f"{status} {HTTPStatus(status).phrase}", [
        ("Content-Type", content_type), ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; form-action 'self'"),
        *headers,
    ])
    return [body.encode("utf-8")]


def password_digest(password, salt):
    return pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()


def session_token(environ):
    cookie = SimpleCookie()
    try:
        cookie.load(environ.get("HTTP_COOKIE", ""))
    except CookieError:
        return ""
    value = cookie.get("smoothfinance_session")
    return value.value if value and re.fullmatch(r"[A-Za-z0-9_-]{43}", value.value) else ""


def session_cookie(environ, value, age):
    secure = "; Secure" if environ.get("wsgi.url_scheme") == "https" else ""
    return ("Set-Cookie", f"smoothfinance_session={value}; Path=/; HttpOnly; SameSite=Lax"
            f"; Max-Age={age}{secure}")


def digest(value):
    return sha256(value.encode()).hexdigest()


def auth_page(register):
    path = "/register" if register else "/login"
    title = "Create your account" if register else "Sign in"
    alternate = ('Already have an account? <a href="/login">Sign in</a>'
                 if register else 'New to SmoothFinance? <a href="/register">Create an account</a>')
    password_mode = "new-password" if register else "current-password"
    minimum = ' minlength="8"' if register else ""
    fields = f"""<label>Email address<input name="email" type="email" autocomplete="username"
    maxlength="254" required></label>
    <label>Password<input name="password" type="password" autocomplete="{password_mode}"
    maxlength="128"{minimum} required></label>"""
    if register:
        fields += """<label>Confirm password<input name="confirmation" type="password"
        autocomplete="new-password" minlength="8" maxlength="128" required></label>"""
    return title, f"""<div class="auth-grid"><section class="hero auth-intro">
    <small>ROTATING SAVINGS</small><h1>Manage your savings circles</h1>
    <p>Track your chits, member contributions, and payouts from your own dashboard.</p>
    </section><section class="card auth-card"><h2>{title}</h2>
    {form(path, fields, title)}<p>{alternate}</p></section></div>"""


def authenticate(db, data, register):
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Enter a valid email address.")
    if not 8 <= len(password) <= 128:
        raise ValueError("Use a password between 8 and 128 characters.")
    account = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if register:
        if password != data.get("confirmation"):
            raise ValueError("The passwords do not match.")
        if account:
            raise ValueError("An account already exists for this email. Sign in instead.")
        salt = secrets.token_hex(16)
        user_id = db.execute("INSERT INTO users (email, password_hash, salt) VALUES (?, ?, ?)",
                             (email, password_digest(password, salt), salt)).lastrowid
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1:
            db.execute("UPDATE chits SET owner_id = ? WHERE owner_id IS NULL", (user_id,))
        return user_id
    salt = account["salt"] if account else "0" * 32
    candidate = password_digest(password, salt)
    if not account or not secrets.compare_digest(candidate, account["password_hash"]):
        raise ValueError("Email or password is incorrect.")
    return account["id"]


def read_form(environ):
    size = int(environ.get("CONTENT_LENGTH") or 0)
    if not 0 < size <= 8192:
        raise ValueError("Invalid form size.")
    values = parse_qs(environ["wsgi.input"].read(size).decode("utf-8"),
                      keep_blank_values=True, max_num_fields=20)
    return {key: items[0] for key, items in values.items()}


def redirect(start_response, destination, message="", headers=()):
    if message:
        destination += ("&" if "?" in destination else "?") + urlencode({"message": message})
    return respond(start_response, "", 303, (("Location", destination), *headers))


def application(environ, start_response):
    token = session_token(environ)
    marker = csrf.set(digest("form:" + token) if token else FORM_TOKEN)
    try:
        return route(environ, start_response, token)
    finally:
        csrf.reset(marker)


def route(environ, start_response, token):
    path = environ["PATH_INFO"]
    method = environ["REQUEST_METHOD"]
    if path == "/style.css" and method == "GET":
        return respond(start_response, (ROOT / "style.css").read_text(encoding="utf-8"),
                       content_type="text/css; charset=utf-8")
    match = re.fullmatch(r"/chits/([0-9]{1,18})", path)
    auth = path in {"/login", "/register"}
    if path not in {"/", "/dashboard", "/logout"} and not (auth or match):
        return respond(start_response, layout("Page missing", "<h1>Page missing</h1>"), 404)
    allowed = {"/": {"GET"}, "/logout": {"POST"}}.get(path, {"GET", "POST"})
    if method not in allowed:
        return respond(start_response, "", 405, (("Allow", ", ".join(sorted(allowed))),))

    query = parse_qs(environ.get("QUERY_STRING", ""))
    message = query.get("message", [""])[0][:240]
    with closing(connect()) as db, db:
        if method == "POST":
            db.execute("BEGIN IMMEDIATE")
        user = db.execute("""
            SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires > ?
        """, (digest(token), int(time()))).fetchone() if token else None
        if path == "/":
            return respond(start_response, layout("Home", homepage(user), user=user))
        if not user and not auth:
            return redirect(start_response, "/login")
        if user and auth:
            return redirect(start_response, "/dashboard")
        chit = None
        if match:
            chit = db.execute("SELECT * FROM chits WHERE id = ? AND owner_id = ?",
                              (int(match[1]), user["id"])).fetchone()
            if not chit:
                return respond(start_response, layout("Chit missing", "<h1>Chit missing</h1>", user=user), 404)
        if method == "POST":
            try:
                data = read_form(environ)
                if not secrets.compare_digest(data.get("token", "").encode(), csrf.get().encode()):
                    return respond(start_response, layout("Expired form",
                                   "<h1>Refresh the form and try again.</h1>", user=user), 403)
                if auth:
                    user_id = authenticate(db, data, path == "/register")
                    new_token = secrets.token_urlsafe(32)
                    now = int(time())
                    db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
                    db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                               (digest(new_token), user_id, now + SESSION_AGE))
                    return redirect(start_response, "/dashboard", headers=(
                        session_cookie(environ, new_token, SESSION_AGE),))
                if path == "/logout":
                    db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest(token),))
                    return redirect(start_response, "/login", "You signed out.",
                                    (session_cookie(environ, "", 0),))
                destination, message = update(db, chit, data, user["id"])
            except ValueError as error:
                db.rollback()
                destination, message = path, str(error)
            return redirect(start_response, destination, message)

        if auth:
            title, content = auth_page(path == "/register")
            return respond(start_response, layout(title, content, message))
        if chit and "checkout" in query:
            try:
                return respond(start_response, layout(
                    "Pay contribution", checkout(db, chit, query["checkout"][0]), message, user))
            except ValueError as error:
                message = str(error)
        content = detail(db, chit, query.get("confirm") == ["delete"]) if chit else dashboard(db, user)
        return respond(start_response, layout(chit["name"] if chit else "Dashboard", content, message, user))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the local Halal Chit Fund website.")
    parser.add_argument("--open-browser", action="store_true", help="open the website after startup")
    args = parser.parse_args()
    initialize()
    with make_server("127.0.0.1", 8000, application) as server:
        print("Halal Chit Fund is running at http://127.0.0.1:8000", flush=True)
        if args.open_browser:
            webbrowser.open("http://127.0.0.1:8000")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
