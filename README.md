# Halal Chit Fund · School project

A classroom simulation of rotating savings built with Python and SQLite.
Every member contributes the same amount each round and receives the full
pool once, in enrollment order. The project uses no interest, fees, bids,
or real payments.

## Run locally

On Windows, install Python 3.10 or newer, extract the project, and
double-click `Start_SmoothFinance.bat`. Keep the launcher window open.
If your browser does not open automatically, go to
[http://127.0.0.1:8000](http://127.0.0.1:8000).

### Start from a terminal

1. Extract the ZIP and open a terminal in the `halal-chit-fund` folder.
2. Run `python app.py`. On Windows you can also use `py app.py`.
3. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).
4. Press Ctrl+C in the terminal to stop the local server.

Use Python 3.10 or newer. There are no packages to install. The app creates
`chits.sqlite3` alongside the source on first run and saves changes there.

## Classroom demo

1. Create a chit with three member slots and a ₹500 contribution.
2. Add three members in the order they should receive payouts.
3. Record each contribution. Test the Undo payment button on the current round.
4. After all three contributions are recorded, record the ₹1,500 payout.
5. Repeat for the next two rounds and check the payout history.
6. Create a second chit to demonstrate editing and confirmed deletion.

The contribution, member slots, and roster lock when the first payment is
recorded. Undoing every payment in the first round reopens those settings.
A recorded payout advances the round and preserves its contribution records.
Delete chit removes the group and all of its records after confirmation.

## Source files

- `app.py` handles web routes, validation, and database transactions.
- `schema.sql` defines the four SQLite tables and their constraints.
- `style.css` styles the dashboard and chit details.

The website is designed for one local operator. It has no user accounts
or payment integration.
