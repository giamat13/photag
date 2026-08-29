"""SQLite catalog. Single-user local app -> plain sqlite3, no ORM."""
import sqlite3
from pathlib import Path

from .config import PATHS

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS photos(
  id INTEGER PRIMARY KEY,
  sha256 TEXT UNIQUE,
  filename TEXT,
  rel_path TEXT,            -- relative to library media root
  mime TEXT,
  is_video INTEGER DEFAULT 0,
  width INTEGER, height INTEGER, bytes INTEGER,
  taken_at INTEGER,         -- unix seconds (photoTakenTime)
  created_at INTEGER,       -- unix seconds (creationTime / import)
  lat REAL, lng REAL, altitude REAL,
  description TEXT,
  favorited INTEGER DEFAULT 0,
  rating INTEGER DEFAULT 0, -- 0-5, Windows-style
  trashed INTEGER DEFAULT 0,
  trashed_at INTEGER,       -- unix seconds; set when moved to trash, used to auto-purge
  gphotos_url TEXT,
  faces_done INTEGER DEFAULT 0,
  tags_done INTEGER DEFAULT 0,
  tags_en_done INTEGER DEFAULT 0,
  edited INTEGER DEFAULT 0,
  orig_backup TEXT,         -- rel path of pre-edit original, if edited
  imported_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_photos_taken ON photos(taken_at);

CREATE TABLE IF NOT EXISTS albums(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE,
  description TEXT,
  access TEXT,
  album_date INTEGER,
  kind TEXT,                -- 'year' | 'album' | 'people-share'
  cover_photo_id INTEGER
);
CREATE TABLE IF NOT EXISTS photo_albums(
  photo_id INTEGER, album_id INTEGER,
  PRIMARY KEY(photo_id, album_id)
);

CREATE TABLE IF NOT EXISTS people(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE,
  source TEXT,              -- 'takeout' | 'manual' | 'cluster'
  cover_face_id INTEGER
);
-- photo-level names straight from Google Takeout (no bounding box)
CREATE TABLE IF NOT EXISTS photo_people(
  photo_id INTEGER, person_id INTEGER, source TEXT,
  PRIMARY KEY(photo_id, person_id)
);

CREATE TABLE IF NOT EXISTS faces(
  id INTEGER PRIMARY KEY,
  photo_id INTEGER,
  x1 REAL, y1 REAL, x2 REAL, y2 REAL,
  det_score REAL,
  embedding BLOB,           -- 512 float32
  cluster_id INTEGER,
  person_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_faces_photo ON faces(photo_id);
CREATE INDEX IF NOT EXISTS ix_faces_cluster ON faces(cluster_id);

CREATE TABLE IF NOT EXISTS tags(id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS photo_tags(
  photo_id INTEGER, tag_id INTEGER, source TEXT,
  PRIMARY KEY(photo_id, tag_id)
);

-- extra Takeout artifacts so nothing from the ZIP is lost
CREATE TABLE IF NOT EXISTS memory_titles(title TEXT);
CREATE TABLE IF NOT EXISTS shared_comments(
  text TEXT, liked INTEGER, created_at INTEGER, content_url TEXT
);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(PATHS.db, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    con = connect()
    con.executescript(SCHEMA)
    try:
        con.execute("ALTER TABLE photos ADD COLUMN tags_en_done INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # ponytail: column already exists on upgraded DBs
    try:
        con.execute("ALTER TABLE photos ADD COLUMN trashed_at INTEGER")
    except sqlite3.OperationalError:
        pass  # ponytail: column already exists on upgraded DBs
    con.commit()
    return con


def get_setting(con, key, default=None):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(con, key, value):
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    con.commit()
