#!/usr/bin/env python3
"""
main.py - Refactored orchestration + UX-rich server
- Stage-based progress bar, ETA, counts
- Clickable search with highlighting, pagination, toasts, localStorage
- One-click actions, quality tooltips, validation, retry
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import re
import sys
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional, Callable

def _ensure_venv():
    base = Path(__file__).parent
    venv_win = base / "venv" / "Scripts" / "python.exe"
    venv_unix = base / "venv" / "bin" / "python"
    venv_py = venv_win if os.name == "nt" else venv_unix
    if venv_py.exists():
        try:
            cur = Path(sys.executable).resolve()
            tgt = venv_py.resolve()
            if cur != tgt and "venv" not in str(cur).lower():
                print(f"[bootstrap] Relaunching with {venv_py}")
                os.execv(str(tgt), [str(tgt)] + sys.argv)
        except Exception as e:
            print(f"[bootstrap] venv relaunch failed: {e}")

_ensure_venv()

REQUIRED = ["yt-dlp", "youtube-transcript-api", "youtube-comment-downloader", "tqdm", "requests"]
IMPORT_MAP = {"yt-dlp": "yt_dlp", "youtube-transcript-api": "youtube_transcript_api", "youtube-comment-downloader": "youtube_comment_downloader", "tqdm": "tqdm", "requests": "requests"}

def _missing() -> List[str]:
    miss = []
    for pkg in REQUIRED:
        try:
            __import__(IMPORT_MAP[pkg])
        except ImportError:
            miss.append(pkg)
    return miss

def _install(pkgs: List[str], log_fn=print) -> bool:
    if not pkgs:
        return True
    log_fn(f"[deps] Installing {pkgs}")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + pkgs)
        return True
    except Exception as e:
        log_fn(f"[deps] Failed {e}")
        return False

if _missing():
    print(f"Missing {_missing()} -> auto-install")
    _install(_missing())

import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    from core import (
        get_playlist_data, fetch_videos_full_metadata, fetch_transcripts_bulk, fetch_comments_bulk, build_db,
        update_video_user_score, update_video_summary, get_video_copy_data, export_rag_dataset,
        batch_summarize, generate_summary_for_video, test_llm_connection, CONFIG, db_connection,
        is_valid_video_id
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from core import (
        get_playlist_data, fetch_videos_full_metadata, fetch_transcripts_bulk, fetch_comments_bulk, build_db,
        update_video_user_score, update_video_summary, get_video_copy_data, export_rag_dataset,
        batch_summarize, generate_summary_for_video, test_llm_connection, CONFIG, db_connection,
        is_valid_video_id
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ytkb.main")
logger.setLevel(logging.INFO)

STAGES = ["discovery", "metadata", "transcripts", "comments", "database", "complete"]

STATE = {
    "running": False,
    "logs": [],
    "stats": {},
    "error": None,
    "llm_logs": [],
    "cancelled": False,
    "stage": "idle",
    "stage_progress": {"current": 0, "total": 0, "percent": 0, "message": ""},
    "eta_seconds": 0,
    "counts": {"discovered": 0, "playlists": 0, "mappings": 0, "metadata_ok": 0, "transcripts_ok": 0, "comments_ok": 0},
    "stage_times": {}
}
LOCK = threading.Lock()
CANCEL_EVENT = threading.Event()
_stage_start: Dict[str, float] = {}

def log(msg: str) -> None:
    logger.info(msg)
    with LOCK:
        STATE["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(STATE["logs"]) > 1000:
            STATE["logs"] = STATE["logs"][-1000:]

def llm_log(msg: str) -> None:
    logger.info(msg)
    with LOCK:
        STATE["llm_logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(STATE["llm_logs"]) > 500:
            STATE["llm_logs"] = STATE["llm_logs"][-500:]
    log(msg)

def is_cancelled() -> bool:
    return CANCEL_EVENT.is_set() or STATE.get("cancelled", False)

def set_stage(name: str, current: int = 0, total: int = 0, message: str = "") -> None:
    with LOCK:
        if name != STATE.get("stage"):
            _stage_start[name] = time.time()
            STATE["stage_times"][name] = {"started": time.time()}
        STATE["stage"] = name
        percent = int((current / total * 100) if total > 0 else (100 if name == "complete" else 0))
        if name == "complete":
            percent = 100
        STATE["stage_progress"] = {"current": current, "total": total, "percent": percent, "message": message}
        # ETA
        if current > 0 and total > 0:
            elapsed = time.time() - _stage_start.get(name, time.time())
            rate = current / elapsed if elapsed > 0 else 0
            remaining = total - current
            eta = remaining / rate if rate > 0 else 0
            STATE["eta_seconds"] = int(eta)
        else:
            STATE["eta_seconds"] = 0

def update_counts(**kwargs):
    with LOCK:
        for k, v in kwargs.items():
            STATE["counts"][k] = v

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_url_input(urls_input: str) -> List[str]:
    parts = re.split(r'[\n,\r]+', urls_input.strip())
    url_list: List[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        for sub in p.split():
            if sub.startswith("http"):
                url_list.append(sub.strip())
    if not url_list and urls_input.strip().startswith("http"):
        url_list = [urls_input.strip()]
    return url_list

def validate_urls(urls: List[str]) -> Tuple[List[str], List[str]]:
    valid = []
    invalid = []
    for u in urls:
        if not u.startswith("http"):
            invalid.append(u)
            continue
        if "youtube.com" not in u and "youtu.be" not in u:
            invalid.append(u)
            continue
        valid.append(u)
    return valid, invalid

def discover_videos(url_list: List[str], limit: int, progress_cb: Callable[[str], None]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    all_videos_dict: Dict[str, Dict] = {}
    all_playlists_dict: Dict[str, Dict] = {}
    all_mappings: List[Dict] = []

    set_stage("discovery", 0, len(url_list), f"Discovering {len(url_list)} URLs")
    for idx, u in enumerate(url_list, 1):
        if is_cancelled():
            log("Cancelled during discovery")
            break
        set_stage("discovery", idx, len(url_list), f"Fetching {u[:60]}")
        vids, pls, maps = get_playlist_data(u, limit=limit, progress_cb=progress_cb, cancel_check=is_cancelled)
        for v in vids:
            if v["id"] not in all_videos_dict:
                all_videos_dict[v["id"]] = v
        for p in pls:
            if p["playlist_id"] not in all_playlists_dict:
                all_playlists_dict[p["playlist_id"]] = p
        all_mappings.extend(maps)
        update_counts(discovered=len(all_videos_dict), playlists=len(all_playlists_dict), mappings=len(all_mappings))

    videos = list(all_videos_dict.values())
    playlists = list(all_playlists_dict.values())
    if limit and len(videos) > limit:
        videos = videos[:limit]
    return videos, playlists, all_mappings

def persist_discovery(videos: List[Dict], playlists: List[Dict], mappings: List[Dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        (out_dir / "videos_meta.json").write_text(json.dumps(videos, indent=2, ensure_ascii=False), encoding="utf-8")
        (out_dir / "playlists.jsonl").write_text("\n".join([json.dumps(p, ensure_ascii=False) for p in playlists]), encoding="utf-8")
        (out_dir / "playlist_videos.jsonl").write_text("\n".join([json.dumps(m, ensure_ascii=False) for m in mappings]), encoding="utf-8")
        (out_dir / "videos.txt").write_text("\n".join([v["id"] for v in videos]), encoding="utf-8")
    except OSError as e:
        logger.error(f"Failed to persist discovery: {e}")
        raise

def fetch_all_metadata(videos: List[Dict], out_dir: Path, skip: bool, cb: Callable[[str], None]) -> None:
    if skip:
        log("Skipping full metadata")
        set_stage("metadata", 0, 0, "Skipped")
        return
    set_stage("metadata", 0, len(videos), "Starting metadata")
    counter = {"done": 0, "ok": 0}
    def wrapped(msg):
        cb(msg)
        if "[meta OK]" in msg or "[meta FAIL]" in msg:
            counter["done"] += 1
            if "[meta OK]" in msg:
                counter["ok"] += 1
            set_stage("metadata", counter["done"], len(videos), msg)
            update_counts(metadata_ok=counter["ok"])
    fetch_videos_full_metadata([v["id"] for v in videos], out_path=out_dir / "videos_full.jsonl", progress_cb=wrapped, resume=True, cancel_check=is_cancelled)

def fetch_all_transcripts(videos: List[Dict], out_dir: Path, skip: bool, cb: Callable[[str], None]) -> None:
    if skip:
        log("Skipping transcripts")
        set_stage("transcripts", 0, 0, "Skipped")
        return
    set_stage("transcripts", 0, len(videos), "Starting transcripts")
    counter = {"done": 0, "ok": 0}
    def wrapped(msg):
        cb(msg)
        if "[transcript OK]" in msg or "[transcript FAIL]" in msg:
            counter["done"] += 1
            if "[transcript OK]" in msg:
                counter["ok"] += 1
            set_stage("transcripts", counter["done"], len(videos), msg)
            update_counts(transcripts_ok=counter["ok"])
    fetch_transcripts_bulk(videos, out_path=out_dir / "transcripts.jsonl", resume=True, progress_cb=wrapped, cancel_check=is_cancelled)

def fetch_all_comments(videos: List[Dict], out_dir: Path, skip: bool, cb: Callable[[str], None]) -> None:
    if skip:
        log("Skipping comments")
        set_stage("comments", 0, 0, "Skipped")
        return
    set_stage("comments", 0, len(videos), "Starting comments")
    counter = {"done": 0}
    def wrapped(msg):
        cb(msg)
        if "[comments OK]" in msg or "[comments FAIL]" in msg:
            counter["done"] += 1
            set_stage("comments", counter["done"], len(videos), msg)
            update_counts(comments_ok=counter["done"])
    fetch_comments_bulk([v["id"] for v in videos], out_path=out_dir / "comments.jsonl", resume=True, progress_cb=wrapped, cancel_check=is_cancelled)

def build_database(out_dir: Path, cb: Callable[[str], None]) -> Dict[str, Any]:
    set_stage("database", 0, 1, "Building database")
    result = build_db(out_dir=out_dir, db_path=out_dir / "archive.db", progress_cb=cb, cancel_check=is_cancelled)
    set_stage("database", 1, 1, "Database built")
    with LOCK:
        STATE["stats"] = result
        STATE["stats"]["out_dir"] = str(out_dir.resolve())
        STATE["stats"]["db_path"] = str((out_dir / "archive.db").resolve())
    return result

def archive_job(urls_input: str, limit: int = 0, skip_comments: bool = False, skip_transcripts: bool = False,
                skip_metadata: bool = False, out_dir: Path = Path("output")) -> None:
    try:
        CANCEL_EVENT.clear()
        with LOCK:
            STATE["running"] = True
            STATE["logs"] = []
            STATE["stats"] = {}
            STATE["error"] = None
            STATE["cancelled"] = False
            STATE["stage"] = "idle"
            STATE["eta_seconds"] = 0
            STATE["counts"] = {"discovered": 0, "playlists": 0, "mappings": 0, "metadata_ok": 0, "transcripts_ok": 0, "comments_ok": 0}
            _stage_start.clear()

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        log(f"Output dir: {out_dir.resolve()}")

        miss = _missing()
        if miss:
            log(f"Missing {miss}, installing...")
            if not _install(miss, log_fn=log):
                log("Install failed")
                return

        url_list = parse_url_input(urls_input)
        if not url_list:
            log("No valid URLs")
            set_stage("idle")
            return

        valid, invalid = validate_urls(url_list)
        if invalid:
            log(f"Invalid URLs filtered: {invalid}")
        if not valid:
            log("No valid YouTube URLs after validation")
            set_stage("idle")
            return
        url_list = valid

        log(f"Processing {len(url_list)} URL(s)")
        def cb(m): log(m)

        videos, playlists, mappings = discover_videos(url_list, limit, cb)
        log(f"Unique: {len(videos)} videos, {len(playlists)} playlists, {len(mappings)} mappings")
        if not videos:
            log("No videos found")
            set_stage("idle")
            return

        persist_discovery(videos, playlists, mappings, out_dir)

        fetch_all_metadata(videos, out_dir, skip_metadata, cb)
        if is_cancelled():
            log("Cancelled after metadata")
            set_stage("idle")
            return

        fetch_all_transcripts(videos, out_dir, skip_transcripts, cb)
        if is_cancelled():
            log("Cancelled after transcripts")
            set_stage("idle")
            return

        fetch_all_comments(videos, out_dir, skip_comments, cb)
        if is_cancelled():
            log("Cancelled after comments")
            set_stage("idle")
            return

        result = build_database(out_dir, cb)
        set_stage("complete", 1, 1, f"DONE {result.get('videos')} videos")

        log(f"DONE! DB at {out_dir / 'archive.db'} avgQ {result.get('avg_quality')} failures {result.get('failures')}")
        log("Next: Review flagged failures, adjust scores, copy transcripts/comments for AI, paste summaries back, or use local LLM batch summarize.")

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        log(f"ERROR: {e}\n{err}")
        logger.exception("archive_job failed")
        with LOCK:
            STATE["error"] = str(e)
        set_stage("idle")
    finally:
        with LOCK:
            STATE["running"] = False
        CANCEL_EVENT.clear()

def remove_id_from_jsonl(path: Path, key: str, vid: str):
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        kept = []
        for line in lines:
            try:
                j = json.loads(line)
                if j.get(key) != vid:
                    kept.append(line)
            except json.JSONDecodeError:
                continue
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    except OSError as e:
        logger.warning(f"Failed to remove {vid} from {path}: {e}")

HTML_PAGE = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>yt knowledgebase - refinement + local LLM</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--border:#232830;--text:#e6e8eb;--muted:#9aa3b2;--accent:#7c5cff;--accent2:#2ee6a8;--accent3:#ff7cc8;--warn:#f5c542;--err:#ff6b6b;--good:#22c55e;--ok:#eab308;--low:#ef4444}
*{box-sizing:border-box}body{margin:0;font-family:ui-sans,system-ui,Segoe UI,Roboto;background:var(--bg);color:var(--text);line-height:1.4}
.wrap{max-width:1320px;margin:0 auto;padding:20px}
h1{font-size:26px;margin:0} h2{font-size:18px;margin:0 0 10px} h3{font-size:15px;margin:0 0 8px}
.sub{color:var(--muted);margin:6px 0 14px;font-size:13px}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px;margin-bottom:12px;position:relative}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center} .grow{flex:1;min-width:200px}
textarea,input,select{width:100%;background:#0e1117;border:1px solid var(--border);color:var(--text);padding:10px 12px;border-radius:8px;font-size:13px;transition:border .2s}
textarea:focus,input:focus,select:focus{outline:none;border-color:var(--accent)}
textarea{min-height:80px} .small{font-size:12px;color:var(--muted)}
.btn{background:var(--accent);color:white;border:0;padding:10px 14px;border-radius:8px;font-weight:600;cursor:pointer;font-size:13px;transition:all .15s}
.btn:hover{filter:brightness(1.1)} .btn:disabled{opacity:.5;cursor:not-allowed} .btn.secondary{background:#232836} .btn.green{background:#1a7a5a} .btn.warn{background:#8a6d00} .btn.red{background:#7a1a1a} .btn.ghost{background:transparent;border:1px solid var(--border)}
.log{background:#0b0e14;border:1px solid var(--border);border-radius:8px;padding:10px;height:260px;overflow:auto;font-family:monospace;font-size:11px;white-space:pre-wrap}
.badge{display:inline-block;background:#1f2330;border:1px solid var(--border);padding:3px 8px;border-radius:12px;font-size:11px;color:var(--muted);margin:1px}
.badge.ok{background:#0f2a1f;color:var(--accent2)} .badge.fail{background:#2a0f0f;color:var(--err)} .badge.warn{background:#2a230f;color:var(--warn)} .badge.info{background:#101a2a;color:#60a5fa}
.video-card{border:1px solid var(--border);border-radius:12px;padding:12px;margin-bottom:10px;background:#12151d;transition:border .2s,transform .1s}
.video-card:hover{border-color:#2a2f40;transform:translateY(-1px)}
.video-card.has-summary{border-color:#1a7a5a}
.video-card .actions{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
.video-card .actions .btn{padding:5px 9px;font-size:11px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:1100px){.grid{grid-template-columns:1fr}}
.kbd{background:#232836;padding:2px 5px;border-radius:4px;font-size:10px}
.tabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
.tab{padding:8px 14px;border-radius:8px;background:#1a1e2a;border:1px solid var(--border);cursor:pointer;font-size:13px;transition:all .15s}
.tab.active{background:var(--accent);color:white}
.hidden{display:none !important}
/* Progress */
.progress-wrap{margin:10px 0}
.stage-bar{display:flex;gap:6px;margin-bottom:8px}
.stage-step{flex:1;text-align:center;padding:6px 4px;border-radius:8px;background:#1a1e2a;border:1px solid var(--border);font-size:11px;position:relative;overflow:hidden}
.stage-step.active{background:var(--accent);color:white;border-color:var(--accent)}
.stage-step.done{background:#0f2a1f;color:var(--accent2);border-color:#1a7a5a}
.stage-step .dot{width:18px;height:18px;border-radius:50%;background:rgba(255,255,255,.15);display:inline-flex;align-items:center;justify-content:center;margin-right:4px;font-size:10px}
.stage-step.active .dot{background:white;color:var(--accent)}
.progress-track{height:8px;background:#0e1117;border-radius:4px;overflow:hidden;border:1px solid var(--border)}
.progress-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .4s;width:0%}
.eta{font-size:11px;color:var(--muted);margin-top:4px}
/* Toasts */
#toastContainer{position:fixed;bottom:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none}
.toast{pointer-events:auto;min-width:260px;max-width:380px;background:#1a1e2a;border:1px solid var(--border);border-radius:10px;padding:10px 12px;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.4);animation:slideIn .25s ease;position:relative}
.toast.success{border-color:#1a7a5a} .toast.error{border-color:var(--err)} .toast.info{border-color:#2a3a5a} .toast.warn{border-color:#8a6d00}
@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}
.toast .close{position:absolute;top:6px;right:8px;background:transparent;border:0;color:var(--muted);cursor:pointer}
/* Search */
.search-result{border:1px solid var(--border);border-radius:10px;padding:10px;margin-bottom:8px;background:#12151d}
.search-result .meta{font-size:11px;color:var(--muted)}
.search-result mark{background:var(--warn);color:#000;padding:0 2px;border-radius:3px}
.match-badge{font-size:10px;padding:2px 6px;border-radius:8px;background:#1f2330;border:1px solid var(--border);margin-right:4px}
.match-badge.summary{background:#0f2a1f;color:var(--accent2)} .match-badge.title{background:#101a2a;color:#60a5fa} .match-badge.transcript{background:#2a1a0f;color:#fbbf24} .match-badge.tags{background:#1a0f2a;color:#c084fc}
.pagination{display:flex;gap:6px;align-items:center;margin-top:10px}
.empty-state{text-align:center;padding:24px;color:var(--muted)}
.empty-state b{color:var(--text)}
.quality-bar{height:6px;background:#0e1117;border-radius:3px;overflow:hidden;margin-top:4px}
.quality-fill{height:100%;transition:width .3s}
.q-excellent{background:var(--good)} .q-good{background:#84cc16} .q-ok{background:var(--ok)} .q-low{background:#f97316} .q-poor{background:var(--low)}
.tooltip{position:relative;display:inline-block}
.tooltip .tip{visibility:hidden;position:absolute;z-index:10;bottom:125%;left:50%;transform:translateX(-50%);background:#0b0e14;border:1px solid var(--border);padding:8px;border-radius:8px;font-size:11px;width:260px;white-space:pre-wrap;box-shadow:0 4px 12px rgba(0,0,0,.4)}
.tooltip:hover .tip{visibility:visible}
.collapsible{border:1px solid var(--border);border-radius:8px;margin-top:8px}
.collapsible-header{padding:8px 12px;cursor:pointer;background:#1a1e2a;border-radius:8px;display:flex;justify-content:space-between;align-items:center}
.collapsible-body{padding:10px;display:none}
.collapsible.open .collapsible-body{display:block}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:10000}
.modal.open{display:flex}
.modal-box{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;max-width:720px;width:90%;max-height:80vh;overflow:auto}
</style>
</head>
<body>
<div class="wrap">
<h1>📚 yt knowledgebase — refinement + local LLM</h1>
<div class="sub">Download → Flag failures → Score → Adjust → Copy transcript/comments → AI summary → Search summaries → RAG export. Settings persist locally.</div>

<div class="tabs">
<div class="tab active" data-tab="archive">1. Archive</div>
<div class="tab" data-tab="review">2. Review & Score</div>
<div class="tab" data-tab="llm">3. Local LLM</div>
<div class="tab" data-tab="search">4. Search & RAG</div>
</div>

<!-- ARCHIVE TAB -->
<div id="tab-archive" class="tab-pane">
<div class="card">
<label>YOUTUBE URLS (one per line) <span id="urlValidation" class="small" style="margin-left:8px"></span></label>
<textarea id="url" placeholder="https://www.youtube.com/playlist?list=PL...
https://www.youtube.com/@Channel/videos"></textarea>
<div class="row" style="margin-top:8px">
<div style="width:100px"><label>LIMIT</label><input id="limit" type="number" value="0" min="0"/></div>
<div style="width:140px"><label>OUT DIR</label><input id="outdir" type="text" value="output"/></div>
<label><input id="skipMeta" type="checkbox"/> Skip metadata</label>
<label><input id="skipT" type="checkbox"/> Skip transcripts</label>
<label><input id="skipC" type="checkbox"/> Skip comments</label>
<button id="go" class="btn">▶ Download</button>
<button id="cancel" class="btn red hidden">⏹ Cancel</button>
</div>
<div id="inlineUrlError" class="small" style="color:var(--err);margin-top:6px"></div>
</div>

<div class="card" id="progressCard">
<div class="row" style="justify-content:space-between"><strong>Progress</strong><span id="stats" class="badge">idle</span><button class="btn ghost" style="padding:4px 8px;font-size:11px" onclick="document.getElementById('log').textContent=''">Clear log</button></div>

<div class="progress-wrap">
<div class="stage-bar" id="stageBar"></div>
<div class="progress-track"><div class="progress-fill" id="progressFill"></div></div>
<div class="row" style="justify-content:space-between;margin-top:6px">
<span class="eta" id="stageDetail">Waiting...</span>
<span class="eta" id="eta">ETA: --</span>
<span class="eta" id="counts">0 videos</span>
</div>
</div>

<div id="log" class="log">Ready. Paste URLs to start. Settings are saved automatically.</div>
</div>
</div>

<!-- REVIEW TAB -->
<div id="tab-review" class="tab-pane hidden">
<div class="grid">
<div class="card">
<div class="row" style="justify-content:space-between"><h3>🔍 Filters</h3><button class="btn ghost" style="padding:4px 8px" onclick="saveFilterSettings()">Save</button></div>
<div class="row">
<select id="f_status" style="flex:1"><option value="">All status</option><option value="ok">ok</option><option value="no_transcript">no_transcript (needs manual watch)</option><option value="needs_review">needs_review</option><option value="short_transcript">short_transcript</option></select>
<select id="f_has_summary" style="flex:1"><option value="">All summaries</option><option value="yes">Has summary</option><option value="no">No summary</option></select>
<select id="f_sort" style="flex:1"><option value="quality">Sort: quality</option><option value="user_score">Sort: your score</option><option value="views">Sort: views</option><option value="date">Sort: date</option><option value="duration">Sort: duration</option></select>
</div>
<div class="row" style="margin-top:6px">
<input id="f_minq" type="number" placeholder="min quality 0-100" style="flex:1"/>
<button class="btn secondary" onclick="loadVideos()">Apply</button>
<button class="btn ghost" onclick="resetFilters()">Reset</button>
</div>
<div id="videoList" style="margin-top:12px;max-height:700px;overflow:auto"></div>
</div>
<div class="card">
<div id="selectedInfo" class="small">Select a video to see details, quality breakdown, and actions.</div>
<div id="selectedActions" class="hidden">
<div class="row" style="margin-top:10px">
<button class="btn secondary" style="font-size:11px" onclick="copyField('transcript')">Copy transcript</button>
<button class="btn secondary" style="font-size:11px" onclick="copyField('comments')">Copy comments</button>
<button class="btn green" style="font-size:11px" onclick="copyField('combined')">Copy for AI</button>
</div>
<div style="margin-top:10px">
<label>Your Score (0-100, overrides auto)</label>
<div class="row"><input id="userScore" type="number" min="0" max="100" placeholder="e.g. 85"/><button class="btn secondary" onclick="saveScore()">Save Score</button></div>
<label style="margin-top:8px">Your Notes</label>
<textarea id="userNotes" placeholder="Why this is useful..."></textarea>
</div>
<div style="margin-top:10px">
<label>Summary (paste from free AI or local LLM, searchable, used for RAG)</label>
<textarea id="summary" style="min-height:140px" placeholder="SUMMARY: ...&#10;KEY POINTS:&#10;- ...&#10;TOOLS:&#10;- ..."></textarea>
<div class="row" style="margin-top:6px">
<button class="btn green" onclick="saveSummary()">💾 Save Summary</button>
<button class="btn secondary" onclick="summarizeSingle()">🤖 Local LLM</button>
</div>
<div class="small" style="margin-top:6px">Workflow: Copy for AI → Paste into ChatGPT/Claude/Ollama → Copy summary → Paste here → Save.</div>
</div>
<div id="copyStatus" class="small" style="margin-top:8px;color:var(--accent2)"></div>
<div style="margin-top:12px">
<h3>🚩 Failures flagged for manual review</h3>
<div class="row"><button class="btn secondary" onclick="loadFailures()">Load failures</button><button class="btn ghost" onclick="retryAllFailures()">Retry all</button></div>
<div id="failures" style="margin-top:8px;max-height:240px;overflow:auto" class="small"></div>
</div>
</div>
</div>
</div>
</div>

<!-- LLM TAB -->
<div id="tab-llm" class="tab-pane hidden">
<div class="grid">
<div class="card">
<h3>🤖 Local LLM Settings</h3>
<div class="small" style="margin-bottom:8px">No cloud. Runs on your machine. Ollama <span class="kbd">http://localhost:11434</span>, LM Studio <span class="kbd">http://localhost:1234/v1</span></div>
<label>Provider</label>
<select id="llm_provider"><option value="ollama">Ollama</option><option value="lmstudio">LM Studio</option><option value="openai_compat">OpenAI Compatible (custom)</option></select>
<label style="margin-top:6px">Endpoint</label>
<input id="llm_endpoint" type="text" value="http://localhost:11434"/>
<label style="margin-top:6px">Model</label>
<input id="llm_model" type="text" value="llama3.1" placeholder="llama3.1, mistral, qwen2, gemma2..."/>
<div class="row" style="margin-top:6px">
<div style="flex:1"><label>Temperature</label><input id="llm_temp" type="number" step="0.1" value="0.2" min="0" max="2"/></div>
<div style="flex:1"><label>Max tokens</label><input id="llm_max" type="number" value="1024" min="100" max="8000"/></div>
</div>
<label style="margin-top:6px">Custom Prompt (optional, uses {title} {transcript} etc)</label>
<textarea id="llm_prompt" placeholder="Leave empty for default summary prompt"></textarea>
<div class="row" style="margin-top:8px">
<button class="btn secondary" onclick="testLLM()">Test Connection</button>
<button class="btn green" onclick="batchSummarize()">Batch Summarize Missing</button>
<button id="cancelLLM" class="btn red" onclick="cancelJob()">Cancel Batch</button>
</div>
<div id="llmTest" class="small" style="margin-top:8px;white-space:pre-wrap"></div>
<div id="llmHint" class="small" style="margin-top:6px;color:var(--warn)"></div>
</div>
<div class="card">
<h3>LLM Logs</h3>
<div id="llmLog" class="log" style="height:400px">LLM logs appear here...</div>
</div>
</div>
</div>

<!-- SEARCH TAB -->
<div id="tab-search" class="tab-pane hidden">
<div class="grid">
<div class="card">
<h3>🔎 Search knowledgebase</h3>
<div class="row"><input id="q" type="text" placeholder="search summaries, e.g. RAG implementation" style="flex:1"/><button class="btn secondary" onclick="doSearch(0)">Search</button></div>

<div class="collapsible" id="advFilters">
<div class="collapsible-header" onclick="toggleAdvanced()"><span>Advanced filters</span><span id="advIcon">▼</span></div>
<div class="collapsible-body">
<div class="row" style="margin-top:6px">
<input id="s_playlist" type="text" placeholder="playlist filter" style="flex:1"/>
<input id="s_tag" type="text" placeholder="tag (exact match, fixes substring bug)" style="flex:1"/>
<select id="s_type" style="width:160px"><option value="summaries">Summaries</option><option value="videos">Videos (title/desc/tags)</option><option value="transcripts">Transcripts</option><option value="all">All</option></select>
</div>
<div class="row" style="margin-top:6px">
<input id="s_minq" type="number" placeholder="min quality 0-100" style="flex:1"/>
<input id="s_limit" type="number" value="20" min="5" max="100" style="width:100px"/>
<span class="small">per page</span>
</div>
</div>
</div>

<div id="searchResults" style="margin-top:10px"></div>
<div class="pagination" id="pagination"></div>
<div id="searchEmpty" class="empty-state hidden"></div>
</div>
<div class="card">
<h3>📦 RAG Export for Local AI</h3>
<div class="small">Export summaries for use with local AI (LM Studio, Ollama, LangChain, LlamaIndex).</div>
<div class="row" style="margin-top:8px">
<button class="btn secondary" onclick="exportRAG()">Export RAG dataset</button>
<button class="btn secondary" onclick="loadStats()">Stats</button>
</div>
<div id="ragInfo" class="small" style="margin-top:8px;white-space:pre-wrap"></div>
<div id="statsBox" class="small" style="margin-top:12px;white-space:pre-wrap"></div>
</div>
</div>
</div>

</div>

<div id="toastContainer"></div>

<div class="modal" id="contentModal">
<div class="modal-box">
<div class="row" style="justify-content:space-between"><h3 id="modalTitle">Content</h3><button class="btn ghost" onclick="closeModal()">✕</button></div>
<div id="modalBody" style="margin-top:10px;white-space:pre-wrap;max-height:60vh;overflow:auto;font-size:12px"></div>
<div class="row" style="margin-top:10px;justify-content:flex-end"><button class="btn secondary" onclick="copyModal()">Copy</button><button class="btn ghost" onclick="closeModal()">Close</button></div>
</div>
</div>

<script>
let selectedVideoId = null;
let selectedVideoData = null;
let currentSearch = {q:"", offset:0, limit:20, type:"summaries", playlist:"", tag:"", minq:""};
const STAGES = ["discovery","metadata","transcripts","comments","database","complete"];

// --- Settings persistence ---
function loadSettings(){
  try{
    const s = JSON.parse(localStorage.getItem("ytkb_settings")||"{}");
    if(s.outdir) document.getElementById("outdir").value = s.outdir;
    if(s.provider) document.getElementById("llm_provider").value = s.provider;
    if(s.endpoint) document.getElementById("llm_endpoint").value = s.endpoint;
    if(s.model) document.getElementById("llm_model").value = s.model;
    if(s.temp) document.getElementById("llm_temp").value = s.temp;
    if(s.max) document.getElementById("llm_max").value = s.max;
    if(s.prompt) document.getElementById("llm_prompt").value = s.prompt;
    if(s.searchType) document.getElementById("s_type").value = s.searchType;
    if(s.limit) document.getElementById("limit").value = s.limit;
    if(s.f_status) document.getElementById("f_status").value = s.f_status;
    if(s.f_has_summary) document.getElementById("f_has_summary").value = s.f_has_summary;
    if(s.f_sort) document.getElementById("f_sort").value = s.f_sort;
    if(s.f_minq) document.getElementById("f_minq").value = s.f_minq;
    if(s.q) document.getElementById("q").value = s.q;
  }catch(e){}
}
function saveSettings(){
  const s = {
    outdir: document.getElementById("outdir").value,
    provider: document.getElementById("llm_provider").value,
    endpoint: document.getElementById("llm_endpoint").value,
    model: document.getElementById("llm_model").value,
    temp: document.getElementById("llm_temp").value,
    max: document.getElementById("llm_max").value,
    prompt: document.getElementById("llm_prompt").value,
    searchType: document.getElementById("s_type").value,
    limit: document.getElementById("limit").value,
    f_status: document.getElementById("f_status").value,
    f_has_summary: document.getElementById("f_has_summary").value,
    f_sort: document.getElementById("f_sort").value,
    f_minq: document.getElementById("f_minq").value,
    q: document.getElementById("q").value
  };
  localStorage.setItem("ytkb_settings", JSON.stringify(s));
}
["outdir","llm_provider","llm_endpoint","llm_model","llm_temp","llm_max","llm_prompt","s_type","limit","f_status","f_has_summary","f_sort","f_minq","q"].forEach(id=>{
  const el = document.getElementById(id);
  if(el) el.addEventListener("change", saveSettings);
  if(el) el.addEventListener("input", ()=>{clearTimeout(window._saveT); window._saveT=setTimeout(saveSettings,400)});
});

// --- Toasts ---
function showToast(msg, type="info", duration=3000){
  const container = document.getElementById("toastContainer");
  const toast = document.createElement("div");
  toast.className = "toast "+type;
  toast.innerHTML = `<div>${msg}</div><button class="close" onclick="this.parentElement.remove()">✕</button>`;
  container.appendChild(toast);
  setTimeout(()=>{ toast.style.opacity="0"; toast.style.transform="translateX(20px)"; setTimeout(()=>toast.remove(),300); }, duration);
  return toast;
}
function showConfirmToast(msg, onConfirm, onCancel){
  const container = document.getElementById("toastContainer");
  const toast = document.createElement("div");
  toast.className = "toast warn";
  toast.innerHTML = `<div>${msg}</div><div class="row" style="margin-top:8px"><button class="btn green" style="padding:4px 8px;font-size:11px" id="cYes">Yes</button><button class="btn ghost" style="padding:4px 8px;font-size:11px" id="cNo">No</button></div>`;
  container.appendChild(toast);
  toast.querySelector("#cYes").onclick = ()=>{ toast.remove(); if(onConfirm) onConfirm(); };
  toast.querySelector("#cNo").onclick = ()=>{ toast.remove(); if(onCancel) onCancel(); };
  setTimeout(()=>{ if(toast.parentElement) toast.remove(); }, 8000);
}

// --- Tabs ---
document.querySelectorAll(".tab").forEach(tab=>{
  tab.addEventListener("click", (e)=>{
    const name = e.target.dataset.tab;
    document.querySelectorAll(".tab-pane").forEach(el=>el.classList.add("hidden"));
    document.querySelectorAll(".tab").forEach(el=>el.classList.remove("active"));
    document.getElementById("tab-"+name).classList.remove("hidden");
    e.target.classList.add("active");
    if(name==="review") loadVideos();
    if(name==="search" && document.getElementById("q").value.trim()) doSearch(0);
  });
});

// --- Progress bar ---
function renderStageBar(activeStage, progress){
  const bar = document.getElementById("stageBar");
  const labels = {discovery:"Discovery",metadata:"Metadata",transcripts:"Transcripts",comments:"Comments",database:"Database",complete:"Complete"};
  bar.innerHTML = STAGES.map((s, idx)=>{
    const isActive = s===activeStage;
    const activeIdx = STAGES.indexOf(activeStage);
    const isDone = activeIdx>idx || activeStage==="complete";
    const cls = isActive ? "stage-step active" : (isDone ? "stage-step done" : "stage-step");
    const dot = isDone ? "✓" : (idx+1);
    return `<div class="${cls}"><span class="dot">${dot}</span>${labels[s]||s}</div>`;
  }).join("");
  document.getElementById("progressFill").style.width = (progress.percent||0)+"%";
}
function formatETA(sec){
  if(!sec || sec<=0) return "ETA: --";
  if(sec<60) return `ETA: ${sec}s`;
  const m = Math.floor(sec/60), s = sec%60;
  return `ETA: ${m}m ${s}s`;
}

async function poll(){
  try{
    const r=await fetch("/api/status"); const j=await r.json();
    document.getElementById("log").textContent=j.logs.join("\n");
    document.getElementById("log").scrollTop=document.getElementById("log").scrollHeight;
    document.getElementById("stats").textContent=j.running? `${j.stage||"running"}... ${j.stage_progress?.percent||0}%` : (j.stats.videos? `${j.stats.videos} vids, ${j.stats.playlists||0} pls, avgQ ${j.stats.avg_quality||0}, fails ${j.stats.failures||0}` : "idle");
    document.getElementById("go").disabled=j.running;
    const cancelBtn=document.getElementById("cancel");
    if(j.running){ cancelBtn.classList.remove("hidden"); } else { cancelBtn.classList.add("hidden"); }
    if(j.llm_logs && j.llm_logs.length){
      document.getElementById("llmLog").textContent=j.llm_logs.join("\n");
      document.getElementById("llmLog").scrollTop=document.getElementById("llmLog").scrollHeight;
    }
    // Stage UI
    const stage = j.stage||"idle";
    const prog = j.stage_progress||{current:0,total:0,percent:0,message:""};
    renderStageBar(stage, prog);
    document.getElementById("stageDetail").textContent = prog.message || (j.running ? `${stage} ${prog.current||0}/${prog.total||0}` : "Idle - ready");
    document.getElementById("eta").textContent = j.running ? formatETA(j.eta_seconds) : "";
    const c = j.counts||{};
    document.getElementById("counts").textContent = `${c.discovered||0} vids, ${c.metadata_ok||0} meta, ${c.transcripts_ok||0} trans`;
    if(j.running) setTimeout(poll, 800);
  }catch(e){}
}

// --- Archive ---
function validateUrlsInput(){
  const txt = document.getElementById("url").value.trim();
  const errEl = document.getElementById("inlineUrlError");
  const valEl = document.getElementById("urlValidation");
  if(!txt){ errEl.textContent=""; valEl.textContent=""; return false; }
  const lines = txt.split(/[\n,]+/).map(s=>s.trim()).filter(Boolean);
  const invalid = lines.filter(l=>!l.startsWith("http") || (!l.includes("youtube.com") && !l.includes("youtu.be")));
  if(invalid.length){
    errEl.textContent = `Found ${invalid.length} invalid/non-YouTube URLs. Only YouTube links are processed.`;
    valEl.textContent = `⚠ ${invalid.length} invalid`;
    valEl.style.color="var(--err)";
    return false;
  } else {
    errEl.textContent = "";
    valEl.textContent = `✓ ${lines.length} URL(s) valid`;
    valEl.style.color="var(--accent2)";
    return true;
  }
}
document.getElementById("url").addEventListener("input", validateUrlsInput);

document.getElementById("go").onclick=async()=>{
  const url=document.getElementById("url").value.trim();
  const limit=parseInt(document.getElementById("limit").value||"0");
  const outdir=document.getElementById("outdir").value||"output";
  if(!url){ showToast("Paste YouTube URLs first", "error"); return; }
  validateUrlsInput();
  const lines = url.split(/[\n,]+/).map(s=>s.trim()).filter(Boolean);
  if(lines.length===0){ showToast("No valid URLs", "error"); return; }
  await fetch("/api/archive",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({urls:url, limit, out_dir:outdir, skip_metadata:document.getElementById("skipMeta").checked, skip_transcripts:document.getElementById("skipT").checked, skip_comments:document.getElementById("skipC").checked})});
  showToast(`Started archiving ${lines.length} URL(s)`, "success");
  poll();
};

document.getElementById("cancel").onclick=async()=>{
  showConfirmToast("Cancel current job?", async()=>{
    await fetch("/api/cancel",{method:"POST"});
    showToast("Cancel requested", "warn");
  });
};

async function cancelJob(){
  showConfirmToast("Cancel batch LLM job?", async()=>{
    await fetch("/api/cancel",{method:"POST"});
    showToast("Cancel requested", "warn");
  });
}

// --- Review ---
function qualityColor(score){
  if(score>=80) return "q-excellent";
  if(score>=60) return "q-good";
  if(score>=40) return "q-ok";
  if(score>=20) return "q-low";
  return "q-poor";
}
function qualityBadge(score){
  const s = score||0;
  let label="", cls="";
  if(s>=80){ label="excellent"; cls="ok"; }
  else if(s>=60){ label="good"; cls="ok"; }
  else if(s>=40){ label="ok"; cls="warn"; }
  else if(s>=20){ label="low"; cls="warn"; }
  else { label="poor"; cls="fail"; }
  return `<span class="badge ${cls}">${s.toFixed(1)} ${label}</span>`;
}
function parseQualityDetails(video){
  try{
    const d = typeof video.quality_details === "string" ? JSON.parse(video.quality_details) : video.quality_details;
    if(!d) return "";
    return `Completeness: ${d.completeness||0}/25\nAuthority: ${d.authority||0}/25\nDepth: ${d.depth||0}/20\nFreshness: ${d.freshness||0}/10\nConsensus: ${d.consensus||0}/20\nTotal: ${d.total||0}\nViews: ${d.views||0} Likes: ${d.likes||0}\nDuration: ${d.duration||0}s\nTags: ${d.tag_count||0} Has transcript: ${d.has_transcript}\nWPM: ${d.wpm||"-"} Age: ${d.age_days||"-"}d`;
  }catch(e){ return ""; }
}

async function loadVideos(){
  const status=document.getElementById("f_status").value;
  const hasSummary=document.getElementById("f_has_summary").value;
  const sort=document.getElementById("f_sort").value;
  const minq=document.getElementById("f_minq").value;
  const r=await fetch(`/api/videos?status=${status}&has_summary=${hasSummary}&sort=${sort}&min_quality=${minq}&limit=100`);
  const j=await r.json();
  const el=document.getElementById("videoList");
  if(j.error){el.innerHTML=`<div style="color:var(--err)">${j.error}</div>`;return;}
  if(!j.videos || j.videos.length===0){
    el.innerHTML=`<div class="empty-state"><b>No videos match filters</b><br><span class="small">Try lowering min quality, clearing status filter, or check if DB exists.</span></div>`;
    return;
  }
  el.innerHTML=j.videos.map(v=>{
    const hasSum = v.summary && v.summary.length>10;
    const cls = hasSum ? "video-card has-summary" : "video-card";
    const tip = parseQualityDetails(v);
    const qcol = qualityColor(v.quality_score||0);
    return `<div class="${cls}">
      <div class="row" style="justify-content:space-between"><b style="cursor:pointer" onclick="selectVideo('${v.video_id}')">${(v.title||v.video_id).slice(0,90)}</b><div class="tooltip">${qualityBadge(v.quality_score||0)}<div class="tip">${tip}</div></div></div>
      <div class="small">${v.channel||""} | ${v.duration||0}s | ${v.view_count||0} views | ${v.video_id} ${v.status!=="ok"?`<span class="badge fail">${v.status}</span>`:""} ${hasSum?`<span class="badge ok">summary</span>`:`<span class="badge warn">no summary</span>`} ${v.user_score?`<span class="badge info">you:${v.user_score}</span>`:""}</div>
      <div class="quality-bar"><div class="quality-fill ${qcol}" style="width:${Math.min(100, v.quality_score||0)}%"></div></div>
      <div class="small" style="margin-top:4px">${(v.summary||"").slice(0,120)}${hasSum?"...":""}</div>
      <div class="actions">
        <button class="btn secondary" onclick="window.open('${v.url||"https://www.youtube.com/watch?v="+v.video_id}', '_blank')">▶ Watch</button>
        <button class="btn secondary" onclick="copyId('${v.video_id}')">Copy ID</button>
        <button class="btn secondary" onclick="selectVideo('${v.video_id}')">Review</button>
        <button class="btn secondary" onclick="quickCopyPrompt('${v.video_id}')">Copy Prompt</button>
        <button class="btn ghost" onclick="quickGenerate('${v.video_id}')">Generate Summary</button>
        <button class="btn ghost" onclick="openTranscript('${v.video_id}')">Transcript</button>
      </div>
    </div>`;
  }).join("");
}

async function selectVideo(video_id){
  selectedVideoId=video_id;
  const r=await fetch(`/api/video/${video_id}`);
  const j=await r.json();
  selectedVideoData=j;
  document.getElementById("selectedActions").classList.remove("hidden");
  const tip = j.video ? parseQualityDetails(j.video) : "";
  document.getElementById("selectedInfo").innerHTML=`
    <b>${j.video?.title||video_id}</b><br>
    <div class="tooltip" style="margin-top:4px">${qualityBadge(j.video?.quality_score||0)}<div class="tip">${tip}</div></div>
    <span class="badge">${j.video?.channel||""}</span>
    <span class="badge">${j.video?.duration||0}s</span>
    <span class="badge ${j.video?.status!=="ok"?"fail":""}">${j.video?.status||"ok"}</span>
    <div class="quality-bar"><div class="quality-fill ${qualityColor(j.video?.quality_score||0)}" style="width:${Math.min(100,j.video?.quality_score||0)}%"></div></div>
    <br><a href="${j.video?.url||"https://www.youtube.com/watch?v="+video_id}" target="_blank" style="color:var(--accent2)">${j.video?.url||""}</a>
    <br>Playlists: ${(j.playlists||[]).join(", ")}
    <br>Tags: ${(j.video?.tags?JSON.parse(j.video.tags).slice(0,12).join(", "):"")}
    <div class="row" style="margin-top:8px">
      <button class="btn secondary" style="font-size:11px" onclick="window.open('${j.video?.url||"https://www.youtube.com/watch?v="+video_id}', '_blank')">Watch</button>
      <button class="btn secondary" style="font-size:11px" onclick="copyId('${video_id}')">Copy ID</button>
      <button class="btn secondary" style="font-size:11px" onclick="quickCopyPrompt('${video_id}')">Copy Prompt</button>
    </div>
  `;
  document.getElementById("userScore").value=j.video?.user_score||"";
  document.getElementById("userNotes").value=j.video?.user_notes||"";
  document.getElementById("summary").value=j.video?.summary||"";
  // scroll to actions on mobile
  document.getElementById("selectedActions").scrollIntoView({behavior:"smooth", block:"nearest"});
}

function copyId(id){
  copyToClipboard(id);
  showToast(`Copied ID ${id}`, "success", 2000);
}

async function copyToClipboard(text){
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch(e){
    try{
      const ta=document.createElement("textarea");
      ta.value=text;
      ta.style.position="fixed";
      ta.style.opacity="0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      return true;
    } catch(e2){
      prompt("Copy failed, please copy manually:", text.slice(0,5000));
      return false;
    }
  }
}

async function copyField(type){
  if(!selectedVideoId){showToast("Select a video first", "warn");return;}
  const r=await fetch(`/api/video/${selectedVideoId}/copy`);
  const j=await r.json();
  let text="";
  if(type==="transcript") text=j.transcript||"";
  else if(type==="comments") text=(j.comments||[]).map(c=>`${c.author}: ${c.text}`).join("\n");
  else if(type==="combined") text=j.combined_for_ai||"";
  if(!text){ showToast(`No ${type} available`, "warn"); return; }
  await copyToClipboard(text);
  showToast(`Copied ${type} (${text.length} chars)`, "success");
}

async function quickCopyPrompt(video_id){
  const r=await fetch(`/api/video/${video_id}/copy`);
  const j=await r.json();
  const text=j.combined_for_ai||"";
  if(!text){ showToast("No prompt data", "warn"); return; }
  await copyToClipboard(text);
  showToast(`Copied prompt for ${video_id} (${text.length} chars)`, "success");
}

async function openTranscript(video_id){
  const r=await fetch(`/api/video/${video_id}/copy`);
  const j=await r.json();
  showModal("Transcript - "+video_id, j.transcript||"No transcript available. Flagged for manual watch.");
}
async function openComments(video_id){
  const r=await fetch(`/api/video/${video_id}/copy`);
  const j=await r.json();
  const txt = (j.comments||[]).map(c=>`${c.author} (${c.likes||0}): ${c.text}`).join("\n\n") || "No comments";
  showModal("Comments - "+video_id, txt);
}
function showModal(title, body){
  document.getElementById("modalTitle").textContent=title;
  document.getElementById("modalBody").textContent=body;
  document.getElementById("contentModal").classList.add("open");
}
function closeModal(){ document.getElementById("contentModal").classList.remove("open"); }
function copyModal(){
  const txt=document.getElementById("modalBody").textContent;
  copyToClipboard(txt);
  showToast("Copied modal content", "success");
}

async function quickGenerate(video_id){
  const config={
    provider:document.getElementById("llm_provider").value,
    endpoint:document.getElementById("llm_endpoint").value,
    model:document.getElementById("llm_model").value,
    temperature:parseFloat(document.getElementById("llm_temp").value||"0.2"),
    max_tokens:parseInt(document.getElementById("llm_max").value||"1024"),
    prompt_template:document.getElementById("llm_prompt").value
  };
  showToast(`Generating summary for ${video_id}...`, "info");
  const r=await fetch(`/api/summarize`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({video_id, config})});
  const j=await r.json();
  if(j.error){ showToast("Error: "+j.error, "error", 4000); }
  else {
    showToast("Summary generated - open Review tab to save", "success", 3000);
    if(selectedVideoId===video_id){
      document.getElementById("summary").value=j.summary||"";
    }
  }
}

async function saveScore(){
  if(!selectedVideoId){ showToast("Select video", "warn"); return; }
  const score=parseFloat(document.getElementById("userScore").value);
  const notes=document.getElementById("userNotes").value;
  if(isNaN(score)){showToast("Score must be number 0-100", "error");return;}
  const r=await fetch(`/api/video/${selectedVideoId}/score`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({user_score:score,user_notes:notes})});
  const j=await r.json();
  if(j.ok){ showToast("Score saved", "success"); loadVideos(); }
  else showToast("Error: "+j.error, "error");
}

async function saveSummary(){
  if(!selectedVideoId){ showToast("Select video", "warn"); return; }
  const summary=document.getElementById("summary").value;
  if(!summary.trim()){showToast("Summary empty", "error");return;}
  const r=await fetch(`/api/video/${selectedVideoId}/summary`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({summary})});
  const j=await r.json();
  if(j.ok){
    showToast("Summary saved! Now searchable and in RAG export.", "success");
    loadVideos();
  } else showToast("Error: "+j.error, "error");
}

async function loadFailures(){
  const r=await fetch("/api/failed");
  const j=await r.json();
  const el=document.getElementById("failures");
  if(!j.failures || j.failures.length===0){ el.innerHTML=`<div class="empty-state"><b>No failures</b><br><span class="small">All good! No videos need manual review.</span></div>`; return; }
  el.innerHTML=j.failures.map(f=>`<div style="margin-bottom:6px"><span class="badge fail">${f.type}</span> ${f.video_id} - ${(f.error||"").slice(0,80)} <a href="${f.url}" target="_blank" style="color:var(--accent2)">watch</a> <button class="btn ghost" style="padding:2px 6px;font-size:10px" onclick="retryFailed('${f.video_id}','${f.type}')">Retry</button></div>`).join("");
}

async function retryFailed(video_id, type){
  showToast(`Retrying ${type} for ${video_id}...`, "info");
  const r=await fetch("/api/retry",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({video_id, type})});
  const j=await r.json();
  if(j.ok) showToast(`Retry ${type} succeeded for ${video_id}`, "success");
  else showToast(`Retry failed: ${j.error||"unknown"}`, "error", 4000);
}

async function retryAllFailures(){
  showConfirmToast("Retry all failed videos? This may take a while.", async()=>{
    const r=await fetch("/api/failed");
    const j=await r.json();
    for(const f of j.failures||[]){
      await retryFailed(f.video_id, f.type);
      await new Promise(r=>setTimeout(r,500));
    }
    showToast("Retry all finished", "success");
  });
}

async function testLLM(){
  const config={
    provider:document.getElementById("llm_provider").value,
    endpoint:document.getElementById("llm_endpoint").value,
    model:document.getElementById("llm_model").value
  };
  document.getElementById("llmTest").textContent="Testing...";
  document.getElementById("llmHint").textContent="";
  const r=await fetch("/api/llm/test",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(config)});
  const j=await r.json();
  document.getElementById("llmTest").textContent=JSON.stringify(j,null,2);
  if(!j.ok){
    document.getElementById("llmHint").textContent="Hint: Check if Ollama is running (ollama serve), endpoint is reachable, model is pulled (ollama pull "+config.model+"), or LM Studio server is started.";
    showToast("LLM connection failed - see hint", "error", 5000);
  } else {
    if(j.model_found===false) document.getElementById("llmHint").textContent=`Model ${config.model} not found. Available: ${(j.models_available||[]).slice(0,5).join(", ")}`;
    showToast("LLM connection OK", "success");
  }
}

async function summarizeSingle(){
  if(!selectedVideoId){showToast("Select video", "warn");return;}
  const config={
    provider:document.getElementById("llm_provider").value,
    endpoint:document.getElementById("llm_endpoint").value,
    model:document.getElementById("llm_model").value,
    temperature:parseFloat(document.getElementById("llm_temp").value||"0.2"),
    max_tokens:parseInt(document.getElementById("llm_max").value||"1024"),
    prompt_template:document.getElementById("llm_prompt").value
  };
  document.getElementById("copyStatus").textContent="Summarizing with local LLM...";
  showToast("Summarizing with local LLM...", "info");
  const r=await fetch(`/api/summarize`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({video_id:selectedVideoId, config})});
  const j=await r.json();
  if(j.error){ showToast("Error: "+j.error, "error", 4000); document.getElementById("copyStatus").textContent=""; }
  else{
    document.getElementById("summary").value=j.summary||"";
    document.getElementById("copyStatus").textContent="LLM summary generated - review and Save";
    showToast("LLM summary generated - review and save", "success");
    poll();
  }
}

async function batchSummarize(){
  showConfirmToast("Batch summarize all videos without summary using local LLM? This may take a while.", async()=>{
    const config={
      provider:document.getElementById("llm_provider").value,
      endpoint:document.getElementById("llm_endpoint").value,
      model:document.getElementById("llm_model").value,
      temperature:parseFloat(document.getElementById("llm_temp").value||"0.2"),
      max_tokens:parseInt(document.getElementById("llm_max").value||"1024"),
      prompt_template:document.getElementById("llm_prompt").value
    };
    const r=await fetch("/api/summarize/batch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({config, only_missing:true})});
    showToast("Batch started, check LLM logs", "success");
    poll();
  });
}

// --- Search ---
function toggleAdvanced(){
  const el=document.getElementById("advFilters");
  el.classList.toggle("open");
  document.getElementById("advIcon").textContent = el.classList.contains("open") ? "▲" : "▼";
  localStorage.setItem("ytkb_advOpen", el.classList.contains("open")?"1":"0");
}
if(localStorage.getItem("ytkb_advOpen")==="1"){ document.getElementById("advFilters").classList.add("open"); document.getElementById("advIcon").textContent="▲"; }

async function doSearch(offset=0){
  const q=document.getElementById("q").value.trim();
  if(!q){ showToast("Enter search terms", "warn"); return; }
  const pl=document.getElementById("s_playlist").value;
  const tag=document.getElementById("s_tag").value;
  const type=document.getElementById("s_type").value;
  const minq=document.getElementById("s_minq").value;
  const limit=parseInt(document.getElementById("s_limit").value||"20");
  currentSearch = {q, offset, limit, type, playlist:pl, tag, minq};

  const url = `/api/search?q=${encodeURIComponent(q)}&playlist=${encodeURIComponent(pl)}&tag=${encodeURIComponent(tag)}&type=${type}&limit=${limit}&offset=${offset}&min_quality=${minq||0}`;
  const r=await fetch(url);
  const j=await r.json();
  const el=document.getElementById("searchResults");
  const emptyEl=document.getElementById("searchEmpty");
  const pagEl=document.getElementById("pagination");
  if(j.error){ el.innerHTML=`<div style="color:var(--err)">${j.error}</div>`; return; }
  const results = j.results||[];
  if(results.length===0){
    el.innerHTML="";
    emptyEl.classList.remove("hidden");
    emptyEl.innerHTML = `<b>No results for "${q}"</b><br><span class="small">Suggestions:</span><ul style="text-align:left;display:inline-block;margin:8px 0 0 0"><li>Try searching <b>titles</b> instead of summaries (change type to Videos)</li><li>Check <b>Transcripts</b> type for spoken content</li><li>Lower the <b>minimum quality</b> filter</li><li>Remove <b>playlist/tag</b> filters</li><li>Try broader terms - e.g. "python" instead of "python async tutorial"</li></ul>`;
    pagEl.innerHTML="";
    return;
  }
  emptyEl.classList.add("hidden");
  el.innerHTML=results.map((row,i)=>{
    const idx = offset + i + 1;
    const matchField = row._match?.field || "summary";
    const highlighted = row._match?.highlighted || (row.summary||row.text||row.description||"").slice(0,400);
    const reasons = row._match?.reasons||[matchField];
    const badges = reasons.map(r=>`<span class="match-badge ${r}">${r}</span>`).join("");
    const score = row.quality_score||0;
    return `<div class="search-result">
      <div class="row" style="justify-content:space-between"><b>${idx}. ${(row.title||row.video_id||"").slice(0,90)}</b><span>${qualityBadge(score)}</span></div>
      <div class="meta">${badges} ID: ${row.video_id} | ${row.channel||""} | ${row.view_count||0} views | <a href="${row.url}" target="_blank" style="color:var(--accent2)">Watch</a> | <a href="#" onclick="event.preventDefault();copyId('${row.video_id}')" style="color:var(--muted)">Copy ID</a> | <a href="#" onclick="event.preventDefault();jumpToReview('${row.video_id}')" style="color:var(--accent2)">Review</a></div>
      <div class="small" style="margin-top:6px">${highlighted}...</div>
      <div class="row" style="margin-top:6px"><button class="btn secondary" style="padding:3px 7px;font-size:10px" onclick="window.open('${row.url}', '_blank')">▶ Watch</button><button class="btn ghost" style="padding:3px 7px;font-size:10px" onclick="quickCopyPrompt('${row.video_id}')">Copy Prompt</button><button class="btn ghost" style="padding:3px 7px;font-size:10px" onclick="quickGenerate('${row.video_id}')">Generate Summary</button></div>
    </div>`;
  }).join("");

  // pagination
  const total = results.length;
  const hasPrev = offset>0;
  const hasNext = total>=limit;
  pagEl.innerHTML = `
    <button class="btn secondary" ${hasPrev?"":"disabled"} onclick="doSearch(${Math.max(0, offset-limit)})">‹ Previous</button>
    <span class="small">Page ${Math.floor(offset/limit)+1} • ${offset+1}-${offset+total} • ${q} in ${type}</span>
    <button class="btn secondary" ${hasNext?"":"disabled"} onclick="doSearch(${offset+limit})">Next ›</button>
  `;
}

function jumpToReview(video_id){
  document.querySelectorAll(".tab").forEach(el=>el.classList.remove("active"));
  document.querySelectorAll(".tab-pane").forEach(el=>el.classList.add("hidden"));
  document.querySelector('[data-tab="review"]').classList.add("active");
  document.getElementById("tab-review").classList.remove("hidden");
  selectVideo(video_id);
  showToast(`Jumped to Review for ${video_id}`, "info");
}

function saveFilterSettings(){ saveSettings(); showToast("Filters saved", "success", 2000); }
function resetFilters(){
  document.getElementById("f_status").value="";
  document.getElementById("f_has_summary").value="";
  document.getElementById("f_sort").value="quality";
  document.getElementById("f_minq").value="";
  saveSettings();
  loadVideos();
  showToast("Filters reset", "info");
}

async function exportRAG(){
  showToast("Exporting RAG dataset...", "info");
  const r=await fetch("/api/export/rag");
  const j=await r.json();
  if(j.error){ showToast("Export failed: "+j.error, "error"); }
  else { document.getElementById("ragInfo").textContent=JSON.stringify(j,null,2); showToast(`Exported ${j.count} summaries`, "success"); }
}

async function loadStats(){
  showToast("Loading stats...", "info", 1500);
  const r=await fetch("/api/search?q=__stats__"); const j=await r.json();
  document.getElementById("statsBox").innerHTML=`<pre style="white-space:pre-wrap">${JSON.stringify(j.stats,null,2)}</pre>`;
}

document.getElementById("llm_provider").addEventListener("change", e=>{
  const v=e.target.value;
  if(v==="ollama") document.getElementById("llm_endpoint").value="http://localhost:11434";
  else if(v==="lmstudio") document.getElementById("llm_endpoint").value="http://localhost:1234/v1";
  saveSettings();
});

document.getElementById("q").addEventListener("keydown", e=>{ if(e.key==="Enter") doSearch(0); });
loadSettings();
renderStageBar("idle", {percent:0});
poll();
</script>
</body>
</html>
"""

class Handler(BaseHTTPRequestHandler):
    def _set_headers(self, ct="application/json", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers()

    def _get_out_dir(self) -> Path:
        out_dir = Path(STATE["stats"].get("out_dir", "output")) if STATE["stats"] else Path("output")
        db_path = out_dir / "archive.db"
        if not db_path.exists():
            db_path = Path("output") / "archive.db"
            out_dir = db_path.parent
        return out_dir

    def _get_db_path(self) -> Path:
        out_dir = self._get_out_dir()
        db_path = out_dir / "archive.db"
        if not db_path.exists():
            db_path = Path("output") / "archive.db"
        return db_path

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._set_headers("text/html")
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        elif parsed.path == "/api/status":
            self._set_headers()
            with LOCK:
                data = {
                    "running": STATE["running"],
                    "logs": STATE["logs"][-400:],
                    "llm_logs": STATE["llm_logs"][-200:],
                    "stats": STATE["stats"],
                    "error": STATE["error"],
                    "cancelled": STATE["cancelled"],
                    "stage": STATE["stage"],
                    "stage_progress": STATE["stage_progress"],
                    "eta_seconds": STATE["eta_seconds"],
                    "counts": STATE["counts"]
                }
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif parsed.path == "/api/videos":
            qs = parse_qs(parsed.query)
            status = qs.get("status", [""])[0]
            has_summary = qs.get("has_summary", [""])[0]
            sort = qs.get("sort", ["quality"])[0]
            min_q = qs.get("min_quality", [""])[0]
            try:
                limit = int(qs.get("limit", ["50"])[0])
            except ValueError:
                limit = 50
            if limit > 200:
                limit = 200
            db_path = self._get_db_path()
            if not db_path.exists():
                self._set_headers()
                self.wfile.write(json.dumps({"error": "No DB"}).encode("utf-8"))
                return
            try:
                with db_connection(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    where = []
                    params: List[Any] = []
                    if status:
                        where.append("v.status=?")
                        params.append(status)
                    if has_summary == "yes":
                        where.append("v.summary IS NOT NULL AND v.summary != ''")
                    elif has_summary == "no":
                        where.append("(v.summary IS NULL OR v.summary='')")
                    if min_q:
                        try:
                            where.append("v.quality_score>=?")
                            params.append(float(min_q))
                        except ValueError:
                            pass
                    where_sql = " WHERE " + " AND ".join(where) if where else ""
                    order_map = {"quality": "v.quality_score DESC", "user_score": "v.user_score DESC", "date": "v.upload_date DESC", "views": "v.view_count DESC", "duration": "v.duration DESC"}
                    order_sql = order_map.get(sort, "v.quality_score DESC")
                    sql = f"SELECT v.* FROM videos v {where_sql} ORDER BY {order_sql} LIMIT ?"
                    params.append(limit)
                    cur.execute(sql, params)
                    rows = [dict(r) for r in cur.fetchall()]
                    self._set_headers()
                    self.wfile.write(json.dumps({"videos": rows}).encode("utf-8"))
            except sqlite3.Error as e:
                logger.error(f"videos API DB error: {e}")
                self._set_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        elif parsed.path.startswith("/api/video/") and parsed.path.endswith("/copy"):
            video_id = parsed.path.split("/")[3]
            db_path = self._get_db_path()
            try:
                data = get_video_copy_data(db_path, video_id)
                self._set_headers()
                self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                logger.exception(f"copy API error for {video_id}")
                self._set_headers(code=500)
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        elif parsed.path.startswith("/api/video/"):
            parts = parsed.path.split("/")
            if len(parts) >= 4:
                video_id = parts[3]
                db_path = self._get_db_path()
                try:
                    data = get_video_copy_data(db_path, video_id)
                    self._set_headers()
                    self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
                except Exception as e:
                    logger.exception(f"video API error for {video_id}")
                    self._set_headers(code=500)
                    self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return
            self._set_headers("text/plain", 404)
            self.wfile.write(b"Not found")
        elif parsed.path == "/api/failed":
            db_path = self._get_db_path()
            if not db_path.exists():
                self._set_headers()
                self.wfile.write(json.dumps({"failures": []}).encode("utf-8"))
                return
            try:
                with db_connection(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM failures ORDER BY id DESC LIMIT 100")
                    rows = [dict(r) for r in cur.fetchall()]
                    self._set_headers()
                    self.wfile.write(json.dumps({"failures": rows}).encode("utf-8"))
            except sqlite3.Error as e:
                logger.error(f"failed API DB error: {e}")
                self._set_headers()
                self.wfile.write(json.dumps({"error": str(e), "failures": []}).encode("utf-8"))
        elif parsed.path == "/api/export/rag":
            out_dir = self._get_out_dir()
            db_path = self._get_db_path()
            try:
                res = export_rag_dataset(out_dir, db_path)
                self._set_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
            except Exception as e:
                logger.exception("RAG export failed")
                self._set_headers(code=500)
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        elif parsed.path.startswith("/api/search"):
            qs = parse_qs(parsed.query)
            q = qs.get("q", [""])[0]
            playlist = qs.get("playlist", [""])[0]
            tag = qs.get("tag", [""])[0]
            search_type = qs.get("type", ["summaries"])[0]
            try:
                limit = int(qs.get("limit", ["20"])[0])
            except ValueError:
                limit = 20
            try:
                offset = int(qs.get("offset", ["0"])[0])
            except ValueError:
                offset = 0
            try:
                min_quality = float(qs.get("min_quality", ["0"])[0])
            except ValueError:
                min_quality = 0
            db_path = self._get_db_path()
            if q == "__stats__":
                if not db_path.exists():
                    self._set_headers()
                    self.wfile.write(json.dumps({"stats": {"error": "No DB yet"}, "results": []}).encode("utf-8"))
                    return
                try:
                    with db_connection(db_path) as conn:
                        cur = conn.cursor()
                        stats = {}
                        for t in ["videos", "playlists", "playlist_videos", "transcripts", "comments", "failures"]:
                            try:
                                cur.execute(f"SELECT COUNT(*) FROM {t}")
                                stats[t] = cur.fetchone()[0]
                            except sqlite3.Error:
                                stats[t] = 0
                        try:
                            cur.execute("SELECT AVG(quality_score) FROM videos")
                            stats["avg_quality"] = round(cur.fetchone()[0] or 0, 1)
                            cur.execute("SELECT COUNT(*) FROM videos WHERE summary IS NOT NULL AND summary!=''")
                            stats["with_summary"] = cur.fetchone()[0]
                            cur.execute("SELECT quality_label, COUNT(*) FROM videos GROUP BY quality_label")
                            stats["quality_dist"] = dict(cur.fetchall())
                            cur.execute("SELECT status, COUNT(*) FROM videos GROUP BY status")
                            stats["status_dist"] = dict(cur.fetchall())
                        except sqlite3.Error:
                            pass
                    self._set_headers()
                    self.wfile.write(json.dumps({"stats": stats, "results": []}).encode("utf-8"))
                except sqlite3.Error as e:
                    self._set_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return
            if not db_path.exists():
                self._set_headers()
                self.wfile.write(json.dumps({"error": "No DB", "results": []}).encode("utf-8"))
                return
            try:
                from search import search as search_fn
                rows = search_fn(db_path, q, search_type=search_type, playlist=playlist or None, tag=tag or None, min_quality=min_quality, limit=limit, offset=offset)

                # enrich with match highlighting
                def _highlight_info(row, query):
                    q_low = query.lower()
                    terms = [t for t in q_low.split() if len(t) >= 2]
                    field = "summary"
                    reasons = []
                    snippet_src = ""
                    # check fields in priority
                    if row.get("summary") and q_low in (row.get("summary") or "").lower():
                        field = "summary"
                        reasons = ["summary"]
                        snippet_src = row.get("summary", "")
                    elif row.get("text") and q_low in (row.get("text") or "").lower():
                        field = "transcript"
                        reasons = ["transcript"]
                        snippet_src = row.get("text", "")
                    elif row.get("title") and q_low in (row.get("title") or "").lower():
                        field = "title"
                        reasons = ["title"]
                        snippet_src = row.get("title", "")
                    elif row.get("tags"):
                        tags_str = str(row.get("tags")).lower()
                        if any(t in tags_str for t in terms):
                            field = "tags"
                            reasons = ["tags"]
                            snippet_src = str(row.get("tags"))
                    elif row.get("description") and q_low in (row.get("description") or "").lower():
                        field = "description"
                        reasons = ["description"]
                        snippet_src = row.get("description", "")
                    else:
                        # partial term match
                        for f in ["summary", "text", "title", "description", "tags"]:
                            if row.get(f) and any(term in (str(row.get(f)) or "").lower() for term in terms):
                                field = f if f != "text" else "transcript"
                                reasons = [field]
                                snippet_src = str(row.get(f, ""))
                                break
                        if not snippet_src:
                            snippet_src = row.get("summary") or row.get("text") or row.get("description") or ""

                    snippet = snippet_src[:600]
                    highlighted = snippet
                    try:
                        for term in terms:
                            # escape HTML first? simple
                            highlighted = re.sub(f"({re.escape(term)})", r"<mark>\1</mark>", highlighted, flags=re.IGNORECASE)
                    except re.error:
                        pass
                    return {"field": field, "reasons": reasons, "highlighted": highlighted, "raw": snippet[:400]}

                enriched = []
                for r in rows:
                    info = _highlight_info(r, q)
                    r["_match"] = info
                    enriched.append(r)

                self._set_headers()
                self.wfile.write(json.dumps({"results": enriched, "offset": offset, "limit": limit, "query": q}).encode("utf-8"))
            except Exception as e:
                logger.exception(f"search API failed for {q}")
                self._set_headers(code=500)
                self.wfile.write(json.dumps({"error": str(e), "results": []}).encode("utf-8"))
        else:
            self._set_headers("text/plain", 404)
            self.wfile.write(b"Not found")

    def do_POST(self):
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._set_headers(code=400)
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode("utf-8"))
            return

        if parsed.path == "/api/archive":
            urls = data.get("urls", "")
            limit = int(data.get("limit", 0) or 0)
            out_dir = Path(data.get("out_dir", "output"))
            skip_comments = bool(data.get("skip_comments", False))
            skip_transcripts = bool(data.get("skip_transcripts", False))
            skip_metadata = bool(data.get("skip_metadata", False))
            t = threading.Thread(target=archive_job, args=(urls, limit, skip_comments, skip_transcripts, skip_metadata, out_dir), daemon=True)
            t.start()
            self._set_headers()
            self.wfile.write(json.dumps({"ok": True, "started": True}).encode("utf-8"))
        elif parsed.path == "/api/cancel":
            CANCEL_EVENT.set()
            with LOCK:
                STATE["cancelled"] = True
            log("Cancel requested by user")
            self._set_headers()
            self.wfile.write(json.dumps({"ok": True, "cancelled": True}).encode("utf-8"))
        elif parsed.path == "/api/retry":
            video_id = data.get("video_id")
            rtype = data.get("type", "metadata")
            if not video_id:
                self._set_headers(code=400)
                self.wfile.write(json.dumps({"error": "video_id required"}).encode("utf-8"))
                return
            out_dir = self._get_out_dir()
            try:
                # remove old entry to force retry
                if rtype == "metadata":
                    remove_id_from_jsonl(out_dir / "videos_full.jsonl", "id", video_id)
                    remove_id_from_jsonl(out_dir / "failed_metadata.jsonl", "video_id", video_id)
                    fetch_videos_full_metadata([video_id], out_path=out_dir / "videos_full.jsonl", progress_cb=lambda m: log(m), resume=True, cancel_check=is_cancelled)
                elif rtype == "transcript":
                    remove_id_from_jsonl(out_dir / "transcripts.jsonl", "video_id", video_id)
                    remove_id_from_jsonl(out_dir / "failed_transcripts.jsonl", "video_id", video_id)
                    # need minimal video dict
                    vinfo = {"id": video_id, "title": video_id, "url": f"https://www.youtube.com/watch?v={video_id}", "channel": "", "channel_id": ""}
                    # try get title from DB
                    try:
                        db_path = self._get_db_path()
                        if db_path.exists():
                            with db_connection(db_path) as conn:
                                conn.row_factory = sqlite3.Row
                                cur = conn.cursor()
                                cur.execute("SELECT title, channel, channel_id, url FROM videos WHERE video_id=?", (video_id,))
                                row = cur.fetchone()
                                if row:
                                    vinfo.update(dict(row))
                    except Exception:
                        pass
                    fetch_transcripts_bulk([vinfo], out_path=out_dir / "transcripts.jsonl", resume=True, progress_cb=lambda m: log(m), cancel_check=is_cancelled)
                elif rtype == "comments":
                    # comments file is append per comment, need to remove all for video
                    # reuse helper but key is video_id for comments (multiple lines)
                    path = out_dir / "comments.jsonl"
                    if path.exists():
                        try:
                            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                            kept = []
                            for line in lines:
                                try:
                                    j = json.loads(line)
                                    if j.get("video_id") != video_id:
                                        kept.append(line)
                                except json.JSONDecodeError:
                                    continue
                            path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
                        except OSError:
                            pass
                    remove_id_from_jsonl(out_dir / "failed_comments.jsonl", "video_id", video_id)
                    fetch_comments_bulk([video_id], out_path=out_dir / "comments.jsonl", resume=True, progress_cb=lambda m: log(m), cancel_check=is_cancelled)
                else:
                    raise ValueError(f"Unknown retry type {rtype}")

                # optionally rebuild DB after retry
                try:
                    build_db(out_dir=out_dir, db_path=out_dir / "archive.db", progress_cb=lambda m: log(m))
                except Exception as e:
                    logger.warning(f"DB rebuild after retry failed: {e}")

                self._set_headers()
                self.wfile.write(json.dumps({"ok": True, "video_id": video_id, "type": rtype}).encode("utf-8"))
            except Exception as e:
                logger.exception(f"Retry failed for {video_id} {rtype}")
                self._set_headers(code=500)
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        elif parsed.path.startswith("/api/video/") and parsed.path.endswith("/score"):
            video_id = parsed.path.split("/")[3]
            try:
                score = float(data.get("user_score", 0))
                notes = data.get("user_notes", "")
                if not 0 <= score <= 100:
                    raise ValueError("Score must be 0-100")
                out_dir = self._get_out_dir()
                db_path = self._get_db_path()
                update_video_user_score(db_path, video_id, score, notes, out_dir=out_dir)
                self._set_headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            except (ValueError, TypeError, OSError, sqlite3.Error) as e:
                logger.warning(f"Score update failed for {video_id}: {e}")
                self._set_headers(code=400)
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        elif parsed.path.startswith("/api/video/") and parsed.path.endswith("/summary"):
            video_id = parsed.path.split("/")[3]
            try:
                summary = data.get("summary", "")
                if not summary.strip():
                    raise ValueError("Summary empty")
                out_dir = self._get_out_dir()
                db_path = self._get_db_path()
                update_video_summary(db_path, video_id, summary, source="user_paste", out_dir=out_dir)
                self._set_headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            except (ValueError, OSError, sqlite3.Error) as e:
                logger.warning(f"Summary update failed for {video_id}: {e}")
                self._set_headers(code=400)
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        elif parsed.path == "/api/summarize":
            try:
                video_id = data.get("video_id")
                config = data.get("config", {})
                if not video_id:
                    raise ValueError("video_id required")
                out_dir = self._get_out_dir()
                db_path = self._get_db_path()
                def cb(m): llm_log(m)
                res = generate_summary_for_video(video_id, out_dir, db_path, config, progress_cb=cb, cancel_check=is_cancelled)
                self._set_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                logger.exception("Single summarize failed")
                self._set_headers(code=500)
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        elif parsed.path == "/api/summarize/batch":
            try:
                config = data.get("config", {})
                only_missing = data.get("only_missing", True)
                limit = int(data.get("limit", 0) or 0)
                out_dir = self._get_out_dir()
                db_path = self._get_db_path()
                def bg():
                    CANCEL_EVENT.clear()
                    with LOCK:
                        STATE["cancelled"] = False
                    def cb(m): llm_log(m)
                    batch_summarize(out_dir, db_path, config, only_missing=only_missing, limit=limit, progress_cb=cb, cancel_check=is_cancelled)
                t = threading.Thread(target=bg, daemon=True)
                t.start()
                self._set_headers()
                self.wfile.write(json.dumps({"ok": True, "started": True}).encode("utf-8"))
            except Exception as e:
                logger.exception("Batch summarize start failed")
                self._set_headers(code=500)
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        elif parsed.path == "/api/llm/test":
            try:
                config = data
                res = test_llm_connection(config)
                self._set_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
            except Exception as e:
                logger.exception("LLM test failed")
                self._set_headers(code=500)
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        else:
            self._set_headers("text/plain", 404)
            self.wfile.write(b"Not found")

    def log_message(self, format, *args):
        return

def find_free_port(start: int, host: str = "127.0.0.1") -> int:
    import socket
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    return start

def run_server(host: str = "127.0.0.1", port: int = 8765):
    actual_port = find_free_port(port, host)
    server = HTTPServer((host, actual_port), Handler)
    url = f"http://{host}:{actual_port}"
    print(f"\n=== yt refinement + local LLM (UX upgraded) ===")
    print(f"Python: {sys.executable}")
    print(f"Open {url}")
    print(f"CWD: {Path.cwd().resolve()}")
    print(f"Default out: {(Path.cwd() / 'output').resolve()}")
    print(f"Ollama: {CONFIG.ollama_endpoint} | LMStudio: {CONFIG.lmstudio_endpoint}")
    print(f"Progress bar, ETA, toasts, localStorage, clickable search, quality tooltips enabled")
    print(f"Press Ctrl+C to stop\n")
    try:
        webbrowser.open_new_tab(url)
    except Exception as e:
        logger.debug(f"Failed to open browser: {e}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        CANCEL_EVENT.set()
        server.shutdown()

def main_cli():
    parser = argparse.ArgumentParser(description="YouTube refinement workflow + local LLM (UX upgraded)")
    parser.add_argument("--url", help="URLs (newline/comma separated)")
    parser.add_argument("--urls-file", help="File with URLs")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--skip-comments", action="store_true")
    parser.add_argument("--skip-transcripts", action="store_true")
    parser.add_argument("--skip-metadata", action="store_true")
    parser.add_argument("--out-dir", default=str(CONFIG.out_dir), help="Relative or absolute out dir (env YTK_OUT_DIR)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind server (env YTK_HOST)")
    parser.add_argument("--port", type=int, default=8765, help="Port to start scanning from (env YTK_PORT)")
    parser.add_argument("--install-deps", action="store_true")
    parser.add_argument("--summarize", action="store_true", help="Batch summarize missing with local LLM")
    parser.add_argument("--llm-provider", default=CONFIG.default_provider, help="ollama or lmstudio (env YTK_LLM_PROVIDER)")
    parser.add_argument("--llm-endpoint", default=None, help="LLM endpoint")
    parser.add_argument("--llm-model", default=CONFIG.default_model, help="LLM model (env YTK_LLM_MODEL)")
    args = parser.parse_args()

    host = os.getenv("YTK_HOST", args.host)
    try:
        port = int(os.getenv("YTK_PORT", str(args.port)))
    except ValueError:
        port = args.port

    if args.llm_endpoint is None:
        if args.llm_provider == "ollama":
            args.llm_endpoint = os.getenv("OLLAMA_ENDPOINT", CONFIG.ollama_endpoint)
        elif args.llm_provider in ("lmstudio", "lm_studio"):
            args.llm_endpoint = os.getenv("LMSTUDIO_ENDPOINT", CONFIG.lmstudio_endpoint)
        else:
            args.llm_endpoint = CONFIG.ollama_endpoint

    if args.install_deps:
        _install(REQUIRED, log_fn=print)
        return

    urls_input = args.url or ""
    if args.urls_file:
        p = Path(args.urls_file)
        if not p.is_absolute():
            p = Path.cwd() / p
        if p.exists():
            try:
                urls_input += "\n" + p.read_text(encoding="utf-8")
            except OSError as e:
                print(f"Failed to read urls file {p}: {e}", file=sys.stderr)
                return

    if args.summarize:
        out_dir = Path(args.out_dir)
        db_path = out_dir / "archive.db"
        if not db_path.exists():
            db_path = Path("output") / "archive.db"
        config = {"provider": args.llm_provider, "endpoint": args.llm_endpoint, "model": args.llm_model}
        def cb(m): print(m)
        CANCEL_EVENT.clear()
        batch_summarize(out_dir, db_path, config, only_missing=True, progress_cb=cb, cancel_check=is_cancelled)
        return

    if not urls_input:
        run_server(host=host, port=port)
        return

    out_dir = Path(args.out_dir)
    archive_job(urls_input, limit=args.limit, skip_comments=args.skip_comments, skip_transcripts=args.skip_transcripts, skip_metadata=args.skip_metadata, out_dir=out_dir)

if __name__ == "__main__":
    main_cli()
