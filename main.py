#!/usr/bin/env python3
"""
main.py - Entry point with auto-launching frontend
- Double-click or `python main.py` -> opens browser UI at http://localhost:8765
- CLI: python main.py --url "https://..." --limit 20

Fix for "No module named yt_dlp":
- Auto-detects ./venv and re-launches with it if you forgot to activate
- Auto-pip installs missing deps if needed
"""
import sys, os, subprocess
from pathlib import Path

# --- 0. Bootstrap: auto-use venv if it exists ---
def _ensure_venv():
    base = Path(__file__).parent
    # Windows venv layout
    venv_py_win = base / "venv" / "Scripts" / "python.exe"
    venv_py_unix = base / "venv" / "bin" / "python"
    venv_py = venv_py_win if os.name == "nt" else venv_py_unix
    # If venv exists and we're NOT inside it, relaunch with it
    if venv_py.exists():
        try:
            # sys.prefix points to venv when active; also check executable path
            current = Path(sys.executable).resolve()
            target = venv_py.resolve()
            # avoid infinite loop if already same
            if current != target and "venv" not in str(current).lower():
                # print for debugging but don't spam UI
                print(f"[bootstrap] Found venv at {venv_py}, relaunching with it...")
                os.execv(str(target), [str(target)] + sys.argv)
        except Exception as e:
            print(f"[bootstrap] venv relaunch failed: {e}")

_ensure_venv()

# --- 1. Bootstrap: auto-install deps ---
REQUIRED_PKGS = ["yt-dlp", "youtube-transcript-api", "youtube-comment-downloader", "tqdm"]
IMPORT_MAP = {
    "yt-dlp": "yt_dlp",
    "youtube-transcript-api": "youtube_transcript_api",
    "youtube-comment-downloader": "youtube_comment_downloader",
    "tqdm": "tqdm"
}

def _check_missing():
    missing = []
    for pkg in REQUIRED_PKGS:
        mod = IMPORT_MAP[pkg]
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    return missing

def _auto_install(missing, log_fn=print):
    if not missing:
        return True
    log_fn(f"[deps] Missing packages: {missing}")
    log_fn(f"[deps] Installing with {sys.executable} -m pip install ...")
    try:
        # Upgrade pip first (quiet)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)
        log_fn(f"[deps] Installed OK: {missing}")
        return True
    except Exception as e:
        log_fn(f"[deps] Auto-install failed: {e}")
        log_fn(f"[deps] Try manually: {sys.executable} -m pip install {' '.join(missing)}")
        return False

# Try to auto-install before importing core
_missing_at_start = _check_missing()
if _missing_at_start:
    print(f"Missing deps: {_missing_at_start} -> attempting auto-install")
    _auto_install(_missing_at_start)

# Now import the rest
import argparse, json, threading, time, webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
import socket

try:
    from core import get_video_infos, fetch_transcripts_bulk, fetch_comments_bulk, build_db
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from core import get_video_infos, fetch_transcripts_bulk, fetch_comments_bulk, build_db

OUT_DIR = Path("output")
OUT_DIR.mkdir(exist_ok=True)

STATE = {"running": False, "logs": [], "stats": {}, "error": None}
LOCK = threading.Lock()

def log(msg):
    print(msg)
    with LOCK:
        STATE["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(STATE["logs"]) > 600:
            STATE["logs"] = STATE["logs"][-600:]

def archive_job(url, limit=0, skip_comments=False, skip_transcripts=False, out_dir=OUT_DIR):
    try:
        with LOCK:
            STATE["running"] = True
            STATE["logs"] = []
            STATE["stats"] = {}
            STATE["error"] = None

        # Double-check deps inside job (for UI logs)
        missing = _check_missing()
        if missing:
            log(f"Missing deps inside job: {missing}, installing...")
            ok = _auto_install(missing, log_fn=log)
            if not ok:
                log("ERROR: Could not install dependencies. Run: pip install " + " ".join(missing))
                return
            # re-check after install
            missing = _check_missing()
            if missing:
                log(f"Still missing after install: {missing}. Restart the app.")
                return

        log(f"Starting archive for: {url}")
        out_dir = Path(out_dir)
        out_dir.mkdir(exist_ok=True, parents=True)

        def cb(m):
            log(m)

        infos = get_video_infos(url, limit=limit, progress_cb=cb)
        if not infos:
            log("No videos found. Check URL (must be public playlist/channel/video)")
            return

        (out_dir / "videos_meta.json").write_text(json.dumps(infos, indent=2, ensure_ascii=False), encoding="utf-8")
        (out_dir / "videos.txt").write_text("\n".join([i['id'] for i in infos]), encoding="utf-8")
        log(f"Saved {len(infos)} video IDs to {out_dir / 'videos.txt'}")

        if not skip_transcripts:
            fetch_transcripts_bulk(infos, out_path=out_dir / "transcripts.jsonl", resume=True, progress_cb=cb)
        else:
            log("Skipping transcripts")

        if not skip_comments:
            fetch_comments_bulk([i['id'] for i in infos], out_path=out_dir / "comments.jsonl", resume=True, progress_cb=cb)
        else:
            log("Skipping comments")

        result = build_db(
            transcripts_path=out_dir / "transcripts.jsonl",
            comments_path=out_dir / "comments.jsonl",
            db_path=out_dir / "archive.db",
            meta_path=out_dir / "videos_meta.json",
            progress_cb=cb
        )
        with LOCK:
            STATE["stats"] = result
            STATE["stats"]["videos"] = len(infos)
            STATE["stats"]["out_dir"] = str(out_dir.resolve())
            STATE["stats"]["db_path"] = str((out_dir / "archive.db").resolve())

        log(f"DONE! Files in {out_dir.resolve()}")
        log(f"  - transcripts.jsonl (readable)")
        log(f"  - comments.jsonl (readable)")
        log(f"  - archive.db (searchable)")

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        log(f"ERROR: {e}\n{err}")
        with LOCK:
            STATE["error"] = str(e)
    finally:
        with LOCK:
            STATE["running"] = False

HTML_PAGE = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>yt-archive-seed</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--border:#232830;--text:#e6e8eb;--muted:#9aa3b2;--accent:#7c5cff;--accent2:#2ee6a8}
*{box-sizing:border-box}body{margin:0;font-family:ui-sans,system-ui,-apple-system,Segoe UI,Roboto;background:var(--bg);color:var(--text)}
.wrap{max-width:980px;margin:0 auto;padding:24px}
h1{font-size:28px;margin:0 0 6px} .sub{color:var(--muted);margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:18px;margin-bottom:18px}
.row{display:flex;gap:12px;flex-wrap:wrap} .grow{flex:1;min-width:260px}
input[type=text],input[type=number]{width:100%;background:#0e1117;border:1px solid var(--border);color:var(--text);padding:12px 14px;border-radius:10px;font-size:15px}
input:focus{outline:none;border-color:var(--accent)}
label{font-size:13px;color:var(--muted);display:flex;gap:6px;align-items:center}
.btn{background:var(--accent);color:white;border:0;padding:12px 18px;border-radius:10px;font-weight:600;cursor:pointer}
.btn:disabled{opacity:.5;cursor:not-allowed} .btn.secondary{background:#232836}
.log{background:#0b0e14;border:1px solid var(--border);border-radius:10px;padding:12px;height:320px;overflow:auto;font-family:ui-monospace,monospace;font-size:12.5px;white-space:pre-wrap}
.badge{display:inline-block;background:#1f2330;border:1px solid var(--border);padding:4px 10px;border-radius:20px;font-size:12px;color:var(--muted);margin-right:6px}
.result{border-bottom:1px solid var(--border);padding:12px 0} .result a{color:var(--accent2);text-decoration:none} .snippet{color:var(--muted);font-size:13px;margin-top:4px}
.kbd{background:#232836;padding:2px 6px;border-radius:6px;font-size:11px}
</style>
</head>
<body>
<div class="wrap">
<h1>yt-archive-seed</h1>
<div class="sub">Paste a YouTube playlist / channel / video URL. Saves readable <span class="kbd">.jsonl</span> + searchable <span class="kbd">archive.db</span>. $0, no API key.</div>

<div class="card">
<div class="row">
<div class="grow">
<label>YOUTUBE URL</label>
<input id="url" type="text" placeholder="https://www.youtube.com/playlist?list=PL... or https://www.youtube.com/watch?v=..."/>
</div>
<div style="width:120px">
<label>LIMIT (0=all)</label>
<input id="limit" type="number" value="0" min="0"/>
</div>
</div>
<div class="row" style="margin-top:12px">
<label><input id="skipT" type="checkbox"/> Skip transcripts</label>
<label><input id="skipC" type="checkbox"/> Skip comments</label>
<span style="flex:1"></span>
<button id="go" class="btn">▶ Archive</button>
</div>
<div style="margin-top:10px;color:#9aa3b2;font-size:12px">If you see "No module named yt_dlp", the app will auto-install it. If it fails, run: <span class="kbd">venv\Scripts\python.exe -m pip install yt-dlp youtube-transcript-api youtube-comment-downloader tqdm</span></div>
</div>

<div class="card">
<div class="row" style="justify-content:space-between;align-items:center">
<div><strong>Progress</strong> <span id="stats" class="badge">idle</span></div>
<button class="btn secondary" onclick="clearLog()">Clear log</button>
</div>
<div id="log" class="log" style="margin-top:12px">Ready. Paste a URL above and hit Archive.
Tip: start with limit 5 to test.
Outputs in output/ : transcripts.jsonl, comments.jsonl, archive.db
</div>
</div>

<div class="card">
<h3 style="margin:0 0 10px">🔍 Search your archive (reads archive.db)</h3>
<div class="row">
<div class="grow"><input id="q" type="text" placeholder="search phrase e.g. neural networks"/></div>
<select id="table" style="background:#0e1117;border:1px solid #232830;color:#e6e8eb;padding:12px;border-radius:10px">
<option value="transcripts">transcripts</option>
<option value="comments">comments</option>
</select>
<button class="btn secondary" onclick="doSearch()">Search</button>
<button class="btn secondary" onclick="doStats()">Stats</button>
</div>
<div id="results" style="margin-top:14px"></div>
</div>
</div>

<script>
let polling = null;
function clearLog(){ document.getElementById('log').textContent=''; }
function appendLog(lines){
  const el = document.getElementById('log');
  el.textContent = lines.join("\n");
  el.scrollTop = el.scrollHeight;
}
async function poll(){
  const r = await fetch('/api/status');
  const j = await r.json();
  appendLog(j.logs);
  document.getElementById('stats').textContent = j.running ? 'running...' : (j.stats.videos ? `${j.stats.videos} videos, ${j.stats.transcripts||0} transcripts, ${j.stats.comments||0} comments` : 'idle');
  document.getElementById('go').disabled = j.running;
  if(j.running) setTimeout(poll, 800);
}
document.getElementById('go').onclick = async () => {
  const url = document.getElementById('url').value.trim();
  if(!url){ alert('Paste a YouTube URL'); return; }
  const limit = parseInt(document.getElementById('limit').value||'0');
  const skipT = document.getElementById('skipT').checked;
  const skipC = document.getElementById('skipC').checked;
  document.getElementById('log').textContent = 'Starting...';
  await fetch('/api/archive', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({url, limit, skip_transcripts: skipT, skip_comments: skipC})});
  poll();
};
async function doSearch(){
  const q = document.getElementById('q').value.trim();
  if(!q) return;
  const table = document.getElementById('table').value;
  const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&table=${table}&limit=20`);
  const j = await r.json();
  const el = document.getElementById('results');
  if(j.error){ el.innerHTML = `<div style="color:#ff6b6b">${j.error}</div>`; return; }
  if(!j.results.length){ el.innerHTML = '<div class="snippet">No results. Try AND/OR or * wildcard.</div>'; return; }
  el.innerHTML = j.results.map((row,i)=>`
    <div class="result">
      <div><b>${i+1}. [${row.video_id||''}]</b> ${row.title||''}</div>
      <div><a href="${row.url||'https://www.youtube.com/watch?v='+row.video_id}" target="_blank">${row.url||'https://www.youtube.com/watch?v='+row.video_id}</a></div>
      <div class="snippet">${(row.text||'').slice(0,400).replace(/</g,'&lt;')}</div>
    </div>`).join('');
}
async function doStats(){
  const r = await fetch('/api/search?q=__stats__');
  const j = await r.json();
  document.getElementById('results').innerHTML = `<pre style="white-space:pre-wrap;font-size:12px">${JSON.stringify(j.stats,null,2)}</pre>`;
}
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
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        if parsed.path in ("/","/index.html"):
            self._set_headers("text/html")
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        elif parsed.path == "/api/status":
            self._set_headers()
            with LOCK:
                data = {"running": STATE["running"], "logs": STATE["logs"][-200:], "stats": STATE["stats"], "error": STATE["error"]}
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif parsed.path.startswith("/api/search"):
            qs = parse_qs(parsed.query)
            q = qs.get("q",[""])[0]
            table = qs.get("table",["transcripts"])[0]
            limit = int(qs.get("limit",["20"])[0])
            db_path = OUT_DIR / "archive.db"
            if q == "__stats__":
                if not db_path.exists():
                    self._set_headers()
                    self.wfile.write(json.dumps({"stats":{"error":"No DB yet"},"results":[]}).encode("utf-8"))
                    return
                import sqlite3
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                stats={}
                for t in ["transcripts","comments","videos"]:
                    try:
                        cur.execute(f"SELECT COUNT(*) FROM {t}")
                        stats[t]=cur.fetchone()[0]
                    except:
                        stats[t]=0
                conn.close()
                self._set_headers()
                self.wfile.write(json.dumps({"stats":stats,"results":[]}).encode("utf-8"))
                return
            if not db_path.exists():
                self._set_headers()
                self.wfile.write(json.dumps({"error":"No archive.db yet","results":[]}).encode("utf-8"))
                return
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            try:
                fts = f"{table}_fts"
                cur.execute(f"SELECT t.*, rank FROM {fts} JOIN {table} t ON t.rowid = {fts}.rowid WHERE {fts} MATCH ? ORDER BY rank LIMIT ?", (q, limit))
                rows=[dict(r) for r in cur.fetchall()]
                for r in rows:
                    if not r.get("url") and r.get("video_id"):
                        r["url"]=f"https://www.youtube.com/watch?v={r['video_id']}"
                self._set_headers()
                self.wfile.write(json.dumps({"results":rows}).encode("utf-8"))
            except Exception as e:
                try:
                    cur.execute(f"SELECT * FROM {table} WHERE text LIKE ? LIMIT ?", (f"%{q}%", limit))
                    rows=[dict(r) for r in cur.fetchall()]
                    for r in rows:
                        if not r.get("url") and r.get("video_id"):
                            r["url"]=f"https://www.youtube.com/watch?v={r['video_id']}"
                    self._set_headers()
                    self.wfile.write(json.dumps({"results":rows}).encode("utf-8"))
                except Exception as e2:
                    self._set_headers()
                    self.wfile.write(json.dumps({"error":str(e2),"results":[]}).encode("utf-8"))
            finally:
                conn.close()
        else:
            self._set_headers("text/plain",404)
            self.wfile.write(b"Not found")
    def do_POST(self):
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        if parsed.path == "/api/archive":
            length=int(self.headers.get("Content-Length",0))
            body=self.rfile.read(length).decode("utf-8")
            try:
                data=json.loads(body)
                url=data.get("url","").strip()
                limit=int(data.get("limit",0))
                skip_c=bool(data.get("skip_comments",False))
                skip_t=bool(data.get("skip_transcripts",False))
                if not url:
                    raise ValueError("URL required")
                with LOCK:
                    if STATE["running"]:
                        self._set_headers(code=409)
                        self.wfile.write(json.dumps({"error":"Already running"}).encode("utf-8"))
                        return
                t=threading.Thread(target=archive_job, args=(url,limit,skip_c,skip_t,OUT_DIR), daemon=True)
                t.start()
                self._set_headers()
                self.wfile.write(json.dumps({"ok":True}).encode("utf-8"))
            except Exception as e:
                self._set_headers(code=400)
                self.wfile.write(json.dumps({"error":str(e)}).encode("utf-8"))
        else:
            self._set_headers("text/plain",404)
            self.wfile.write(b"Not found")
    def log_message(self, format, *args):
        return

def find_free_port(start=8765):
    for port in range(start, start+20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start

def run_server():
    port=find_free_port()
    server=HTTPServer(("127.0.0.1",port),Handler)
    url=f"http://127.0.0.1:{port}"
    print(f"\n=== yt-archive-seed UI ===")
    print(f"Python: {sys.executable}")
    print(f"Open {url}")
    print(f"Output: {OUT_DIR.resolve()}")
    print(f"Press Ctrl+C to stop\n")
    try:
        webbrowser.open_new_tab(url)
    except:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        server.shutdown()

def main_cli():
    parser=argparse.ArgumentParser(description="YouTube bulk archive -> jsonl + sqlite FTS5")
    parser.add_argument("--url", help="Playlist/channel/video URL")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--skip-comments", action="store_true")
    parser.add_argument("--skip-transcripts", action="store_true")
    parser.add_argument("--out-dir", default="output")
    parser.add_argument("--install-deps", action="store_true", help="Force reinstall deps")
    args=parser.parse_args()

    if args.install_deps:
        _auto_install(REQUIRED_PKGS, log_fn=print)
        return

    if not args.url:
        run_server()
        return
    out_dir=Path(args.out_dir)
    archive_job(args.url, limit=args.limit, skip_comments=args.skip_comments, skip_transcripts=args.skip_transcripts, out_dir=out_dir)

if __name__ == "__main__":
    main_cli()
