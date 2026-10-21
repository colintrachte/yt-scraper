import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
import main
import search


def make_database(tmp_path):
    db = tmp_path / "archive.db"
    with core.db_connection(db) as conn:
        core.ensure_schema(conn)
        for index, tags in enumerate([["other"], ["Python"], ["python"]]):
            vid = f"video{index:06d}"
            conn.execute(
                "INSERT INTO videos(video_id,title,tags,quality_score) VALUES (?,?,?,?)",
                (vid, "needle", json.dumps(tags), 80),
            )
            conn.execute(
                "INSERT INTO videos_fts(video_id,title) VALUES (?,?)", (vid, "needle")
            )
            conn.execute(
                "INSERT INTO transcripts(video_id,text) VALUES (?,?)",
                (vid, "transcript-only phrase"),
            )
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


def test_all_search_includes_transcript_only_matches(tmp_path):
    db = make_database(tmp_path)
    results = search.search(db, "transcript-only", "all", tag="python")
    assert {result["video_id"] for result in results} == {
        "video000001",
        "video000002",
    }


def test_rag_export_without_query(tmp_path, monkeypatch):
    db = make_database(tmp_path)
    (tmp_path / "summaries.jsonl").write_text(
        json.dumps({"video_id": "video000001", "summary": "A summary"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["search.py", "--rag", "--db", str(db)])
    assert search.main() == 0
    assert (tmp_path / "rag_dataset.jsonl").exists()


def test_rebuild_backup_includes_uncheckpointed_wal(tmp_path):
    db = make_database(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute(
            "INSERT INTO videos(video_id,title) VALUES ('pending0001','WAL sentinel')"
        )
        conn.commit()
        core.build_db(tmp_path, db)
        with sqlite3.connect(db.with_suffix(".bak.db")) as backup:
            assert (
                backup.execute(
                    "SELECT title FROM videos WHERE video_id='pending0001'"
                ).fetchone()[0]
                == "WAL sentinel"
            )
    finally:
        conn.close()


def test_cancelled_rebuild_rolls_back_derived_tables(tmp_path):
    db = make_database(tmp_path)
    (tmp_path / "videos_full.jsonl").write_text(
        json.dumps({"id": "video000001", "title": "replacement"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="rolled back"):
        core.build_db(tmp_path, db, cancel_check=lambda: True)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM videos_fts").fetchone()[0] == 3
        assert (
            conn.execute(
                "SELECT title FROM videos WHERE video_id='video000001'"
            ).fetchone()[0]
            == "needle"
        )


def test_highlighted_source_text_cannot_inject_html():
    rendered = core.highlight_search_snippet(
        '<img src=x onerror="alert(1)"> Python & python', "python"
    )
    assert "<img" not in rendered
    assert "&lt;img" in rendered
    assert rendered.count("<mark>") == 2
    assert "&amp;" in rendered


class NoDelayThrottler:
    max_retries = 0

    def pace(self, *_args, **_kwargs):
        return True

    def record_success(self):
        pass


@pytest.mark.parametrize(
    ("url", "expected_video", "expected_playlist"),
    [
        ("abcdefghijk", "abcdefghijk", None),
        ("https://youtu.be/abcdefghijk?si=share", "abcdefghijk", None),
        ("https://www.youtube.com/watch?v=abcdefghijk", "abcdefghijk", None),
        ("https://www.youtube.com/shorts/abcdefghijk", "abcdefghijk", None),
        ("https://www.youtube.com/live/abcdefghijk?feature=share", "abcdefghijk", None),
        ("https://www.youtube-nocookie.com/embed/abcdefghijk", "abcdefghijk", None),
        (
            "https://music.youtube.com/watch?v=abcdefghijk&list=PL_testing",
            "abcdefghijk",
            "PL_testing",
        ),
    ],
)
def test_extract_ids_supports_common_youtube_urls(
    url, expected_video, expected_playlist
):
    assert core.extract_ids_from_url(url) == (expected_video, expected_playlist)


def test_parse_url_input_accepts_share_text_handles_and_bare_ids():
    raw = (
        "Try (https://www.youtube.com/shorts/abcdefghijk), "
        "@usefulchannel and lmnopqrstuv."
    )
    assert main.parse_url_input(raw) == [
        "https://www.youtube.com/shorts/abcdefghijk",
        "https://www.youtube.com/@usefulchannel",
        "https://www.youtube.com/watch?v=lmnopqrstuv",
    ]


def test_playlist_discovery_uses_flat_playlist_options(monkeypatch):
    captured = {}

    class FakeYDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            assert download is False
            return {
                "id": "PL_testing",
                "title": "Playlist",
                "entries": [{"id": "abcdefghijk", "title": "Saved"}],
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    videos, playlists, mappings = core.get_playlist_data(
        "https://www.youtube.com/playlist?list=PL_testing",
        limit=5,
        throttler=NoDelayThrottler(),
    )

    assert captured["no_playlist"] is False
    assert captured["ignoreerrors"] is True
    assert captured["extract_flat"] == "in_playlist"
    assert captured["playlistend"] == 5
    assert [video["id"] for video in videos] == ["abcdefghijk"]
    assert playlists[0]["playlist_id"] == "PL_testing"
    assert mappings[0]["video_id"] == "abcdefghijk"


def test_failed_single_video_discovery_still_returns_visible_placeholder(monkeypatch):
    class EmptyYDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            assert download is False

    monkeypatch.setitem(
        sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=EmptyYDL)
    )
    videos, _, _ = core.get_playlist_data(
        "https://www.youtube.com/shorts/abcdefghijk",
        throttler=NoDelayThrottler(),
    )

    assert videos[0]["id"] == "abcdefghijk"
    assert "metadata pending" in videos[0]["title"]


def test_zero_comment_video_is_completed_and_skipped_on_resume(tmp_path, monkeypatch):
    calls = []

    class EmptyDownloader:
        def get_comments_from_url(self, url, **_kwargs):
            calls.append(url)
            return iter(())

    fake_module = types.SimpleNamespace(YoutubeCommentDownloader=EmptyDownloader)
    monkeypatch.setitem(sys.modules, "youtube_comment_downloader", fake_module)
    output = tmp_path / "comments.jsonl"
    video_id = "abcdefghijk"

    for _ in range(2):
        core.fetch_comments_bulk(
            [video_id], output, throttler=NoDelayThrottler(), resume=True
        )

    assert len(calls) == 1
    completion = core.load_jsonl_deduped(
        tmp_path / "completed_comments.jsonl", "video_id"
    )
    assert completion[video_id]["comment_count"] == 0


def test_missing_comment_dependency_never_erases_existing_data(tmp_path, monkeypatch):
    output = tmp_path / "comments.jsonl"
    output.write_text('{"video_id":"abcdefghijk","text":"keep me"}\n')
    monkeypatch.setitem(sys.modules, "youtube_comment_downloader", None)

    core.fetch_comments_bulk(["abcdefghijk"], output)

    assert "keep me" in output.read_text()


def test_resolved_failures_are_hidden(tmp_path):
    video_id = "abcdefghijk"
    (tmp_path / "videos_full.jsonl").write_text(
        json.dumps({"id": video_id}) + "\n", encoding="utf-8"
    )
    (tmp_path / "failed_metadata.jsonl").write_text(
        json.dumps({"video_id": video_id, "type": "metadata", "error": "old"}) + "\n",
        encoding="utf-8",
    )

    assert core.load_unresolved_failures(tmp_path) == []


def test_archive_database_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    video_id = "abcdefghijk"
    monkeypatch.setattr(
        main,
        "core_get_playlist_data",
        lambda *_args, **_kwargs: ([{"id": video_id}], [], []),
    )
    monkeypatch.setattr(main, "fetch_videos_full_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "fetch_transcripts_bulk", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "fetch_comments_bulk", lambda *_a, **_k: None)
    monkeypatch.setattr(
        main, "build_db", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full"))
    )

    main.archive_job(
        ["https://www.youtube.com/watch?v=" + video_id],
        {"out_dir": str(tmp_path), "pacing": "fast"},
    )

    assert main.STATE["stage"] == "error"
    assert "disk full" in main.STATE["stage_details"]
    assert not main.STATE["running"]


def test_stop_and_save_rebuilds_database_before_stopping(tmp_path, monkeypatch):
    video_id = "abcdefghijk"
    built = []
    monkeypatch.setattr(
        main,
        "core_get_playlist_data",
        lambda *_args, **_kwargs: ([{"id": video_id}], [], []),
    )

    def stop_during_metadata(*_args, **_kwargs):
        with main.LOCK:
            main.STATE["cancel"] = True

    monkeypatch.setattr(main, "fetch_videos_full_metadata", stop_during_metadata)
    monkeypatch.setattr(
        main,
        "fetch_transcripts_bulk",
        lambda *_a, **_k: pytest.fail("transcripts ran after stop"),
    )
    monkeypatch.setattr(
        main,
        "fetch_comments_bulk",
        lambda *_a, **_k: pytest.fail("comments ran after stop"),
    )
    monkeypatch.setattr(
        main,
        "build_db",
        lambda *_a, **_k: built.append(True) or {"videos": 1},
    )

    main.archive_job(
        ["https://www.youtube.com/watch?v=" + video_id],
        {"out_dir": str(tmp_path), "pacing": "fast"},
    )

    assert built == [True]
    assert main.STATE["stage"] == "stopped"
    assert "Progress saved" in main.STATE["stage_details"]
