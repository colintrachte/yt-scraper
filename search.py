#!/usr/bin/env python3
"""
search.py - Refactored search with pagination, proper tag parsing, repository layer
"""
from __future__ import annotations
import argparse
import csv
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

# logging
logger = logging.getLogger("ytkb.search")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Use core helpers for consistency
try:
    from core import db_connection, parse_tags_field, CONFIG, is_valid_video_id
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from core import db_connection, parse_tags_field, CONFIG, is_valid_video_id

@dataclass
class SearchFilters:
    query: str
    search_type: str = "summaries"
    playlist_id: Optional[str] = None
    tag: Optional[str] = None
    min_quality: float = 0.0
    limit: int = 20
    offset: int = 0

def _safe_tables() -> List[str]:
    return ["videos", "playlists", "playlist_videos", "transcripts", "comments", "failures", "summaries"]

def print_stats(db_path: Path) -> None:
    if not db_path.exists():
        print(f"No DB at {db_path}")
        return
    try:
        with db_connection(db_path) as conn:
            cur = conn.cursor()
            print(f"\n=== {db_path.resolve()} ===")
            for tbl in _safe_tables():
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                    count = cur.fetchone()[0]
                    print(f"{tbl:20} : {count}")
                except sqlite3.OperationalError as e:
                    print(f"{tbl:20} : - ({e})")
                except sqlite3.Error as e:
                    print(f"{tbl:20} : - ({e})")
            try:
                print("\nQuality:")
                cur.execute("SELECT quality_label, COUNT(*), AVG(quality_score) FROM videos GROUP BY quality_label ORDER BY AVG(quality_score) DESC")
                for label, cnt, avg in cur.fetchall():
                    print(f"  {label or 'unknown':10} : {cnt} (avg {avg:.1f})")
            except sqlite3.Error as e:
                logger.debug(f"Quality stats failed: {e}")
            try:
                print("\nStatus (for manual review):")
                cur.execute("SELECT status, COUNT(*) FROM videos GROUP BY status")
                for status, cnt in cur.fetchall():
                    print(f"  {status:20} : {cnt}")
            except sqlite3.Error as e:
                logger.debug(f"Status stats failed: {e}")
            try:
                print("\nSummaries:")
                cur.execute("SELECT COUNT(*) FROM videos WHERE summary IS NOT NULL AND summary!=''")
                print(f"  with summary: {cur.fetchone()[0]}")
                cur.execute("SELECT COUNT(*) FROM videos WHERE summary IS NULL OR summary=''")
                print(f"  without summary: {cur.fetchone()[0]}")
            except sqlite3.Error as e:
                logger.debug(f"Summary stats failed: {e}")
    except sqlite3.Error as e:
        print(f"Failed to open DB {db_path}: {e}")
        return

    out_dir = db_path.parent
    for name in ["videos_full.jsonl", "playlists.jsonl", "summaries.jsonl", "rag_dataset.jsonl", "rag_context.md"]:
        p = out_dir / name
        if p.exists():
            try:
                mb = p.stat().st_size / 1024 / 1024
                lines = 0
                if p.suffix == ".jsonl":
                    try:
                        with open(p, encoding="utf-8", errors="ignore") as f:
                            lines = sum(1 for _ in f)
                    except OSError:
                        lines = 0
                extra = f"{lines} lines, " if lines else ""
                print(f"{name:25} : {extra}{mb:.1f} MB")
            except OSError as e:
                logger.debug(f"Stat for {p} failed: {e}")

def _apply_tag_filter_exact(results: List[Dict[str, Any]], tag: str) -> List[Dict[str, Any]]:
    """Fixes original bug: substring matching on raw JSON string. Now parses tags array and checks membership."""
    if not tag:
        return results
    tag_lower = tag.lower()
    filtered = []
    for r in results:
        tags = parse_tags_field(r.get("tags"))
        if tag_lower in tags:
            # normalize to list for output
            try:
                r["tags"] = json.loads(r.get("tags") or "[]") if isinstance(r.get("tags"), str) else r.get("tags")
            except json.JSONDecodeError:
                r["tags"] = tags
            filtered.append(r)
        else:
            # also keep raw tags normalized for display even if filtered out? No, we drop
            continue
    return filtered

def search(db_path: Path, query: str, search_type: str = "summaries", playlist: Optional[str] = None,
           tag: Optional[str] = None, min_quality: float = 0, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Paginated search, filters applied in SQL where possible.
    - min_quality filtered BEFORE limit
    - playlist filter uses JOIN instead of N+1 queries
    - tag filtering uses exact membership after proper deserialization
    """
    if not db_path.exists():
        print(f"DB not found at {db_path}", file=sys.stderr)
        return []

    if limit <= 0:
        limit = 20
    if limit > 200:
        logger.warning(f"Limit {limit} too large, capping to 200")
        limit = 200
    offset = max(0, offset)

    results: List[Dict[str, Any]] = []

    try:
        with db_connection(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            # Match tags before pagination, using the same parser as displayed results.
            conn.create_function("has_tag", 2, lambda raw, wanted: int(wanted.lower() in parse_tags_field(raw)))

            # Build base query with JOIN for playlist to avoid N+1
            # We also apply min_quality early
            def _search_with_type(qtype: str) -> List[Dict[str, Any]]:
                sql_params: List[Any] = []
                where_clauses: List[str] = []
                join_clause = ""
                base_select = "v.*"
                fts_table = ""
                rank_order = "rank"

                if qtype == "summaries":
                    fts_table = "summaries_fts sf"
                    base_select = "v.*, s.summary, rank"
                    join_clause = "JOIN videos v ON v.video_id=sf.video_id JOIN summaries s ON s.video_id=v.video_id"
                    where_clauses.append("summaries_fts MATCH ?")
                    sql_params.append(query)
                elif qtype == "videos":
                    fts_table = "videos_fts"
                    base_select = "v.*, rank"
                    join_clause = "JOIN videos v ON v.video_id=videos_fts.video_id"
                    where_clauses.append("videos_fts MATCH ?")
                    sql_params.append(query)
                elif qtype == "transcripts":
                    fts_table = "transcripts_fts tf"
                    base_select = "v.*, t.text, rank"
                    join_clause = "JOIN transcripts t ON t.video_id=tf.video_id JOIN videos v ON v.video_id=t.video_id"
                    where_clauses.append("transcripts_fts MATCH ?")
                    sql_params.append(query)
                else:  # all
                    # try summaries first, then videos - handled outside
                    return []

                # playlist filter via JOIN
                if playlist:
                    join_clause += " JOIN playlist_videos pv ON pv.video_id=v.video_id"
                    where_clauses.append("pv.playlist_id=?")
                    sql_params.append(playlist)

                if min_quality and min_quality > 0:
                    where_clauses.append("v.quality_score >= ?")
                    sql_params.append(min_quality)
                if tag:
                    where_clauses.append("has_tag(v.tags, ?) = 1")
                    sql_params.append(tag)

                where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
                sql = f"SELECT {base_select} FROM {fts_table} {join_clause}{where_sql} ORDER BY {rank_order} LIMIT ? OFFSET ?"
                sql_params.extend([limit, offset])

                try:
                    cur.execute(sql, sql_params)
                    return [dict(r) for r in cur.fetchall()]
                except sqlite3.OperationalError as e:
                    logger.debug(f"FTS query failed for type {qtype}: {e}")
                    return []

            if search_type in ("summaries", "videos", "transcripts"):
                results = _search_with_type(search_type)
            else:  # all
                # summaries first
                results = _search_with_type("summaries")
                if not results:
                    results = _search_with_type("videos")

            # Fallback LIKE if no results
            if not results:
                logger.info(f"FTS search returned 0, fallback LIKE for query={query!r}")
                try:
                    # build fallback with same filters but LIKE
                    fallback_params: List[Any] = []
                    fallback_where: List[str] = []
                    fallback_join = ""
                    if playlist:
                        fallback_join = "JOIN playlist_videos pv ON pv.video_id=v.video_id"
                        fallback_where.append("pv.playlist_id=?")
                        fallback_params.append(playlist)
                    if min_quality and min_quality > 0:
                        fallback_where.append("v.quality_score >= ?")
                        fallback_params.append(min_quality)
                    if tag:
                        fallback_where.append("has_tag(v.tags, ?) = 1")
                        fallback_params.append(tag)

                    like_pattern = f"%{query}%"
                    if search_type == "summaries":
                        fallback_where.append("v.summary LIKE ?")
                        fallback_params.append(like_pattern)
                        base_sql = f"SELECT v.* FROM videos v {fallback_join}"
                    elif search_type == "transcripts":
                        fallback_join += " JOIN transcripts t ON t.video_id=v.video_id"
                        fallback_where.append("t.text LIKE ?")
                        fallback_params.append(like_pattern)
                        base_sql = f"SELECT v.*, t.text FROM videos v {fallback_join}"
                    else:
                        fallback_where.append("(v.title LIKE ? OR v.summary LIKE ? OR v.description LIKE ?)")
                        fallback_params.extend([like_pattern, like_pattern, like_pattern])
                        base_sql = f"SELECT v.* FROM videos v {fallback_join}"

                    where_sql = " WHERE " + " AND ".join(fallback_where) if fallback_where else ""
                    fallback_sql = base_sql + where_sql + " LIMIT ? OFFSET ?"
                    fallback_params.extend([limit, offset])
                    cur.execute(fallback_sql, fallback_params)
                    results = [dict(r) for r in cur.fetchall()]
                except sqlite3.Error as e:
                    logger.warning(f"Fallback LIKE search failed: {e}")
                    results = []

    except sqlite3.Error as e:
        logger.error(f"Search DB error: {e}")
        print(f"FTS search failed {e}, fallback LIKE", file=sys.stderr)
        # Do not return unrelated data by silently dropping the requested filters.
        return []

    # Tag filter - exact membership, not substring
    if tag:
        results = _apply_tag_filter_exact(results, tag)
    else:
        # still normalize tags for display
        for r in results:
            try:
                if isinstance(r.get("tags"), str):
                    r["tags"] = json.loads(r.get("tags") or "[]")
            except json.JSONDecodeError:
                r["tags"] = parse_tags_field(r.get("tags"))

    return results

def main() -> int:
    p = argparse.ArgumentParser(description="Search knowledgebase - summaries for RAG (refactored with pagination & exact tag matching)")
    p.add_argument("query", nargs="?", default="", help="Search terms")
    p.add_argument("--db", default=str(CONFIG.db_path), help="DB path relative")
    p.add_argument("--type", dest="search_type", default="summaries", choices=["summaries", "videos", "transcripts", "all"], help="What to search (summaries is for refined knowledge)")
    p.add_argument("--playlist", help="Filter playlist_id")
    p.add_argument("--tag", help="Filter tag (exact match, case-insensitive, fixes old substring bug)")
    p.add_argument("--min-quality", type=float, default=0, help="Minimum quality score - filtered in SQL before LIMIT")
    p.add_argument("--limit", type=int, default=20, help="Results per page (max 200)")
    p.add_argument("--offset", type=int, default=0, help="Pagination offset")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--export", help="Export to csv/json")
    p.add_argument("--rag", action="store_true", help="Export RAG files")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path

    if args.stats or (not args.query and not args.rag):
        print_stats(db_path)
        if not args.query:
            print("\nExamples:")
            print('  python search.py "RAG implementation" --type summaries --limit 20 --offset 0')
            print('  python search.py "python" --type videos --min-quality 60 --tag python')
            print('  python search.py "error handling" --type transcripts')
            print('  python search.py --stats')
            print('  python search.py --rag  # exports rag_dataset.jsonl and rag_context.md')
        return 0

    if args.rag:
        try:
            from core import export_rag_dataset
            res = export_rag_dataset(db_path.parent, db_path)
            print(f"RAG export: {res}")
            return 0 if "error" not in res else 1
        except Exception as e:
            logger.exception(f"RAG export failed: {e}")
            print(f"RAG export failed: {e}", file=sys.stderr)
            return 1

    results = search(db_path, args.query, search_type=args.search_type, playlist=args.playlist,
                     tag=args.tag, min_quality=args.min_quality, limit=args.limit, offset=args.offset)

    for i, r in enumerate(results, 1):
        idx = args.offset + i
        title = r.get('title', '')[:90]
        score = r.get('quality_score', 0) or 0
        label = r.get('quality_label', '')
        status = r.get('status', '')
        print(f"\n{idx}. [{score:.1f} {label} {status}] {title}")
        print(f"   ID: {r.get('video_id')} | Channel: {r.get('channel','')} | Views: {r.get('view_count',0)} | Summary: {'YES' if r.get('summary') else 'NO'}")
        print(f"   URL: {r.get('url','')}")
        snippet_src = r.get('summary') or r.get('text') or r.get('description') or ''
        snippet = snippet_src[:400].replace('\n', ' ')
        # only add ellipsis if truncated
        suffix = "..." if len(snippet_src) > 400 else ""
        print(f"   {snippet}{suffix}")

    if args.export and results:
        out = Path(args.export)
        if not out.is_absolute():
            out = Path.cwd() / out
        try:
            if out.suffix == ".json":
                out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                with open(out, "w", newline="", encoding="utf-8") as f:
                    keys = [k for k in results[0].keys() if k not in ("meta_json", "chapters", "quality_details")]
                    w = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
                    w.writeheader()
                    w.writerows(results)
            print(f"\nExported to {out}")
        except OSError as e:
            print(f"Export failed: {e}", file=sys.stderr)
            return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
