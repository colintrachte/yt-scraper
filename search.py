#!/usr/bin/env python3
"""
search.py - Readable search over archive.db + jsonl
Fixes original bugs: adds URL for comments, stats, html report, csv/json export
Can be used standalone or by main.py UI
"""
import argparse, sqlite3, json, csv
from pathlib import Path

def search_fts(db_path: Path, query: str, table="transcripts", limit=20):
    if not db_path.exists():
        print(f"DB not found at {db_path}. Run main.py first or check output/archive.db")
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    fts = f"{table}_fts"
    if table not in ("transcripts","comments"):
        table = "transcripts"
    sql = f"SELECT t.*, rank FROM {fts} JOIN {table} t ON t.rowid = {fts}.rowid WHERE {fts} MATCH ? ORDER BY rank LIMIT ?"
    try:
        cur.execute(sql, (query, limit))
        rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"FTS error ({e}), falling back to LIKE search")
        cur.execute(f"SELECT * FROM {table} WHERE text LIKE ? LIMIT ?", (f"%{query}%", limit))
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        if not r.get("url") and r.get("video_id"):
            r["url"] = f"https://www.youtube.com/watch?v={r['video_id']}"
    return rows

def print_stats(db_path: Path):
    if not db_path.exists():
        print(f"No DB at {db_path}")
        return
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    print(f"\n=== {db_path} ===")
    for tbl in ["videos","transcripts","comments"]:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            cnt = cur.fetchone()[0]
            print(f"{tbl:15} : {cnt}")
        except Exception as e:
            print(f"{tbl:15} : (error {e})")
    try:
        cur.execute("SELECT video_id, title, channel FROM videos LIMIT 5")
        print("\nSample videos:")
        for vid, title, ch in cur.fetchall():
            print(f"  - [{vid}] {title[:70]} ({ch})")
    except:
        pass
    conn.close()
    for p in [db_path.parent / "transcripts.jsonl", db_path.parent / "comments.jsonl"]:
        if p.exists():
            lines = sum(1 for _ in open(p, encoding="utf-8", errors="ignore"))
            size_mb = p.stat().st_size / 1024 / 1024
            print(f"{p.name:15} : {lines} lines, {size_mb:.1f} MB (readable in Notepad/VSCode)")

def export_html(results, query, out_path: Path):
    html = f"<html><head><meta charset='utf-8'><title>Search: {query}</title><style>body{{font-family:sans-serif;max-width:900px;margin:20px auto}} .r{{border-bottom:1px solid #ddd;padding:12px 0}} a{{color:#0b6}} .s{{color:#555;font-size:13px}}</style></head><body><h2>Search: {query} ({len(results)} results)</h2>"
    for i,r in enumerate(results,1):
        snippet = (r.get("text") or "")[:500].replace("<","&lt;")
        html += f'<div class="r"><b>{i}. [{r.get("video_id")}]</b> {r.get("title","")}<br><a href="{r.get("url","")}" target="_blank">{r.get("url","")}</a><div class="s">...{snippet}...</div></div>'
    html += "</body></html>"
    out_path.write_text(html, encoding="utf-8")
    print(f"HTML report -> {out_path} (double-click to open)")

def main():
    p = argparse.ArgumentParser(description="Search archive.db - now readable!")
    p.add_argument("query", nargs="?", default="", help="search terms (FTS5: supports AND, OR, * wildcard)")
    p.add_argument("--db", default="output/archive.db")
    p.add_argument("--in", dest="table", default="transcripts", choices=["transcripts","comments"])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--stats", action="store_true", help="Show readable DB stats")
    p.add_argument("--export", help="Export results to csv or json file")
    p.add_argument("--html", help="Export results to HTML report")
    args = p.parse_args()
    db = Path(args.db)
    if args.stats or not args.query:
        print_stats(db)
        if not args.query:
            print("\nUsage: python search.py \"my keyword\" --in transcripts")
            print("       python search.py \"my keyword\" --in comments")
            print("       python search.py --stats")
            return
    if args.query:
        res = search_fts(db, args.query, args.table, args.limit)
        for i,r in enumerate(res,1):
            snippet = (r.get("text") or "")[:300].replace("\n"," ")
            print(f"\n{i}. [{r.get('video_id')}] {r.get('title','')[:80]}")
            print(f"   {r.get('url','')}")
            print(f"   ...{snippet}...")
        if args.export and res:
            out = Path(args.export)
            if out.suffix == ".json":
                out.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                with open(out,"w",newline="",encoding="utf-8") as f:
                    keys = [k for k in res[0].keys() if k != "json"]
                    w = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
                    w.writeheader(); w.writerows(res)
            print(f"\nExported {len(res)} rows to {out}")
        if args.html and res:
            export_html(res, args.query, Path(args.html))

if __name__ == "__main__":
    main()
