-- Run once: npx wrangler d1 execute mangadle --remote --file=schema.sql
CREATE TABLE IF NOT EXISTS results (
  date TEXT NOT NULL,
  player_id TEXT NOT NULL,
  total INTEGER NOT NULL,
  scores TEXT NOT NULL,
  correct TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (date, player_id)
);

CREATE TABLE IF NOT EXISTS suggestions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  anilist_id INTEGER NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS suggestions_unused ON suggestions (used, id);
