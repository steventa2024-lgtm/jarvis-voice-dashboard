-- {name} — schema
--
-- Kept as SQL rather than built in Python so it can be read, diffed and run
-- by hand. `server.setup()` executes this on every start; every statement
-- must therefore be safe to run twice.

CREATE TABLE IF NOT EXISTS items (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    title   TEXT NOT NULL,
    done    INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_done ON items(done);
