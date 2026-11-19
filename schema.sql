PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS chits (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
    contribution INTEGER NOT NULL CHECK (contribution BETWEEN 1 AND 10000000),
    capacity INTEGER NOT NULL CHECK (capacity BETWEEN 2 AND 60)
);
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY,
    chit_id INTEGER NOT NULL REFERENCES chits(id) ON DELETE CASCADE,
    name TEXT NOT NULL, UNIQUE (chit_id, id)
);
CREATE TABLE IF NOT EXISTS contributions (
    chit_id INTEGER NOT NULL, round INTEGER NOT NULL CHECK (round > 0),
    member_id INTEGER NOT NULL,
    PRIMARY KEY (chit_id, round, member_id),
    FOREIGN KEY (chit_id, member_id) REFERENCES members(chit_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS payouts (
    chit_id INTEGER NOT NULL, round INTEGER NOT NULL CHECK (round > 0),
    member_id INTEGER NOT NULL,
    PRIMARY KEY (chit_id, round), UNIQUE (chit_id, member_id),
    FOREIGN KEY (chit_id, member_id) REFERENCES members(chit_id, id) ON DELETE CASCADE
);
