"""Local classroom web app. Run with Python 3.10 or newer."""
import argparse
from contextlib import closing
from html import escape
from http import HTTPStatus
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.parse import parse_qs, urlencode
import webbrowser
from wsgiref.simple_server import make_server

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "chits.sqlite3"
FORM_TOKEN = secrets.token_urlsafe(32)


def connect():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize():
    with closing(connect()) as db:
        db.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))


def money(value):
    return f"₹{value:,}"


def layout(title, content, message=""):
    alert = f'<p class="alert" role="status">{escape(message)}</p>' if message else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)} | Halal Chit Fund</title><link rel="stylesheet" href="/style.css">
    </head><body><header><div class="shell nav"><a href="/" class="brand">◈ &nbsp; Halal Chit Fund</a>
    <span>School project</span></div></header><main class="shell">{alert}{content}</main>
    <footer class="shell">Classroom simulation · All entries use simulated funds.</footer></body></html>"""


def form(path, fields, label, action="", css=""):
    return f"""<form method="post" action="{path}" class="{css}">
    <input type="hidden" name="token" value="{FORM_TOKEN}">
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


def dashboard(db):
    chits = db.execute("""
        SELECT c.*, (SELECT COUNT(*) FROM members WHERE chit_id = c.id) AS enrolled,
        (SELECT COUNT(*) FROM payouts WHERE chit_id = c.id) AS rounds
        FROM chits c ORDER BY c.id DESC
    """).fetchall()
    cards = []
    for chit in chits:
        status = ("Completed" if chit["rounds"] == chit["capacity"] else
                  "Enrolling" if chit["enrolled"] < chit["capacity"] else "In progress")
        cards.append(f"""<article class="card item"><div><small>{status}</small>
        <h3><a href="/chits/{chit['id']}">{escape(chit['name'])}</a></h3>
        <p>{chit['enrolled']}/{chit['capacity']} members · {money(chit['contribution'])} per round</p>
        </div><a class="link-button" href="/chits/{chit['id']}">Open →</a></article>""")
    listing = "".join(cards) or '<div class="card"><p>No chits yet. Create one to get started.</p></div>'
    return f"""<div class="hero"><small>ROTATING SAVINGS</small><h1>Chit dashboard</h1>
    <p>Members contribute equally each round and take turns receiving the full pool.
    The simulation uses a fixed rotation with zero interest, fees, or bidding.</p></div>
    <div class="grid"><section><h2>Your chits <span class="count">{len(chits)}</span></h2>{listing}</section>
    <section><h2>Create a chit</h2><div class="card">{form("/", chit_fields(), "Create chit", "create")}</div></section></div>"""


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
            action += form(path, member_field + hidden("round", round_number),
                           "Undo payment" if is_paid else "Record payment",
                           "undo" if is_paid else "pay", "inline")
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
    return f"""<div class="hero compact"><a href="/">← All chits</a>
    <h1>{escape(chit['name'])}</h1><p>{money(amount)} per member per round · {pot} total pool</p></div>
    <div class="grid"><div><section class="card round">{round_panel}</section>
    <section class="card"><h2>Members <span class="count">{len(members)}/{capacity}</span></h2>
    <p>Payouts follow the member order shown here.</p>{roster}{add}</section></div>
    <aside><section class="card"><h2>Payout history</h2>{payout_history}</section>
    <section class="card"><h2>Edit chit</h2>
    {"<p>Contribution and slots are locked once payments are recorded.</p>" if locked else ""}
    {form(path, chit_fields(chit, locked), "Save changes", "edit")}</section>
    <section class="card">{deletion}</section></aside></div>"""


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


def update(db, chit, data):
    action = data.get("action")
    if chit is None:
        if action != "create":
            raise ValueError("Unknown action.")
        result = db.execute("INSERT INTO chits (name, contribution, capacity) VALUES (?, ?, ?)",
                            chit_values(data))
        return f"/chits/{result.lastrowid}", "Chit created."

    chit_id = chit["id"]
    path = f"/chits/{chit_id}"
    members, history, round_number, paid = chit_state(db, chit)
    capacity = chit["capacity"]
    locked = bool(history or paid)

    if action == "delete":
        db.execute("DELETE FROM chits WHERE id = ?", (chit_id,))
        return "/", "Chit deleted."
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
        db.execute("INSERT OR IGNORE INTO contributions VALUES (?, ?, ?)", params)
        return path, "Contribution recorded."
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


def application(environ, start_response):
    path = environ["PATH_INFO"]
    method = environ["REQUEST_METHOD"]
    if path == "/style.css" and method == "GET":
        return respond(start_response, (ROOT / "style.css").read_text(encoding="utf-8"),
                       content_type="text/css; charset=utf-8")
    match = re.fullmatch(r"/chits/([0-9]{1,18})", path)
    if path != "/" and not match:
        return respond(start_response, layout("Page missing", "<h1>Page missing</h1>"), 404)
    if method not in {"GET", "POST"}:
        return respond(start_response, "", 405, (("Allow", "GET, POST"),))

    with closing(connect()) as db, db:
        if method == "POST":
            db.execute("BEGIN IMMEDIATE")
        chit = None
        if match:
            chit = db.execute("SELECT * FROM chits WHERE id = ?", (int(match[1]),)).fetchone()
            if not chit:
                return respond(start_response, layout("Chit missing", "<h1>Chit missing</h1>"), 404)
        if method == "POST":
            try:
                size = int(environ.get("CONTENT_LENGTH") or 0)
                if not 0 < size <= 8192:
                    raise ValueError("Invalid form size.")
                values = parse_qs(environ["wsgi.input"].read(size).decode("utf-8"),
                                  keep_blank_values=True, max_num_fields=20)
                data = {key: items[0] for key, items in values.items()}
                if not secrets.compare_digest(data.get("token", "").encode(), FORM_TOKEN.encode()):
                    return respond(start_response, layout("Expired form", "<h1>Refresh the form and try again.</h1>"), 403)
                destination, message = update(db, chit, data)
            except ValueError as error:
                db.rollback()
                destination, message = path, str(error)
            location = destination + "?" + urlencode({"message": message})
            return respond(start_response, "", 303, (("Location", location),))
        query = parse_qs(environ.get("QUERY_STRING", ""))
        message = query.get("message", [""])[0][:240]
        content = detail(db, chit, query.get("confirm") == ["delete"]) if chit else dashboard(db)
        return respond(start_response, layout(chit["name"] if chit else "Dashboard", content, message))


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
