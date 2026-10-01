"""Turn transcripts into searchable chunks: window -> embed -> store.

We keep one embedding model instance alive (ONNX/CPU via fastembed) and
batch-embed for throughput. Vectors are serialized with sqlite-vec's helper
so the vec0 table can KNN over them.
"""
import json
from datetime import datetime, timezone

import sqlite_vec
from fastembed import TextEmbedding

from . import config

_embedder = None


def embedder() -> TextEmbedding:
    global _embedder
    if _embedder is None:
        # First call downloads the ONNX model to a local cache, then offline.
        _embedder = TextEmbedding(model_name=config.EMBED_MODEL)
    return _embedder


def window_transcript(snippets: list[dict]) -> list[dict]:
    """Sliding-window over snippet text, preserving the start time of the
    first snippet in each window for deep-linking back into the video."""
    chunks, buf, buf_start, buf_len, seq = [], [], None, 0, 0
    for s in snippets:
        text = s["text"].strip()
        if not text:
            continue
        if buf_start is None:
            buf_start = s["start"]
        buf.append(text)
        buf_len += len(text) + 1
        if buf_len >= config.CHUNK_CHARS:
            joined = " ".join(buf)
            chunks.append({"seq": seq, "start": buf_start, "text": joined})
            seq += 1
            # carry overlap tail into the next window
            tail, tail_len = [], 0
            for t in reversed(buf):
                tail_len += len(t) + 1
                tail.insert(0, t)
                if tail_len >= config.CHUNK_OVERLAP:
                    break
            buf, buf_len, buf_start = tail, tail_len, None
    if buf:
        chunks.append({"seq": seq, "start": buf_start or 0.0, "text": " ".join(buf)})
    return chunks


def index_video(db, meta: dict, snippets: list[dict], source: str,
                lang: str, is_generated: bool):
    """Insert/replace a video and all its chunks (+ FTS via triggers, + vectors)."""
    now = datetime.now(timezone.utc).isoformat()
    db.execute("DELETE FROM videos WHERE video_id=?", (meta["video_id"],))
    db.execute(
        """INSERT INTO videos(video_id,title,url,playlist,channel,published,
               duration,lang,source,status,error,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (meta["video_id"], meta.get("title"), meta.get("url"),
         meta.get("playlist"), meta.get("channel"), meta.get("published"),
         meta.get("duration"), lang, source, "ok", None, now),
    )

    chunks = window_transcript(snippets)
    if not chunks:
        return 0
    texts = [c["text"] for c in chunks]
    # bge models recommend a passage prefix for indexing-time embeddings
    vectors = list(embedder().embed(texts))

    for c, vec in zip(chunks, vectors):
        cur = db.execute(
            "INSERT INTO chunks(video_id,seq,start,text) VALUES(?,?,?,?)",
            (meta["video_id"], c["seq"], c["start"], c["text"]),
        )
        cid = cur.lastrowid
        db.execute(
            "INSERT INTO chunks_vec(rowid, embedding) VALUES(?,?)",
            (cid, sqlite_vec.serialize_float32(list(map(float, vec)))),
        )
    db.commit()
    return len(chunks)


def record_failure(db, meta: dict, status: str, error: str):
    now = datetime.now(timezone.utc).isoformat()
    db.execute("DELETE FROM videos WHERE video_id=?", (meta["video_id"],))
    db.execute(
        """INSERT INTO videos(video_id,title,url,playlist,channel,published,
               duration,lang,source,status,error,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (meta["video_id"], meta.get("title"), meta.get("url"),
         meta.get("playlist"), meta.get("channel"), meta.get("published"),
         meta.get("duration"), None, None, status, error[:500], now),
    )
    db.commit()


def archive_raw(meta: dict, snippets: list[dict]):
    """Write a durable per-video json+markdown archive alongside the DB,
    so the SQLite index is always rebuildable from source of record."""
    vid = meta["video_id"]
    (config.RAW_DIR / f"{vid}.json").write_text(
        json.dumps({"meta": meta, "snippets": snippets}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    body = " ".join(s["text"] for s in snippets)
    md = f"# {meta.get('title')}\n\n{meta.get('url')}\n\n{body}\n"
    (config.RAW_DIR / f"{vid}.md").write_text(md, encoding="utf-8")
