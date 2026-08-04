#!/usr/bin/env python3
"""
core.py - All heavy logic for yt-archive-seed
Fixed: playlist URL parsing, video ID validation, auto-playlist mode
"""
from __future__ import annotations
import json, time, re, sqlite3
from pathlib import Path
from typing import List, Dict, Callable
from urllib.parse import urlparse, parse_qs

VIDEO_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
PLAYLIST_PREFIXES = ("PL", "UU", "OL", "LL", "RD", "FL", "UL")

def _log(msg: str, cb: Callable[[str], None] | None):
    print(msg)
    if cb:
        try:
            cb(msg)
        except:
            pass

def parse_vote_count(v) -> int:
    if v is None: return 0
    if isinstance(v, int): return v
    s = str(v).strip().replace(",", "").lower()
    try:
        if s.endswith("k"): return int(float(s[:-1]) * 1000)
        if s.endswith("m"): return int(float(s[:-1]) * 1_000_000)
        return int(float(s))
    except: return 0

def _read_existing_ids_jsonl(p: Path) -> set:
    if not p.exists(): return set()
    ids=set()
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip(): continue
        try:
            j=json.loads(line)
            if j.get("video_id"): ids.add(j["video_id"])
        except: continue
    return ids

def _is_valid_video_id(vid: str) -> bool:
    if not vid: return False
    # Playlist IDs are much longer than 11 chars or start with PL etc
    if len(vid) != 11: 
        # Some YouTube IDs are 11, playlist IDs are usually 13-44
        # Reject obvious playlist IDs
        if vid.startswith(PLAYLIST_PREFIXES) and len(vid) > 11:
            return False
        # Also reject if longer than 11
        if len(vid) > 11:
            # but some video IDs can be 11 only, so reject >11
            return False
    return bool(VIDEO_ID_RE.match(vid))

def _extract_ids_from_url(url: str):
    """Returns (video_id_or_none, playlist_id_or_none)"""
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        vid = qs.get("v", [None])[0]
        plist = qs.get("list", [None])[0]
        # youtu.be short links
        if "youtu.be" in parsed.netloc and parsed.path:
            # path is /VIDEOID
            possible = parsed.path.lstrip("/").split("/")[0].split("?")[0]
            if _is_valid_video_id(possible):
                vid = possible
        # /playlist?list=...
        if "playlist" in parsed.path and plist:
            pass
        return vid, plist
    except:
        return None, None

def _yt_dlp_extract(url: str, progress_cb=None):
    import yt_dlp
    ydl_opts = {
        "extract_flat": True,
        "quiet": True,
        "skip_download": True,
        "ignoreerrors": True,
        "no_warnings": True,
        # Force yes-playlist when we want playlist
        "yes_playlist": False,
    }
    # For playlist URLs we need yes_playlist True
    if "playlist?list=" in url or "list=" in url and "watch" not in url:
        ydl_opts["yes_playlist"] = True
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)

# ---- ID FETCH ----
def get_video_infos(url: str, limit: int = 0, progress_cb=None) -> List[Dict]:
    import yt_dlp
    _log(f"Fetching video list from: {url}", progress_cb)

    # Parse URL to detect playlist intent
    v_id, p_id = _extract_ids_from_url(url)
    _log(f"Parsed URL -> video={v_id}, playlist={p_id}", progress_cb)

    # If URL contains both watch?v= and list=PL..., user almost always wants the PLAYLIST
    # e.g. https://www.youtube.com/watch?v=cRCFGZxmob0&list=PLPIwHuVy9EyOAfcctUsw2qQyUitRUKmhz
    # This is what YouTube copies when you click a video inside a playlist
    candidates_to_try = []

    if p_id:
        # Clean playlist URL
        candidates_to_try.append(f"https://www.youtube.com/playlist?list={p_id}")
    # Always try original URL as well (with yes_playlist forced for list URLs)
    candidates_to_try.append(url)

    # Also try original URL with yes_playlist=True explicitly
    if p_id:
        candidates_to_try.append(url)  # will be tried with yes_playlist

    all_infos = []
    seen = set()

    for attempt_url in candidates_to_try:
        try:
            # For playlist URLs, force yes_playlist
            is_playlist_attempt = "playlist?list=" in attempt_url or (p_id and p_id in attempt_url and "watch" in attempt_url)
            ydl_opts = {
                "extract_flat": True,
                "quiet": True,
                "skip_download": True,
                "ignoreerrors": True,
                "no_warnings": True,
                "yes_playlist": is_playlist_attempt,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(attempt_url, download=False)
                if not info:
                    continue

                # Single video case
                if "entries" not in info or info.get("entries") is None:
                    vid = info.get("id")
                    if not vid or not _is_valid_video_id(vid):
                        _log(f"Skipping non-video id: {vid}", progress_cb)
                        continue
                    if vid in seen:
                        continue
                    seen.add(vid)
                    all_infos.append({
                        "id": vid,
                        "title": info.get("title",""),
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "channel": info.get("channel") or info.get("uploader") or ""
                    })
                    # If this was a playlist attempt and we only got 1 video, it might be wrong, continue to next candidate
                    if is_playlist_attempt and len(all_infos) == 1 and p_id:
                        # But if original URL was watch+list, we want to keep trying playlist URL
                        if attempt_url == url and "watch" in url and p_id:
                            continue
                else:
                    # Playlist / channel - entries may be generator
                    for e in info.get("entries", []):
                        if not e: continue
                        vid = e.get("id")
                        if not vid: continue
                        # Filter out playlist IDs and invalid IDs
                        if not _is_valid_video_id(vid):
                            # Sometimes yt-dlp returns playlist as entry, skip
                            _log(f"Skipping invalid entry id: {vid}", progress_cb)
                            continue
                        if vid in seen:
                            continue
                        seen.add(vid)
                        all_infos.append({
                            "id": vid,
                            "title": e.get("title") or "",
                            "url": f"https://www.youtube.com/watch?v={vid}",
                            "channel": e.get("channel") or e.get("uploader") or info.get("uploader") or ""
                        })
                        if limit and len(all_infos) >= limit:
                            break
                # If we got results from a playlist attempt, stop
                if all_infos and p_id and is_playlist_attempt:
                    _log(f"Got {len(all_infos)} videos from playlist attempt {attempt_url}", progress_cb)
                    break
                # If we got results and no playlist id, stop
                if all_infos and not p_id:
                    break
        except Exception as e:
            _log(f"Attempt {attempt_url} failed: {e}", progress_cb)
            continue

    # Deduplicate and limit
    result = []
    seen2=set()
    for i in all_infos:
        if i["id"] not in seen2:
            seen2.add(i["id"])
            result.append(i)
        if limit and len(result) >= limit:
            break

    # Final safety: if user pasted watch?v=...&list=PL... and we still only got 1 video (the watch video),
    # but playlist id exists, try one more time forcing playlist extraction via playlist URL
    if p_id and len(result) == 1 and result[0]["id"] != p_id:
        # If result is the single video from the watch URL, but user likely wanted playlist,
        # we already tried playlist URL above. If that failed, keep single video.
        # Log hint
        _log(f"Note: URL contained playlist {p_id} but only 1 video found. If you wanted the full playlist, use: https://www.youtube.com/playlist?list={p_id}", progress_cb)

    # If still empty but we have v_id, return that single video
    if not result and v_id and _is_valid_video_id(v_id):
        _log(f"Falling back to single video {v_id}", progress_cb)
        return [{"id": v_id, "title": "", "url": f"https://www.youtube.com/watch?v={v_id}", "channel": ""}]

    _log(f"Found {len(result)} videos", progress_cb)
    return result

# ---- TRANSCRIPT ----
def _fetch_one_transcript(vid: str):
    if not _is_valid_video_id(vid):
        raise ValueError(f"Invalid video ID {vid} - looks like playlist ID, not video ID")
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        api = YouTubeTranscriptApi()
        t_list = api.list(vid) if hasattr(api, "list") else YouTubeTranscriptApi.list_transcripts(vid)
        transcript_obj = None
        for langs in (["en","en-US","en-GB"], ["en"], None):
            try:
                if langs is None:
                    transcript_obj = list(t_list)[0]
                else:
                    try:
                        transcript_obj = t_list.find_transcript(langs)
                    except:
                        transcript_obj = t_list.find_generated_transcript(langs)
                if transcript_obj:
                    break
            except:
                continue
        if not transcript_obj:
            raise ValueError("No transcript")
        fetched = transcript_obj.fetch()
        segs=[]
        full=[]
        for x in fetched:
            if hasattr(x,"text"):
                txt,st,du = x.text, getattr(x,"start",0), getattr(x,"duration",0)
            else:
                txt,st,du = x.get("text",""), x.get("start",0), x.get("duration",0)
            segs.append({"text":txt,"start":float(st),"duration":float(du)})
            full.append(txt)
        return " ".join(full), segs, getattr(transcript_obj,"language_code","en")
    except AttributeError:
        data = YouTubeTranscriptApi.get_transcript(vid, languages=["en","en-US"])
        segs=[{"text":d["text"],"start":d["start"],"duration":d["duration"]} for d in data]
        return " ".join(d["text"] for d in data), segs, "en"

def fetch_transcripts_bulk(video_infos: List[Dict], out_path: Path, resume=True, progress_cb=None, sleep_sec=0.6):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing=_read_existing_ids_jsonl(out_path) if resume else set()
    mode="a" if resume and out_path.exists() else "w"
    ok=skip=err=0
    with open(out_path, mode, encoding="utf-8") as f:
        for info in video_infos:
            vid=info["id"]
            if not _is_valid_video_id(vid):
                _log(f"[transcript SKIP] {vid} is not a valid video ID, skipping", progress_cb)
                err+=1
                continue
            if vid in existing:
                skip+=1
                continue
            try:
                full,segs,lang=_fetch_one_transcript(vid)
                rec={"video_id":vid,"title":info.get("title",""),"url":info.get("url",""),"channel":info.get("channel",""),"language":lang,"text":full,"segments":segs}
                f.write(json.dumps(rec,ensure_ascii=False)+"\n")
                f.flush()
                ok+=1
                _log(f"[transcript OK] {vid} - {info.get('title','')[:60]}", progress_cb)
            except Exception as e:
                rec={"video_id":vid,"title":info.get("title",""),"url":info.get("url",""),"error":str(e),"text":"","segments":[]}
                f.write(json.dumps(rec,ensure_ascii=False)+"\n")
                f.flush()
                err+=1
                _log(f"[transcript FAIL] {vid}: {e}", progress_cb)
            time.sleep(sleep_sec)
    _log(f"Transcripts done: {ok} ok, {skip} skipped, {err} errors -> {out_path}", progress_cb)

# ---- COMMENTS ----
def fetch_comments_bulk(video_ids: List[str], out_path: Path, limit_per_video=0, resume=True, progress_cb=None):
    try:
        from youtube_comment_downloader import YoutubeCommentDownloader
    except ImportError:
        _log("youtube-comment-downloader not installed, skipping comments", progress_cb)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("", encoding="utf-8")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing_vids=set()
    if resume and out_path.exists():
        for line in open(out_path, encoding="utf-8", errors="ignore"):
            try:
                j=json.loads(line)
                if j.get("video_id"): existing_vids.add(j["video_id"])
            except: continue
    downloader=YoutubeCommentDownloader()
    total=0
    with open(out_path, "a" if resume and out_path.exists() else "w", encoding="utf-8") as f:
        for vid in video_ids:
            if not _is_valid_video_id(vid):
                _log(f"[comments SKIP] {vid} not valid video ID", progress_cb)
                continue
            if resume and vid in existing_vids:
                _log(f"[comments SKIP] {vid} already done", progress_cb)
                continue
            try:
                comments=downloader.get_comments_from_url(f"https://www.youtube.com/watch?v={vid}", sort_by=0)
                c=0
                for cm in comments:
                    text=cm.get("text","")
                    if not text: continue
                    rec={"video_id":vid,"author":cm.get("author",""),"text":text,"votes":parse_vote_count(cm.get("votes")),"time":cm.get("time","")}
                    f.write(json.dumps(rec,ensure_ascii=False)+"\n")
                    total+=1; c+=1
                    if limit_per_video and c>=limit_per_video: break
                _log(f"[comments OK] {vid}: {c} comments", progress_cb)
            except Exception as e:
                _log(f"[comments FAIL] {vid}: {e}", progress_cb)
    _log(f"Comments done: {total} total -> {out_path}", progress_cb)

# ---- DB ----
def build_db(transcripts_path: Path, comments_path: Path, db_path: Path, meta_path: Path | None = None, progress_cb=None):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists(): db_path.unlink()
    conn=sqlite3.connect(db_path)
    cur=conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("CREATE TABLE transcripts(video_id TEXT PRIMARY KEY, title TEXT, url TEXT, channel TEXT, text TEXT, json TEXT)")
    cur.execute("CREATE TABLE comments(id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, author TEXT, text TEXT, likes INTEGER, time TEXT, json TEXT)")
    cur.execute("CREATE TABLE videos(video_id TEXT PRIMARY KEY, title TEXT, url TEXT, channel TEXT, meta_json TEXT)")
    cur.execute("CREATE VIRTUAL TABLE transcripts_fts USING fts5(video_id, title, text, tokenize='porter unicode61')")
    cur.execute("CREATE VIRTUAL TABLE comments_fts USING fts5(video_id, author, text, tokenize='porter unicode61')")
    meta_map={}
    if meta_path and Path(meta_path).exists():
        try:
            data=json.loads(Path(meta_path).read_text(encoding="utf-8"))
            if isinstance(data,list):
                for m in data:
                    if m.get("id"): meta_map[m["id"]]=m
        except Exception as e:
            _log(f"Meta load warning: {e}", progress_cb)
    tc=0
    if transcripts_path and transcripts_path.exists():
        batch=[]; fts_batch=[]
        for line in open(transcripts_path, encoding="utf-8", errors="ignore"):
            if not line.strip(): continue
            try:
                j=json.loads(line)
                if not j.get("text"): continue
                vid=j.get("video_id")
                if not vid or not _is_valid_video_id(vid): continue
                batch.append((vid,j.get("title",""),j.get("url",""),j.get("channel",""),j.get("text",""),json.dumps(j,ensure_ascii=False)))
                fts_batch.append((vid,j.get("title",""),j.get("text","")))
                tc+=1
                if len(batch)>=500:
                    cur.executemany("INSERT OR REPLACE INTO transcripts VALUES (?,?,?,?,?,?)",batch)
                    cur.executemany("INSERT INTO transcripts_fts VALUES (?,?,?)",fts_batch)
                    batch=[]; fts_batch=[]
            except: continue
        if batch:
            cur.executemany("INSERT OR REPLACE INTO transcripts VALUES (?,?,?,?,?,?)",batch)
            cur.executemany("INSERT INTO transcripts_fts VALUES (?,?,?)",fts_batch)
    if meta_map:
        v_batch=[(vid,m.get("title",""),m.get("url",""),m.get("channel",""),json.dumps(m,ensure_ascii=False)) for vid,m in meta_map.items() if _is_valid_video_id(vid)]
        cur.executemany("INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?)",v_batch)
    else:
        cur.execute("INSERT OR REPLACE INTO videos(video_id, title, url, channel) SELECT video_id, title, url, channel FROM transcripts")
    cc=0
    if comments_path and comments_path.exists():
        batch=[]; fts_batch=[]
        for line in open(comments_path, encoding="utf-8", errors="ignore"):
            if not line.strip(): continue
            try:
                j=json.loads(line)
                if not j.get("text"): continue
                if not _is_valid_video_id(j.get("video_id","")): continue
                likes=parse_vote_count(j.get("votes") if "votes" in j else j.get("likes"))
                batch.append((j.get("video_id",""),j.get("author",""),j.get("text",""),likes,j.get("time",""),json.dumps(j,ensure_ascii=False)))
                fts_batch.append((j.get("video_id",""),j.get("author",""),j.get("text","")))
                cc+=1
                if len(batch)>=1000:
                    cur.executemany("INSERT INTO comments(video_id, author, text, likes, time, json) VALUES (?,?,?,?,?,?)",batch)
                    cur.executemany("INSERT INTO comments_fts VALUES (?,?,?)",fts_batch)
                    batch=[]; fts_batch=[]
            except: continue
        if batch:
            cur.executemany("INSERT INTO comments(video_id, author, text, likes, time, json) VALUES (?,?,?,?,?,?)",batch)
            cur.executemany("INSERT INTO comments_fts VALUES (?,?,?)",fts_batch)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_comments_vid ON comments(video_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_comments_likes ON comments(likes DESC)")
    conn.commit(); conn.close()
    _log(f"DB built at {db_path}: {tc} transcripts, {cc} comments", progress_cb)
    return {"transcripts":tc,"comments":cc}
