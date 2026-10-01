"""Command-line interface.

  ytrag ingest --playlist URL [--cookies cookies.txt] [--whisper]
  ytrag ingest --ids ids.txt          # one video id or URL per line
  ytrag ingest --takeout watch-history.json
  ytrag search "query" [--mode hybrid|lexical|semantic] [-n 10]
  ytrag ask "question" [--model llama3.1]
  ytrag stats
"""
import argparse
import json
import re
import sys
import time

from . import config, db as dbmod, fetch, index as idx


def _resolve_id(s: str) -> str | None:
    s = s.strip()
    if not s:
        return None
    m = re.search(r"(?:v=|youtu\.be/|/watch\?.*v=)([A-Za-z0-9_-]{11})", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    return None


def _ingest_one(db, meta, langs, delay, use_whisper):
    vid = meta["video_id"]
    if dbmod.already_done(db, vid):
        print(f"  skip (done): {meta.get('title')}")
        return "skip"
    try:
        snippets, lang, gen = fetch.fetch_transcript(vid, langs)
        idx.archive_raw(meta, snippets)
        n = idx.index_video(db, meta, snippets,
                            source="captions", lang=lang, is_generated=gen)
        print(f"  ok ({n} chunks): {meta.get('title')}")
        return "ok"
    except fetch.DISABLED as e:
        if use_whisper:
            try:
                from . import whisper_fallback
                print(f"  captions off -> whisper: {meta.get('title')}")
                snippets = whisper_fallback.transcribe(vid)
                idx.archive_raw(meta, snippets)
                idx.index_video(db, meta, snippets,
                                source="whisper", lang="en", is_generated=True)
                return "ok"
            except Exception as we:  # noqa
                idx.record_failure(db, meta, "error", f"whisper: {we}")
                print(f"  whisper failed: {we}")
                return "error"
        idx.record_failure(db, meta, "disabled", str(e))
        print(f"  disabled: {meta.get('title')}")
        return "disabled"
    except fetch.BLOCKED as e:
        idx.record_failure(db, meta, "blocked", str(e))
        print(f"  BLOCKED (PoToken/IP): {meta.get('title')}")
        return "blocked"
    except Exception as e:  # noqa
        idx.record_failure(db, meta, "error", str(e))
        print(f"  error: {e}")
        return "error"
    finally:
        time.sleep(delay)


def cmd_ingest(args):
    db = dbmod.connect(); dbmod.init_schema(db)
    langs = args.lang or config.PREFERRED_LANGS
    metas, playlist_title = [], "manual"

    if args.playlist:
        vids, playlist_title = fetch.enumerate_playlist(args.playlist, args.cookies)
        metas = vids
    elif args.ids:
        for line in open(args.ids, encoding="utf-8"):
            vid = _resolve_id(line)
            if vid:
                metas.append({"video_id": vid,
                              "url": f"https://www.youtube.com/watch?v={vid}",
                              "title": vid})
    elif args.takeout:
        data = json.load(open(args.takeout, encoding="utf-8"))
        for item in data:
            url = item.get("titleUrl", "")
            vid = _resolve_id(url)
            if vid:
                metas.append({"video_id": vid, "url": url,
                              "title": item.get("title", vid)})
    else:
        print("Provide --playlist, --ids, or --takeout", file=sys.stderr)
        return 2

    for m in metas:
        m.setdefault("playlist", playlist_title)
    print(f"Ingesting {len(metas)} videos from '{playlist_title}'")
    counts = {}
    for i, m in enumerate(metas, 1):
        print(f"[{i}/{len(metas)}]", end=" ")
        r = _ingest_one(db, m, langs, args.delay, args.whisper)
        counts[r] = counts.get(r, 0) + 1
    print("Summary:", counts)


def cmd_search(args):
    from .search import hybrid_search
    db = dbmod.connect(); dbmod.init_schema(db)
    results = hybrid_search(db, args.query, top_k=args.n, mode=args.mode)
    if not results:
        print("No results."); return
    for i, r in enumerate(results, 1):
        snippet = r["text"][:220].replace("\n", " ")
        print(f"\n[{i}] {r['title']}  ({r['playlist']})")
        print(f"    {r['deep_link']}")
        print(f"    {snippet}...")


def cmd_ask(args):
    from .search import hybrid_search
    from .answer import synthesize
    db = dbmod.connect(); dbmod.init_schema(db)
    results = hybrid_search(db, args.question, top_k=args.n)
    if not results:
        print("No context found."); return
    print(synthesize(args.question, results, model=args.model))
    print("\nSources:")
    for i, r in enumerate(results, 1):
        print(f"  [{i}] {r['title']} -> {r['deep_link']}")


def cmd_stats(args):
    db = dbmod.connect(); dbmod.init_schema(db)
    v = db.execute("SELECT status, COUNT(*) c FROM videos GROUP BY status").fetchall()
    ch = db.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    pl = db.execute("SELECT playlist, COUNT(*) c FROM videos GROUP BY playlist").fetchall()
    print("Videos by status:", {r["status"]: r["c"] for r in v})
    print("Total chunks:", ch)
    print("By playlist:", {r["playlist"]: r["c"] for r in pl})


def main(argv=None):
    p = argparse.ArgumentParser(prog="ytrag")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="fetch + index transcripts")
    src = ing.add_mutually_exclusive_group(required=True)
    src.add_argument("--playlist"); src.add_argument("--ids")
    src.add_argument("--takeout")
    ing.add_argument("--cookies")
    ing.add_argument("--lang", nargs="+")
    ing.add_argument("--delay", type=float, default=config.DEFAULT_DELAY)
    ing.add_argument("--whisper", action="store_true",
                     help="local faster-whisper fallback for caption-off videos")
    ing.set_defaults(func=cmd_ingest)

    se = sub.add_parser("search", help="hybrid search over transcripts")
    se.add_argument("query")
    se.add_argument("--mode", choices=["hybrid", "lexical", "semantic"],
                    default="hybrid")
    se.add_argument("-n", type=int, default=config.FINAL_TOPK)
    se.set_defaults(func=cmd_search)

    ak = sub.add_parser("ask", help="RAG answer via local Ollama")
    ak.add_argument("question")
    ak.add_argument("--model", default="llama3.1")
    ak.add_argument("-n", type=int, default=config.FINAL_TOPK)
    ak.set_defaults(func=cmd_ask)

    st = sub.add_parser("stats"); st.set_defaults(func=cmd_stats)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
