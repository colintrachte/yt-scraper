"""SQLite store: metadata + FTS5 (BM25) + sqlite-vec (dense) in one file.

Design notes:
  - 'videos' holds one row per video with playlist + metadata.
  - 'chunks' holds transcript windows; this is the retrieval unit.
  - 'chunks_fts' is an FTS5 virtual table giving BM25 lexical search.
  - 'chunks_vec' is a sqlite-vec vec0 table giving KNN dense search.
  All three share the same integer chunk id, so fusion is a simple join.
"""
import sqlite3
import sqlite_vec
from . import config


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_schema(db: sqlite3.Connection):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS videos (
            video_id   TEXT PRIMARY KEY,
            title      TEXT,
            url        TEXT,
            playlist   TEXT,
            channel    TEXT,
            published  TEXT,
            duration   INTEGER,
            lang       TEXT,
            source     TEXT,           -- 'captions' or 'whisper'
            status     TEXT,           -- 'ok' | 'disabled' | 'blocked' | 'error'
            error      TEXT,
            fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id   TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
            seq        INTEGER,         -- chunk order within video
            start      REAL,            -- start time (seconds) for deep-linking
            text       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_video ON chunks(video_id);
        """
    )
    # FTS5 external-content table mirrors chunks.text for BM25.
    db.executescript(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text,
            content='chunks',
            content_rowid='id',
            tokenize='porter unicode61'
        );
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
            INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
        END;
        """
    )
    # sqlite-vec KNN table. rowid == chunks.id keeps everything aligned.
    db.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0("
        f"embedding float[{config.EMBED_DIM}])"
    )
    db.commit()


def already_done(db: sqlite3.Connection, video_id: str) -> bool:
    row = db.execute(
        "SELECT status FROM videos WHERE video_id=? AND status='ok'", (video_id,)
    ).fetchone()
    return row is not None
