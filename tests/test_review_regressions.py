import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
import search


def make_database(tmp_path):
    db = tmp_path / "archive.db"
    with core.db_connection(db) as conn:
        core.ensure_schema(conn)
        for index, tags in enumerate([["other"], ["Python"], ["python"]]):
            vid = f"video{index:06d}"
            conn.execute("INSERT INTO videos(video_id,title,tags,quality_score) VALUES (?,?,?,?)",
                         (vid, "needle", json.dumps(tags), 80))
            conn.execute("INSERT INTO videos_fts(video_id,title) VALUES (?,?)", (vid, "needle"))
            conn.execute("INSERT INTO transcripts(video_id,text) VALUES (?,?)", (vid, "transcript-only phrase"))
    return db


def test_tag_filter_precedes_pagination(tmp_path):
    db = make_database(tmp_path)
    first = search.search(db, "needle", "videos", tag="python", limit=1)
    second = search.search(db, "needle", "videos", tag="python", limit=1, offset=1)
    assert len(first) == len(second) == 1
    assert first[0]["video_id"] != second[0]["video_id"]


def test_transcript_fallback_searches_transcript_body(tmp_path):
    db = make_database(tmp_path)
    assert len(search.search(db, "transcript-only", "transcripts", tag="python")) == 2


def test_rag_export_without_query(tmp_path, monkeypatch):
    db = make_database(tmp_path)
    (tmp_path / "summaries.jsonl").write_text(json.dumps({"video_id": "video000001", "summary": "A summary"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["search.py", "--rag", "--db", str(db)])
    assert search.main() == 0
    assert (tmp_path / "rag_dataset.jsonl").exists()


def test_rebuild_backup_includes_uncheckpointed_wal(tmp_path):
    db = make_database(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("INSERT INTO videos(video_id,title) VALUES ('pending0001','WAL sentinel')")
        conn.commit()
        core.build_db(tmp_path, db)
        with sqlite3.connect(db.with_suffix(".bak.db")) as backup:
            assert backup.execute("SELECT title FROM videos WHERE video_id='pending0001'").fetchone()[0] == "WAL sentinel"
    finally:
        conn.close()


def test_cancelled_rebuild_rolls_back_derived_tables(tmp_path):
    db = make_database(tmp_path)
    (tmp_path / "videos_full.jsonl").write_text(json.dumps({"id": "video000001", "title": "replacement"}) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="rolled back"):
        core.build_db(tmp_path, db, cancel_check=lambda: True)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM videos_fts").fetchone()[0] == 3
        assert conn.execute("SELECT title FROM videos WHERE video_id='video000001'").fetchone()[0] == "needle"


def test_highlighted_source_text_cannot_inject_html():
    rendered = core.highlight_search_snippet('<img src=x onerror="alert(1)"> Python & python', "python")
    assert "<img" not in rendered
    assert '&lt;img' in rendered
    assert rendered.count("<mark>") == 2
    assert "&amp;" in rendered
