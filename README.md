# Halal Chit Fund — SmoothFinance

A working Python Flask + SQL university showcase for interest-free rotating savings. Python handles authentication, membership, circle rules, contributions, prepayments, simulated payouts, deletion and receipts. HTML/CSS/browser JavaScript provide the interface. Node.js is not needed to run the application.

**All payments are fake. No bank, card network, UPI service, real identity verification, or real money is involved. No formal Shariah certification is claimed.**

## Demo login IDs

These are shared, fictional showcase logins. They do not establish anyone's real identity. Their records and actions are shared with all visitors. Do not enter personal or financial information.

| Name | Login email | Role |
|---|---|---|
| Shehzeen Fatima | shehzeen@demo.halalchit.test | Organizer |
| Shaik Asefa | asefa@demo.halalchit.test | Member |
| maryam Mohd | maryam@demo.halalchit.test | Member |
| nabeehunnisa | nabeehunnisa@demo.halalchit.test | Member |

Password for all four: **`DemoHalal2026!`**. Fake gateway OTP: **`123456`**.

The login page lets you fill an account with one click. A four-member active demo circle is created once, ready for payments. Shared account names/passwords cannot be changed in the UI. Visitors can also create separate test accounts. Signup does not verify email ownership. Accounts and data from the previous Netlify deployment are not migrated.

## Run locally (Python 3.12+)

```bash
python -m venv .venv
```

Activate on Windows: `.venv\Scripts\activate`. On macOS/Linux: `source .venv/bin/activate`.

```bash
python -m pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000**. SQLite persists records in `instance/halal-chit-fund.sqlite`. No external database is needed locally. `HCF_DEMO_MODE=0` disables sample account provisioning and the login selector. Existing demo accounts remain in the database until explicitly removed; use a fresh database for a private installation.

## Demonstrate code execution

```bash
python -m unittest discover -s tests -v
```

Ten Python integration tests cover authentication and password hashing, authorization, CSRF, invitation approval, discovery privacy, payment ownership and server-derived amounts, wrong OTP, decline/retry, prepayment, a full three-member rotation, concurrent confirmation, deletion preserving history, cancellation, expiry, pause/resume rules, demo seed/login, persistence and validation.

For a browser demonstration:

1. Sign in as Shehzeen; open Payments and the active demo circle.
2. Pay the current contribution using the fixed fake card or UPI fixture and OTP `123456`. Demonstrate a decline and retry if desired.
3. Sign in as the other demo members in separate browser profiles and pay their contributions.
4. As organizer, release the fully funded test pool. Position 1 receives the first payout; payout order stays fixed.
5. Use **Prepay a round** to pay an unpaid future round. That contribution counts only toward its chosen round.
6. Show the receipt, reload, and show that it persists.
7. Use **Delete circle** as organizer, type its exact name, and show that receipts remain in Payments.

Shared demo data can be changed by other visitors, including circle deletion. Create a new circle with separate test accounts for a fully controlled presentation. Restarting the server does not reset history or resurrect deleted demo circles.

## Source architecture

- `app.py`: Flask entrypoint, local server and Vercel app export.
- `backend/app.py`: Python HTTP routes, hashed-password accounts, server-side sessions, origin checks, attempt limits, authorization and receipts.
- `backend/domain.py`: pure Python ROSCA rules and simulated gateway. Money is integer paise. Equal contributions and pools, no interest, bids, platform fees or late fees.
- `backend/storage.py`: SQL repository with atomic version checks. SQLite locally, PostgreSQL through PostgREST in production. Each circle is a versioned JSON document in a SQL row; this is a document-oriented SQL schema rather than separate relational tables for each entity.
- `backend/demo.py`: idempotent sample account and active-circle provisioning.
- `frontend/`: HTML, CSS and JavaScript interface, team credits, demo selector, payment centre and automatic data refresh.
- `sql/schema.sql`: production PostgreSQL table and access policies.
- `tests/test_app.py`: executable Python integration and concurrency tests.

Payment flow: browser Pay → Python Flask API → authenticated account → Python domain validation → atomic SQL version update → receipt and dashboard.

Passwords use Werkzeug scrypt. Session cookies are opaque random tokens (only hashes stored), HttpOnly and SameSite Lax, and Secure in production. Requests cannot choose a different payer or amount. Terminal orders cannot be changed from failed to successful. Simultaneous confirmations use compare-and-swap version updates and retries, preventing duplicate contributions.

Deletion closes a circle and cancels pending checkouts, while retaining contributions, prepayments, payouts, events and receipts. It does not refund or erase fake payments. Automatic data refresh checks every 15 seconds and when returning to the tab, but skips active inputs, dialogs, checkout and account forms, and rechecks safety after requests finish.

Local password recovery writes a temporary link to `instance/outbox`. Mail delivery is not configured for the live university showcase; live recovery reports that limitation. Use demo accounts or contact the project team. Runtime databases, sessions and recovery links are never committed.

## Live deployment

The public site runs the Python Flask backend on Vercel and stores records persistently in PostgreSQL. SQLite is for local use only: a serverless filesystem must not hold persistent user records.

Configure these server-only environment variables on the host:

```text
HCF_SQL_URL=<database project HTTPS URL>
HCF_SQL_PUBLIC_KEY=<database publishable API key>
HCF_SQL_BACKEND_KEY=<strong private machine credential>
HCF_PRODUCTION=1
HCF_DEMO_MODE=1
```

`HCF_SQL_BACKEND_KEY` never enters frontend code or GitHub. SQL row-level security requires its SHA-256 hash to match the dedicated backend policy in `sql/schema.sql`; the public/publishable database key alone cannot read or write records. Application user authorization happens in Python before any record is returned or changed. The deployed machine credential is stored as a Vercel Secret.

For a new database, generate a new random machine credential, replace the hash in the SQL policy, run the schema, and set the matching host secret. Do not use the deployed credential for other applications. The SQL records table is dedicated to this project; existing tables and apps are left intact. Record scans are suitable for a university showcase; add indexed user/member queries before scaling to a large user population.

The Vercel account currently cannot link to this GitHub repository automatically. The current release is deployed directly from the same reviewed source. Install/authorize the Vercel GitHub app for this repository to enable automatic deployment on future pushes. The previous repository revision is preserved on `backup-before-halal-chit-fund-2026-11-19`.

## Team

Shehzeen Fatima · Shaik Asefa · maryam Mohd · nabeehunnisa

Team credits appear on the homepage, signed-in workspace and footer.
