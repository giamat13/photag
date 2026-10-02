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

-- previous versions of a video, kept when it is compressed (or restored): the file under media/.originals
CREATE TABLE IF NOT EXISTS video_backups(
  id INTEGER PRIMARY KEY, photo_id INTEGER, backup_rel TEXT,
  orig_rel TEXT, orig_filename TEXT, orig_sha TEXT, orig_bytes INTEGER,
  created_at INTEGER, kind TEXT, report TEXT
);
CREATE INDEX IF NOT EXISTS ix_vb_photo ON video_backups(photo_id);

-- searches the user saved from Advanced Search (criteria = JSON)
CREATE TABLE IF NOT EXISTS saved_searches(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, criteria TEXT, created_at INTEGER
);

-- files of the automatic-import folder that were already looked at (imported, duplicate or unreadable): never hashed twice
CREATE TABLE IF NOT EXISTS auto_import_seen(
  path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, failed INTEGER DEFAULT 0
);

-- photos that stay in the user's own folder (app/refmode.py): where each file was and how it looked when last read
CREATE TABLE IF NOT EXISTS ref_files(
  path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, photo_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_ref_files_photo ON ref_files(photo_id);

-- per-photo analysis (app/analysis.py): quality score 1-100, sharpness, exposure, perceptual hash for duplicate finding,
-- screenshot / receipt hints and closed eyes (NULL = not checked yet). Rebuilt when the file (sha) or ANALYSIS_VERSION changes.
CREATE TABLE IF NOT EXISTS photo_analysis(
  photo_id INTEGER PRIMARY KEY, sha TEXT, version INTEGER,
  phash INTEGER,            -- 64-bit difference hash as a signed integer; NULL for flat images
  sharp REAL, mean REAL, p5 REAL, p95 REAL, clip_dark REAL, clip_white REAL, sat REAL, ink REAL, white REAL,
  is_screenshot INTEGER DEFAULT 0, is_receipt INTEGER DEFAULT 0, has_camera INTEGER DEFAULT 0,
  faces_n INTEGER, eyes_closed INTEGER,
  score INTEGER
);

-- CLIP image embeddings for search by meaning (app/semantic.py): float16 x 512, rebuilt when the file (sha) changes
CREATE TABLE IF NOT EXISTS photo_clip(photo_id INTEGER PRIMARY KEY, sha TEXT, emb BLOB);

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
    for col in ("tags_en_done INTEGER DEFAULT 0",
                "trashed_at INTEGER",
                "flag INTEGER DEFAULT 0",    # Lightroom pick flag: 1 pick, -1 reject, 0 none
                "label TEXT",                # color label: red|yellow|green|blue|purple
                "quick INTEGER DEFAULT 0",   # member of the Quick Collection
                "edit_ops TEXT",             # JSON of the last applied develop settings
                "exif_json TEXT",            # every EXIF tag of the ORIGINAL file; NULL = not read yet, '{}' = nothing there
                "camera_make TEXT", "camera_model TEXT", "lens TEXT",  # from EXIF, for advanced search / smart collections
                "focal_length REAL", "focal_length_35mm INTEGER"):
        try:
            con.execute(f"ALTER TABLE photos ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # ponytail: column already exists on upgraded DBs
    con.execute("CREATE INDEX IF NOT EXISTS ix_photos_camera ON photos(camera_model)")
    try:
        con.execute("ALTER TABLE albums ADD COLUMN is_trip INTEGER DEFAULT 0")   # photag x triplan, step 1
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("ALTER TABLE albums ADD COLUMN triplan_trip_id TEXT")   # photag x triplan, step 2: which trip
    except sqlite3.OperationalError:
        pass
    # Automatic tagging was removed twice (Ollama, then CLIP): drop what the keyword versions created.
    # Only tags of those sources go; manual, Lightroom and Google keywords are untouched.
    # (search by meaning, app/semantic.py, keeps its embeddings in photo_clip and makes no keywords.)
    con.execute("DROP TABLE IF EXISTS clip_emb")
    con.execute("DROP TABLE IF EXISTS autotag_rejected")
    if con.execute("SELECT 1 FROM photo_tags WHERE source LIKE 'ollama%' OR source IN ('auto','place') LIMIT 1").fetchone():
        con.execute("DELETE FROM photo_tags WHERE source LIKE 'ollama%' OR source IN ('auto','place')")
        con.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM photo_tags)")
    con.commit()
    return con


def get_setting(con, key, default=None):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(con, key, value):
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    con.commit()
