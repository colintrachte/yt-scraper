"""Hybrid search = FTS5 BM25 (lexical) + sqlite-vec KNN (semantic), fused
with Reciprocal Rank Fusion. RRF avoids normalizing BM25 scores against
cosine distances (different scales) — you just merge by rank.

    RRF(d) = sum_over_lists( 1 / (k + rank_in_list(d)) )
"""
import sqlite_vec
from . import config
from .index import embedder


def _bm25(db, query: str, k: int):
    # FTS5 MATCH ranks by bm25(); lower is better, so order ascending.
    rows = db.execute(
        """SELECT c.id AS id, bm25(chunks_fts) AS score
           FROM chunks_fts
           JOIN chunks c ON c.id = chunks_fts.rowid
           WHERE chunks_fts MATCH ?
           ORDER BY score LIMIT ?""",
        (_fts_query(query), k),
    ).fetchall()
    return [r["id"] for r in rows]


def _fts_query(q: str) -> str:
    # Quote each term to keep FTS5 from choking on punctuation/operators.
    terms = [t for t in "".join(ch if ch.isalnum() or ch.isspace() else " "
                          for ch in q).split() if t]
    return " OR ".join(f'"{t}"' for t in terms) or '""'


def _vector(db, query: str, k: int):
    qv = list(embedder().query_embed([query]))[0]
    rows = db.execute(
        """SELECT rowid AS id, distance
           FROM chunks_vec
           WHERE embedding MATCH ? AND k = ?
           ORDER BY distance""",
        (sqlite_vec.serialize_float32(list(map(float, qv))), k),
    ).fetchall()
    return [r["id"] for r in rows]


def _rrf(ranked_lists, k: int):
    scores = {}
    for lst in ranked_lists:
        for rank, cid in enumerate(lst):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)


def hybrid_search(db, query: str, top_k: int | None = None,
                  mode: str = "hybrid"):
    top_k = top_k or config.FINAL_TOPK
    lists = []
    if mode in ("hybrid", "lexical"):
        lists.append(_bm25(db, query, config.BM25_TOPK))
    if mode in ("hybrid", "semantic"):
        lists.append(_vector(db, query, config.VECTOR_TOPK))
    fused = _rrf(lists, config.RRF_K)[:top_k]

    if not fused:
        return []
    placeholders = ",".join("?" * len(fused))
    rows = db.execute(
        f"""SELECT c.id, c.text, c.start, v.title, v.url, v.playlist, v.channel
            FROM chunks c JOIN videos v ON v.video_id = c.video_id
            WHERE c.id IN ({placeholders})""",
        fused,
    ).fetchall()
    order = {cid: i for i, cid in enumerate(fused)}
    results = sorted((dict(r) for r in rows), key=lambda r: order[r["id"]])
    for r in results:
        s = int(r["start"] or 0)
        r["deep_link"] = f"{r['url']}&t={s}s"
    return results
