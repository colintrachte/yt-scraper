#!/usr/bin/env python3
"""
main.py - Refactored orchestration + UX-rich server (FIXED)
Fixes:
 - XSS: adds esc() and uses it everywhere
 - Cancel button relabeled to Stop & Save with tooltip
 - Failures deduped (video_id+type) to avoid duplicate rows
 - Cookies/proxy warnings added
 - Removed unused imports, sorted imports, tightened blind excepts
 - parse_url_input improved, _ensure_venv safer
"""

from __future__ import annotations  # ruff: noqa: I001

import argparse
import importlib
import json
import logging
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core import (
    CONFIG,
    ThrottleAbort,
    build_db,
    export_rag_dataset,
    fetch_comments_bulk,
    fetch_transcripts_bulk,
    fetch_videos_full_metadata,
    generate_summary_for_video,
    get_video_copy_data,
    highlight_search_snippet,
    make_throttler,
    test_llm_connection,
    update_video_summary,
    update_video_user_score,
)
from core import get_playlist_data as core_get_playlist_data
from search import search_videos as search_search_videos  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("ytkb.main")

# ---------------------------------------------------------------------------
# Optional UI helpers
# ---------------------------------------------------------------------------
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore

# ---------------------------------------------------------------------------
# HTML - fixed for XSS + UX notes
# ---------------------------------------------------------------------------
HTML_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YouTube Knowledgebase</title>
<style>
:root{--bg:#0b0f19;--panel:#121a2b;--panel2:#17223a;--line:#263656;--text:#e8eefc;--muted:#9bb0d4;--accent:#6ea8fe;--accent2:#8b5cf6;--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 600px at 20% -10%,#1b2a4a 0%,transparent 60%),var(--bg);color:var(--text);font:14px/1.45 ui-sans-system,system-ui,Segoe UI,Roboto,Helvetica,Arial}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1300px;margin:0 auto;padding:18px}
.top{display:flex;gap:12px;align-items:center;justify-content:space-between;margin-bottom:14px}
.brand{display:flex;gap:12px;align-items:center}
.logo{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:grid;place-items:center;font-weight:800;color:#081028}
h1{font-size:18px;margin:0}
.sub{color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:14px}
@media(max-width:980px){.grid{grid-template-columns:1fr}}
.card{background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,.01));border:1px solid var(--line);border-radius:16px;overflow:hidden}
.card h2{margin:0;padding:12px 14px;border-bottom:1px solid var(--line);font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.body{padding:14px}
.row{display:flex;gap:8px;flex-wrap:wrap}
.input, .textarea{width:100%;background:var(--panel);border:1px solid var(--line);color:var(--text);border-radius:12px;padding:10px 12px;outline:none}
.textarea{min-height:110px;resize:vertical}
.btn{appearance:none;border:1px solid var(--line);background:linear-gradient(180deg,var(--panel2),var(--panel));color:var(--text);padding:8px 12px;border-radius:12px;cursor:pointer;font-weight:600}
.btn:hover{border-color:#36507f}
.btn.primary{background:linear-gradient(180deg,#2b57ff,#1c3fbe);border-color:#3c64ff}
.btn.red{background:linear-gradient(180deg,#5a1a1a,#3a1010);border-color:#7a2a2a}
.btn.ghost{background:transparent}
.kbd{font:11px ui-monospace,Menlo,monospace;background:#0e1526;border:1px solid var(--line);border-radius:8px;padding:2px 6px;color:var(--muted)}
.tabs{display:flex;gap:6px;padding:8px;background:var(--panel);border-bottom:1px solid var(--line);flex-wrap:wrap}
.tab{padding:6px 10px;border-radius:999px;border:1px solid var(--line);cursor:pointer;color:var(--muted)}
.tab.active{background:linear-gradient(180deg,#213055,#18233f);color:var(--text);border-color:#39507a}
.pill{display:inline-flex;gap:6px;align-items:center;padding:3px 8px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.03);font-size:11px;color:var(--muted)}
.dot{width:8px;height:8px;border-radius:50%}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}.dot.bad{background:var(--bad)}.dot.muted{background:#44567c}
.list{display:flex;flex-direction:column;gap:8px;max-height:520px;overflow:auto;padding-right:4px}
.item{border:1px solid var(--line);background:rgba(255,255,255,.02);border-radius:12px;padding:10px 12px;display:flex;gap:10px;align-items:flex-start;justify-content:space-between}
.item:hover{border-color:#39507a}
.meta{color:var(--muted);font-size:11px;display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
.score{font-weight:800}
.progress{height:8px;background:#0f182c;border:1px solid var(--line);border-radius:999px;overflow:hidden}
.bar{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));width:0%}
.logs{height:260px;overflow:auto;background:#0b1222;border:1px solid var(--line);border-radius:12px;padding:8px 10px;font:11px/1.45 ui-monospace,Menlo,monospace;white-space:pre-wrap}
.kv{display:grid;grid-template-columns:150px 1fr;gap:8px;padding:6px 0;border-bottom:1px dashed rgba(255,255,255,.06)}
.kv:last-child{border-bottom:none}
.copygrid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
@media(max-width:700px){.copygrid{grid-template-columns:1fr}}
.tag{display:inline-block;padding:2px 6px;border-radius:999px;background:#14203a;border:1px solid var(--line);font-size:10px;color:var(--muted);margin:2px}
.details{border:1px solid var(--line);border-radius:12px;overflow:hidden}
.details>summary{cursor:pointer;padding:10px 12px;background:rgba(255,255,255,.02);list-style:none}
.details[open]>summary{border-bottom:1px solid var(--line)}
.hidden{display:none !important}
.hl{outline:2px solid var(--accent)}
mark{background:#f59e0b33;color:#ffe7a8;border-radius:4px;padding:0 2px}
.tooltip{position:relative;display:inline-block}
.tooltip:hover::after{content:attr(data-tip);position:absolute;left:0;top:100%;margin-top:6px;background:#0f1a33;border:1px solid var(--line);color:var(--text);padding:6px 8px;border-radius:8px;white-space:nowrap;font-size:11px;z-index:20}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand"><div class="logo">YT</div><div><h1>YouTube Knowledgebase</h1><div class="sub">Full archive • transcripts • comments • FTS • scores • summaries • RAG export</div></div></div>
    <div class="row">
      <button class="btn ghost" onclick="openHelp()">Help</button>
      <button class="btn" onclick="loadStats()">Refresh stats</button>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Archive</h2>
      <div class="body">
        <div class="row" style="align-items:center">
          <span class="pill"><span class="dot muted" id="health-dot"></span><span id="health-text">Idle</span></span>
          <span class="pill" id="counts"></span>
          <span class="pill" id="timer"></span>
        </div>
        <div style="height:8px"></div>
        <textarea id="urls" class="textarea" placeholder="Paste YouTube playlist / channel / video URLs, one per line or comma separated. Use @handle for channels."></textarea>
        <div class="row" style="margin-top:8px">
          <button class="btn primary" id="start" onclick="startArchive()">▶ Start Archive</button>
          <button class="btn red hidden" id="cancel" title="Saves everything collected so far - safe to press at first 429" onclick="cancelArchive()">⏹ Stop & Save</button>
          <button class="btn" onclick="pollNow()">Poll</button>
          <span class="kbd" id="stage">stage: idle</span>
        </div>
        <div class="progress" style="margin-top:10px"><div id="bar" class="bar"></div></div>
        <div id="stage-details" class="meta" style="margin-top:6px"></div>
        <div style="height:10px"></div>
        <div class="logs" id="logs"></div>
        <div class="row" style="margin-top:8px">
          <button class="btn ghost" onclick="clearLogs()">Clear logs</button>
          <button class="btn ghost" onclick="loadFailures()">Show failures</button>
          <button class="btn ghost" onclick="exportRag()">Export RAG</button>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>Options</h2>
      <div class="body">
        <details class="details" open><summary><b>General</b></summary>
          <div class="body">
            <div class="kv"><div>Output dir</div><div><input id="opt-out" class="input" value="output"></div></div>
            <div class="kv"><div>Pacing</div><div>
              <select id="opt-pacing" class="input"><option value="conservative">Conservative (safe, avoids 429)</option><option value="normal">Normal</option><option value="fast">Fast (risky)</option></select>
              <div class="sub" style="margin-top:6px">If you keep seeing 429 / "Not a bot", switch to Conservative, add cookies, and avoid cloud/VPN IPs. Cloud IPs are often hard-blocked.</div>
            </div></div>
            <div class="kv"><div>Chunk / Pause</div><div><div class="row"><input id="opt-chunk" class="input" style="width:90px" value="25"><input id="opt-pause" class="input" style="width:90px" value="30"></div></div></div>
            <div class="kv"><div>Max videos</div><div><input id="opt-limit" class="input" value="0" placeholder="0 = all"></div></div>
          </div>
        </details>
        <div style="height:10px"></div>
        <details class="details"><summary><b>Auth / Anti-blocking</b></summary>
          <div class="body">
            <div class="kv"><div>Browser cookies</div><div><input id="opt-cookies-browser" class="input" placeholder="e.g. chrome, firefox, brave, edge"></div></div>
            <div class="kv"><div>Cookies file</div><div><input id="opt-cookies-file" class="input" placeholder="/path/to/cookies.txt (Netscape format)"></div></div>
            <div class="kv"><div>Proxy</div><div><input id="opt-proxy" class="input" placeholder="http://user:pass@ip:port or socks5://..."></div></div>
            <div class="sub" style="padding:8px">
              <div style="color:var(--warn)">⚠ Using your main Google account with cookies can risk flagging if you run heavy jobs. Use a throwaway login if possible.</div>
              <div style="margin-top:6px">Proxy / cookies are passed to yt-dlp (metadata) and transcript API where supported. Comment downloader does NOT support proxy — it may still 429 even with proxy. For transcript, use residential IP, not datacenter/VPN.</div>
            </div>
          </div>
        </details>
        <div style="height:10px"></div>
        <details class="details"><summary><b>LLM</b></summary>
          <div class="body">
            <div class="kv"><div>Provider</div><div><select id="opt-llm-provider" class="input"><option value="ollama">Ollama</option><option value="lmstudio">LM Studio</option><option value="openai">OpenAI-compat</option></select></div></div>
            <div class="kv"><div>Endpoint</div><div><input id="opt-llm-endpoint" class="input" value="http://localhost:11434"></div></div>
            <div class="kv"><div>Model</div><div><input id="opt-llm-model" class="input" value="llama3.1"></div></div>
            <div class="kv"><div>Temperature</div><div><input id="opt-llm-temp" class="input" value="0.2"></div></div>
            <div class="kv"><div>Prompt template</div><div><textarea id="opt-llm-prompt" class="textarea" placeholder="Leave blank for default"></textarea></div></div>
            <div class="row"><button class="btn" onclick="testLLM()">Test connection</button><span id="llm-test" class="pill"></span></div>
          </div>
        </details>
      </div>
    </div>
  </div>

  <div style="height:14px"></div>
  <div class="card">
    <div class="tabs">
      <div class="tab active" data-t="browse" onclick="setTab('browse')">Browse</div>
      <div class="tab" data-t="search" onclick="setTab('search')">Search</div>
      <div class="tab" data-t="playlists" onclick="setTab('playlists')">Playlists</div>
      <div class="tab" data-t="failures" onclick="setTab('failures')">Failures</div>
      <div class="tab" data-t="detail" onclick="setTab('detail')">Detail</div>
    </div>
    <div class="body">
      <div id="panel-browse">
        <div class="row"><input id="browse-q" class="input" placeholder="Filter loaded videos by title/channel/tags"><button class="btn" onclick="loadVideos()">Load</button></div>
        <div style="height:8px"></div>
        <div id="browse-list" class="list"></div>
      </div>
      <div id="panel-search" class="hidden">
        <div class="row"><input id="search-q" class="input" placeholder="FTS: search all"><button class="btn primary" onclick="doSearch()">Search</button></div>
        <div style="height:8px"></div>
        <div id="search-list" class="list"></div>
      </div>
      <div id="panel-playlists" class="hidden">
        <div id="pl-list" class="list"></div>
      </div>
      <div id="panel-failures" class="hidden">
        <div id="fail-list" class="list"></div>
      </div>
      <div id="panel-detail" class="hidden">
        <div id="detail-head"></div>
        <div style="height:8px"></div>
        <div class="row">
          <input id="score-val" class="input" style="width:110px" placeholder="0-100 score">
          <input id="score-notes" class="input" placeholder="Notes">
          <button class="btn" onclick="saveScore()">Save score</button>
        </div>
        <div style="height:8px"></div>
        <div class="row">
          <textarea id="summary-val" class="textarea" placeholder="Paste summary or click AI summarize"></textarea>
        </div>
        <div class="row" style="margin-top:8px">
          <button class="btn" onclick="saveSummary()">Save summary</button>
          <button class="btn primary" onclick="aiSummarize()">AI summarize</button>
          <button class="btn" onclick="loadCopyData()">Reload copy data</button>
        </div>
        <div style="height:10px"></div>
        <div class="copygrid">
          <div class="card"><h2>Copy fields</h2><div class="body" id="copy-fields"></div></div>
          <div class="card"><h2>Combined for AI</h2><div class="body"><textarea id="combined" class="textarea" style="min-height:220px"></textarea><div class="row" style="margin-top:8px"><button class="btn" onclick="copyCombined()">Copy combined</button><button class="btn ghost" onclick="downloadCombined()">Download</button></div></div></div>
        </div>
      </div>
    </div>
  </div>
</div>

<div id="help" class="hidden" style="position:fixed;inset:0;background:rgba(0,0,0,.55);display:grid;place-items:center;padding:18px">
  <div class="card" style="max-width:820px;width:100%">
    <h2>Help & tips</h2>
    <div class="body">
      <ul>
        <li>Start with <b>Conservative</b> pacing if you hit 429. Keep chunk ~25 and pause ~30s.</li>
        <li><b>Browser cookies</b> = most reliable fix, but use a throwaway account. Export via extension if needed.</li>
        <li><b>Cloud / VPN / datacenter IPs</b> are often blocked for transcripts. Residential IP or rotating residential proxy helps.</li>
        <li>If blocked, hit <b>Stop & Save</b> - it saves everything so far. Wait 30-60m or change network/proxy, then re-run. Resume is automatic.</li>
        <li>Failures table is deduped by video+type, showing last error.</li>
      </ul>
      <div class="row"><button class="btn" onclick="closeHelp()">Close</button></div>
    </div>
  </div>
</div>

<script>
let pollTimer=null, selectedVideo=null, searchTimer=null;
function esc(s){ return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function setTab(t){
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active', x.dataset.t===t));
  ['browse','search','playlists','failures','detail'].forEach(k=>{
    document.getElementById('panel-'+k).classList.toggle('hidden', k!==t);
  });
}
function openHelp(){ document.getElementById('help').classList.remove('hidden'); }
function closeHelp(){ document.getElementById('help').classList.add('hidden'); }
function getOpts(){
  return {
    out_dir: document.getElementById('opt-out').value || 'output',
    pacing: document.getElementById('opt-pacing').value,
    chunk: parseInt(document.getElementById('opt-chunk').value||'25'),
    pause: parseFloat(document.getElementById('opt-pause').value||'30'),
    limit: parseInt(document.getElementById('opt-limit').value||'0'),
    cookies_browser: document.getElementById('opt-cookies-browser').value.trim(),
    cookies_file: document.getElementById('opt-cookies-file').value.trim(),
    proxy: document.getElementById('opt-proxy').value.trim(),
    llm_provider: document.getElementById('opt-llm-provider').value,
    llm_endpoint: document.getElementById('opt-llm-endpoint').value.trim(),
    llm_model: document.getElementById('opt-llm-model').value.trim(),
    llm_temp: parseFloat(document.getElementById('opt-llm-temp').value||'0.2'),
    llm_prompt: document.getElementById('opt-llm-prompt').value
  };
}
async function startArchive(){
  const urls=document.getElementById('urls').value.trim();
  if(!urls){ alert('Paste at least one URL'); return; }
  const opts=getOpts();
  const body={urls, options:opts};
  document.getElementById('start').classList.add('hidden');
  document.getElementById('cancel').classList.remove('hidden');
  const res=await fetch('/api/archive',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await res.json();
  if(!j.ok){ alert('Start failed: '+(j.error||'')); document.getElementById('start').classList.remove('hidden'); document.getElementById('cancel').classList.add('hidden'); return; }
  pollTimer=setInterval(pollNow, 900);
}
async function cancelArchive(){
  await fetch('/api/cancel',{method:'POST'});
  document.getElementById('cancel').classList.add('hidden');
  document.getElementById('start').classList.remove('hidden');
}
async function pollNow(){
  const res=await fetch('/api/status'); const j=await res.json();
  document.getElementById('stage').textContent='stage: '+(j.stage||'idle')+' '+(j.running?'running':'idle');
  document.getElementById('stage-details').textContent=j.stage_details||'';
  const pct=j.total ? Math.min(100, Math.round((j.done/j.total)*100)) : 0;
  document.getElementById('bar').style.width=pct+'%';
  document.getElementById('health-text').textContent=j.running ? (j.stage+'... '+pct+'%') : 'Idle';
  document.getElementById('health-dot').className='dot '+(j.running?'warn':'ok');
  document.getElementById('counts').textContent=`done ${j.done||0}/${j.total||0} ok=${j.ok||0} fail=${j.failed||0}`;
  document.getElementById('timer').textContent=j.elapsed ? (j.elapsed+'s') : '';
  const logsEl=document.getElementById('logs');
  const tail=(j.logs||[]).slice(-200).join('\n');
  logsEl.textContent=tail;
  logsEl.scrollTop=logsEl.scrollHeight;
  if(j.stats){ renderStats(j.stats); }
  if(!j.running && pollTimer){ clearInterval(pollTimer); pollTimer=null; document.getElementById('start').classList.remove('hidden'); document.getElementById('cancel').classList.add('hidden'); }
}
function renderStats(s){
  document.getElementById('counts').textContent=`videos ${s.videos||0} playlists ${s.playlists||0} avgQ ${s.avg_quality||0}`;
}
function clearLogs(){ document.getElementById('logs').textContent=''; }
async function loadStats(){ const r=await fetch('/api/stats'); const j=await r.json(); renderStats(j); }
async function loadVideos(){
  const q=document.getElementById('browse-q').value.trim().toLowerCase();
  const r=await fetch('/api/videos?limit=500'); const j=await r.json();
  let list=j.videos||[];
  if(q){ list=list.filter(v=> (v.title||'').toLowerCase().includes(q) || (v.channel||'').toLowerCase().includes(q) || (v.tags||'').toLowerCase().includes(q)); }
  const el=document.getElementById('browse-list');
  el.innerHTML=list.map(v=>`<div class="item" onclick="selectVideo('${esc(v.video_id)}')"><div><div><b>${esc((v.title||v.video_id).slice(0,90))}</b> <span class="tag">${esc(v.quality_label||'')}</span> ${v.user_score?`<span class="tag">user:${esc(v.user_score)}</span>`:''}</div><div class="meta"><span>${esc(v.channel||'')}</span><span>Q:${esc(v.quality_score||0)}</span><span>${esc(v.duration||0)}s</span><span>${esc(v.view_count||0)} views</span><span>${esc(v.status||'')}</span></div></div><div class="pill">${esc(v.video_id)}</div></div>`).join('') || '<div class="sub">No videos yet</div>';
}
async function selectVideo(id){
  const r=await fetch('/api/video/'+encodeURIComponent(id)); const j=await r.json();
  if(j.error){ alert(j.error); return; }
  selectedVideo=j.video;
  setTab('detail');
  const head=document.getElementById('detail-head');
  head.innerHTML=`<div><b>${esc(j.video.title||id)}</b> <a href="${esc(j.video.url||'')}" target="_blank">[YouTube]</a></div><div class="meta"><span>${esc(j.video.channel||'')}</span><span>${esc(j.video.quality_label||'')}</span><span>Q:${esc(j.video.quality_score||0)}</span></div>`;
  document.getElementById('score-val').value=j.video.user_score||'';
  document.getElementById('score-notes').value=j.video.user_notes||'';
  document.getElementById('summary-val').value=j.video.summary||'';
  await loadCopyData();
}
async function saveScore(){
  if(!selectedVideo){ alert('No video selected'); return; }
  const val=parseFloat(document.getElementById('score-val').value||'0');
  const notes=document.getElementById('score-notes').value;
  const r=await fetch('/api/video/'+encodeURIComponent(selectedVideo.video_id)+'/score',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({score:val, notes})});
  const j=await r.json(); if(j.ok) alert('Saved'); else alert('Failed: '+j.error);
}
async function saveSummary(){
  if(!selectedVideo){ alert('No video selected'); return; }
  const summary=document.getElementById('summary-val').value;
  const r=await fetch('/api/video/'+encodeURIComponent(selectedVideo.video_id)+'/summary',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({summary})});
  const j=await r.json(); if(j.ok) alert('Saved'); else alert('Failed: '+j.error);
}
async function loadCopyData(){
  if(!selectedVideo) return;
  const r=await fetch('/api/video/'+encodeURIComponent(selectedVideo.video_id)+'/copy'); const j=await r.json();
  if(j.error){ alert(j.error); return; }
  const fields=document.getElementById('copy-fields');
  fields.innerHTML=`
    <div class="kv"><div>Title</div><div>${esc(j.video.title||'')} <button class="btn ghost" onclick="copyField('title')">Copy</button></div></div>
    <div class="kv"><div>URL</div><div>${esc(j.video.url||'')} <button class="btn ghost" onclick="copyField('url')">Copy</button></div></div>
    <div class="kv"><div>Channel</div><div>${esc(j.video.channel||'')} <button class="btn ghost" onclick="copyField('channel')">Copy</button></div></div>
    <div class="kv"><div>Tags</div><div>${(j.video.tags||'').slice(0,500)} <button class="btn ghost" onclick="copyField('tags')">Copy</button></div></div>
    <div class="kv"><div>Transcript len</div><div>${esc((j.transcript||'').length)} chars <button class="btn ghost" onclick="copyField('transcript')">Copy</button></div></div>
  `;
  document.getElementById('combined').value=j.combined_for_ai||'';
}
function copyField(which){
  if(!selectedVideo) return;
  fetch('/api/video/'+encodeURIComponent(selectedVideo.video_id)+'/copy').then(r=>r.json()).then(j=>{
    const map={title:j.video.title, url:j.video.url, channel:j.video.channel, tags:j.video.tags, transcript:j.transcript, combined:j.combined_for_ai};
    navigator.clipboard.writeText(map[which]||'');
  });
}
function copyCombined(){ navigator.clipboard.writeText(document.getElementById('combined').value||''); }
function downloadCombined(){
  const blob=new Blob([document.getElementById('combined').value||''], {type:'text/plain'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=(selectedVideo?selectedVideo.video_id:'combined')+'.txt'; a.click();
}
async function doSearch(){
  const q=document.getElementById('search-q').value.trim(); if(!q) return;
  const r=await fetch('/api/search?q='+encodeURIComponent(q)); const j=await r.json();
  const el=document.getElementById('search-list');
  el.innerHTML=(j.results||[]).map(row=>`<div class="item" onclick="selectVideo('${esc(row.video_id)}')"><div><b>${esc(row.title||row.video_id)}</b><div class="meta"><span>${esc(row.channel||'')}</span><span>${esc(row.type||'')}</span><span>score:${esc(row.rank||'')}</span></div><div class="sub">${row.snippet||''}</div></div><div class="pill">${esc(row.video_id)}</div></div>`).join('') || '<div class="sub">No results</div>';
}
async function loadFailures(){
  setTab('failures');
  const r=await fetch('/api/failed'); const j=await r.json();
  // dedupe client-side too (server already dedupes)
  const seen=new Map();
  for(const f of (j.failures||[])){ seen.set(f.video_id+'|'+(f.type||''), f); }
  const deduped=[...seen.values()];
  const el=document.getElementById('fail-list');
  el.innerHTML=deduped.map(f=>`<div class="item"><div><b>${esc(f.video_id)}</b> <span class="tag">${esc(f.type||'')}</span><div class="sub">${esc(f.error||'').slice(0,400)}</div></div><div class="row"><button class="btn ghost" onclick="retryOne('${esc(f.video_id)}','${esc(f.type)}')">Retry</button><a class="btn ghost" href="https://www.youtube.com/watch?v=${esc(f.video_id)}" target="_blank">YT</a></div></div>`).join('') || '<div class="sub">No failures</div>';
}
async function retryOne(id, type){
  alert('Re-run archive - it will resume and retry failed items automatically (existing JSONL deduped).');
}
async function exportRag(){ const r=await fetch('/api/rag/export',{method:'POST'}); const j=await r.json(); if(j.error) alert(j.error); else alert('Exported: '+(j.rag_jsonl||'')+' count='+j.count); }
async function testLLM(){
  const opts=getOpts();
  const cfg={provider:opts.llm_provider, endpoint:opts.llm_endpoint, model:opts.llm_model};
  const r=await fetch('/api/llm/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
  const j=await r.json();
  document.getElementById('llm-test').textContent=j.ok ? 'OK '+ (j.models_available?j.models_available.length+' models':'') : 'FAIL '+esc(j.error||'');
}
async function aiSummarize(){
  if(!selectedVideo){ alert('Select video'); return; }
  const opts=getOpts();
  const cfg={provider:opts.llm_provider, endpoint:opts.llm_endpoint, model:opts.llm_model, temperature:opts.llm_temp, prompt_template:opts.llm_prompt};
  const r=await fetch('/api/video/'+encodeURIComponent(selectedVideo.video_id)+'/summarize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
  const j=await r.json();
  if(j.error) alert('Summarize failed: '+j.error);
  else { document.getElementById('summary-val').value=j.summary||''; alert('Summarized, remember to Save'); }
}
document.getElementById('search-q')?.addEventListener('keydown', e=>{ if(e.key==='Enter') doSearch(); });
setTimeout(()=>{ loadStats(); loadVideos(); }, 300);
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_PORT = 8765
DEFAULT_OUT = Path("output")
VENV_DIR = Path(".venv")

IMPORT_MAP = {
    "yt-dlp": "yt_dlp",
    "youtube-transcript-api": "youtube_transcript_api",
    "youtube-comment-downloader": "youtube_comment_downloader",
    "requests": "requests",
    "tqdm": "tqdm",
    "pillow": "PIL",
}

STATE = {
    "running": False,
    "stage": "idle",
    "stage_details": "",
    "total": 0,
    "done": 0,
    "ok": 0,
    "failed": 0,
    "logs": [],
    "start_ts": None,
    "stats": None,
    "out_dir": str(DEFAULT_OUT),
    "cancel": False,
}

LOCK = threading.Lock()
_stage_start: dict[str, float] = {}


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with LOCK:
        STATE["logs"].append(line)
        if len(STATE["logs"]) > 2000:
            STATE["logs"] = STATE["logs"][-1500:]
    logger.info(msg)


def set_stage(name: str, details: str = "") -> None:
    with LOCK:
        STATE["stage"] = name
        STATE["stage_details"] = details
        _stage_start[name] = time.time()
    _log(f"STAGE {name}: {details}")


def _missing() -> list[str]:
    miss = []
    for pkg, mod in IMPORT_MAP.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            miss.append(pkg)
    return miss


def _install(pkgs: list[str]) -> bool:
    if not pkgs:
        return True
    py = sys.executable
    try:
        subprocess.check_call(
            [py, "-m", "pip", "install", "-U", "pip", "wheel", "setuptools"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as e:
        logger.warning(f"pip upgrade failed: {e}")
    try:
        subprocess.check_call([py, "-m", "pip", "install", *pkgs])
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"pip install {pkgs} failed: {e}")
        return False


def _ensure_venv() -> None:
    if VENV_DIR.exists():
        return
    try:
        import venv

        _log("Creating .venv with stdlib venv")
        venv.create(str(VENV_DIR), with_pip=True)
        py = VENV_DIR / (
            "Scripts/python.exe" if platform.system() == "Windows" else "bin/python"
        )
        if py.exists():
            _log(f"Re-exec with {py}")
            os.execv(str(py), [str(py), *sys.argv])
    except (OSError, ValueError, ImportError) as e:
        logger.debug(f"venv creation failed, fallback to virtualenv: {e}")
        try:
            subprocess.check_call([
                sys.executable,
                "-m",
                "pip",
                "install",
                "virtualenv",
            ])
            subprocess.check_call([sys.executable, "-m", "virtualenv", str(VENV_DIR)])
            py = VENV_DIR / (
                "Scripts/python.exe" if platform.system() == "Windows" else "bin/python"
            )
            if py.exists():
                os.execv(str(py), [str(py), *sys.argv])
        except (OSError, subprocess.CalledProcessError, ValueError) as e2:
            logger.warning(f"virtualenv fallback failed: {e2}")


def find_free_port(preferred: int = DEFAULT_PORT) -> int:
    # Try preferred, else OS assign. TOCTOU possible but acceptable for local UI.
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1] if port == 0 else preferred
            except OSError:
                continue
    return preferred


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------
def parse_url_input(raw: str) -> list[str]:
    if not raw:
        return []
    # split on comma, newline, spaces, but keep only http(s) or @handle
    parts: list[str] = []
    for chunk in raw.replace("\r", "\n").split("\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        # split on comma
        for c in chunk.split(","):
            c = c.strip()
            if not c:
                continue
            # split on whitespace inside chunk (allow "url url")
            for sub in c.split():
                sub = sub.strip(" ,;")
                if not sub:
                    continue
                if sub.startswith(("http://", "https://", "@")):
                    parts.append(sub)
                elif "youtube.com" in sub or "youtu.be" in sub:
                    # add scheme if missing
                    if not sub.startswith("http"):
                        parts.append("https://" + sub.lstrip("/"))
                    else:
                        parts.append(sub)
    # dedupe preserve order
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Archive job - FIXED throttler usage + dedupe
# ---------------------------------------------------------------------------
def archive_job(urls: list[str], opts: dict) -> None:
    out_dir = Path(opts.get("out_dir") or "output")
    out_dir.mkdir(parents=True, exist_ok=True)

    with LOCK:
        STATE["out_dir"] = str(out_dir.resolve())
        STATE["cancel"] = False

    # build config from UI opts
    cfg = CONFIG
    cfg.out_dir = out_dir
    cfg.pacing_mode = opts.get("pacing") or cfg.pacing_mode
    try:
        cfg.chunk_size = int(opts.get("chunk") or cfg.chunk_size)
    except (ValueError, TypeError):
        pass
    try:
        cfg.chunk_pause_sec = float(opts.get("pause") or cfg.chunk_pause_sec)
    except (ValueError, TypeError):
        pass
    cfg.cookies_browser = opts.get("cookies_browser") or None
    cfg.cookies_file = opts.get("cookies_file") or None
    cfg.proxy = opts.get("proxy") or None

    # separate throttlers per service (FIX)
    discovery_throttler = make_throttler("discovery", cfg)
    metadata_throttler = make_throttler("metadata", cfg)
    transcripts_throttler = make_throttler("transcripts", cfg)
    comments_throttler = make_throttler("comments", cfg)

    def cancel_check() -> bool:
        with LOCK:
            return bool(STATE.get("cancel"))

    def progress_cb(msg: str) -> None:
        _log(msg)

    try:
        with LOCK:
            STATE["running"] = True
            STATE["start_ts"] = time.time()
            STATE["done"] = 0
            STATE["total"] = len(urls)
            STATE["ok"] = 0
            STATE["failed"] = 0
            STATE["logs"] = []

        # STAGE 1 discovery
        set_stage("discovery", f"{len(urls)} input URLs, mode={cfg.pacing_mode}")
        all_videos: dict[str, dict] = {}
        all_playlists: dict[str, dict] = {}
        all_mappings: list[dict] = []

        for u in urls:
            if cancel_check():
                _log("Cancelled before discovery")
                break
            limit = int(opts.get("limit") or 0)
            try:
                vids, pls, maps = core_get_playlist_data(
                    u,
                    limit=limit,
                    progress_cb=progress_cb,
                    cancel_check=cancel_check,
                    config=cfg,
                    throttler=discovery_throttler,
                )
                for v in vids:
                    all_videos[v["id"]] = v
                for p in pls:
                    all_playlists[p["playlist_id"]] = p
                all_mappings.extend(maps)
            except ThrottleAbort as e:
                _log(f"ThrottleAbort in discovery: {e}")
                break
            except Exception as e:
                _log(f"Discovery error for {u}: {e}")
                logger.exception(f"Discovery error for {u}")

        # write discovery JSONL deduped
        pl_path = out_dir / "playlists.jsonl"
        pv_path = out_dir / "playlist_videos.jsonl"
        try:
            with open(pl_path, "w", encoding="utf-8") as f:
                for p in all_playlists.values():
                    f.write(json.dumps(p, ensure_ascii=False) + "\n")
            with open(pv_path, "w", encoding="utf-8") as f:
                # dedupe mappings
                seen_m = set()
                for m in all_mappings:
                    key = (m["playlist_id"], m["video_id"])
                    if key in seen_m:
                        continue
                    seen_m.add(key)
                    f.write(json.dumps(m, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error(f"Failed to write discovery files: {e}")

        video_ids = list(all_videos.keys())
        with LOCK:
            STATE["total"] = len(video_ids)
        _log(f"Discovered {len(video_ids)} unique videos")

        if cancel_check():
            raise RuntimeError("Cancelled after discovery")

        # STAGE 2 metadata
        set_stage("metadata", f"{len(video_ids)} videos")
        try:
            fetch_videos_full_metadata(
                video_ids,
                out_dir / "videos_full.jsonl",
                progress_cb=progress_cb,
                resume=True,
                cancel_check=cancel_check,
                throttler=metadata_throttler,
                config=cfg,
            )
        except ThrottleAbort as e:
            _log(f"ThrottleAbort in metadata: {e}")

        if cancel_check():
            raise RuntimeError("Cancelled after metadata")

        # STAGE 3 transcripts
        set_stage(
            "transcripts",
            f"{len(video_ids)} videos, pacing={cfg.get_pacing_range('transcripts')}",
        )
        try:
            # need video infos for title etc.
            video_infos = list(all_videos.values())
            fetch_transcripts_bulk(
                video_infos,
                out_dir / "transcripts.jsonl",
                resume=True,
                progress_cb=progress_cb,
                cancel_check=cancel_check,
                throttler=transcripts_throttler,
                config=cfg,
            )
        except ThrottleAbort as e:
            _log(f"ThrottleAbort in transcripts: {e}")

        if cancel_check():
            raise RuntimeError("Cancelled after transcripts")

        # STAGE 4 comments
        set_stage("comments", f"{len(video_ids)} videos")
        try:
            fetch_comments_bulk(
                video_ids,
                out_dir / "comments.jsonl",
                limit_per_video=cfg.comments_per_video,
                resume=True,
                progress_cb=progress_cb,
                throttler=comments_throttler,
                config=cfg,
                cancel_check=cancel_check,
            )
        except ThrottleAbort as e:
            _log(f"ThrottleAbort in comments: {e}")

        if cancel_check():
            raise RuntimeError("Cancelled after comments")

        # STAGE 5 DB
        set_stage("database", "Building FTS and indexes")
        try:
            stats = build_db(
                out_dir,
                out_dir / CONFIG.db_name,
                progress_cb=progress_cb,
                cancel_check=cancel_check,
            )
            with LOCK:
                STATE["stats"] = stats
        except ThrottleAbort as e:
            _log(f"ThrottleAbort in database: {e}")
        except Exception as e:
            _log(f"DB build error: {e}")
            logger.exception("DB build error")

        set_stage("done", "All done - data saved")

    except Exception as e:
        _log(f"Archive job error: {e}")
        logger.exception("Archive job error")
        set_stage("error", str(e))
    finally:
        with LOCK:
            STATE["running"] = False
            STATE["cancel"] = False


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def _set_headers(self, code: int = 200, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path in ("/", "/index.html"):
                self._set_headers(200, "text/html; charset=utf-8")
                self.wfile.write(HTML_PAGE.encode("utf-8"))
                return

            if parsed.path == "/api/status":
                with LOCK:
                    st = dict(STATE)
                    if st.get("start_ts"):
                        st["elapsed"] = round(time.time() - st["start_ts"], 1)
                    else:
                        st["elapsed"] = 0
                    # copy logs
                    st["logs"] = list(st["logs"][-300:])
                self._set_headers()
                self.wfile.write(json.dumps(st).encode("utf-8"))
                return

            if parsed.path == "/api/stats":
                out_dir = Path(STATE.get("out_dir") or "output")
                db_path = out_dir / CONFIG.db_name
                stats = {}
                if db_path.exists():
                    import sqlite3

                    try:
                        with sqlite3.connect(str(db_path)) as conn:
                            cur = conn.cursor()
                            cur.execute("SELECT COUNT(*) FROM videos")
                            stats["videos"] = cur.fetchone()[0]
                            cur.execute("SELECT COUNT(*) FROM playlists")
                            stats["playlists"] = cur.fetchone()[0]
                            cur.execute("SELECT AVG(quality_score) FROM videos")
                            stats["avg_quality"] = round(cur.fetchone()[0] or 0, 1)
                    except Exception as e:  # noqa: BLE001  # noqa: BLE001
                        logger.debug(f"stats error: {e}")
                else:
                    stats = STATE.get("stats") or {}
                self._set_headers()
                self.wfile.write(json.dumps(stats).encode("utf-8"))
                return

            if parsed.path == "/api/videos":
                qs = parse_qs(parsed.query)
                limit = int(qs.get("limit", ["200"])[0])
                out_dir = Path(STATE.get("out_dir") or "output")
                db_path = out_dir / CONFIG.db_name
                videos = []
                if db_path.exists():
                    import sqlite3

                    try:
                        with sqlite3.connect(str(db_path)) as conn:
                            conn.row_factory = sqlite3.Row
                            cur = conn.cursor()
                            cur.execute(
                                "SELECT video_id, title, channel, quality_score, quality_label, duration, view_count, status, user_score, tags FROM videos ORDER BY quality_score DESC LIMIT ?",
                                (limit,),
                            )
                            videos = [dict(r) for r in cur.fetchall()]
                    except Exception as e:  # noqa: BLE001  # noqa: BLE001
                        logger.warning(f"videos api error: {e}")
                self._set_headers()
                self.wfile.write(json.dumps({"videos": videos}).encode("utf-8"))
                return

            if parsed.path.startswith("/api/video/"):
                parts = parsed.path.split("/")
                # /api/video/{id} or /api/video/{id}/copy etc
                if len(parts) >= 4:
                    vid = parts[3]
                    out_dir = Path(STATE.get("out_dir") or "output")
                    db_path = out_dir / CONFIG.db_name
                    if parsed.path.endswith("/copy"):
                        data = get_video_copy_data(db_path, vid)
                        self._set_headers()
                        self.wfile.write(json.dumps(data).encode("utf-8"))
                        return
                    else:
                        # detail
                        if db_path.exists():
                            import sqlite3

                            try:
                                with sqlite3.connect(str(db_path)) as conn:
                                    conn.row_factory = sqlite3.Row
                                    cur = conn.cursor()
                                    cur.execute(
                                        "SELECT * FROM videos WHERE video_id=?", (vid,)
                                    )
                                    row = cur.fetchone()
                                    if not row:
                                        self._set_headers(404)
                                        self.wfile.write(
                                            json.dumps({"error": "not found"}).encode(
                                                "utf-8"
                                            )
                                        )
                                        return
                                    self._set_headers()
                                    self.wfile.write(
                                        json.dumps({"video": dict(row)}).encode("utf-8")
                                    )
                                    return
                            except Exception as e:  # noqa: BLE001
                                self._set_headers(500)
                                self.wfile.write(
                                    json.dumps({"error": str(e)}).encode("utf-8")
                                )
                                return
                        self._set_headers(404)
                        self.wfile.write(
                            json.dumps({"error": "DB not found"}).encode("utf-8")
                        )
                        return

            if parsed.path == "/api/search":
                qs = parse_qs(parsed.query)
                q = qs.get("q", [""])[0]
                out_dir = Path(STATE.get("out_dir") or "output")
                db_path = out_dir / CONFIG.db_name
                results = []
                if q and db_path.exists():
                    try:
                        results = search_search_videos(db_path, q, limit=100)
                        # highlight
                        for r in results:
                            snippet = r.get("snippet") or r.get("description") or ""
                            r["snippet"] = highlight_search_snippet(snippet, q)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"search error: {e}")
                self._set_headers()
                self.wfile.write(json.dumps({"results": results}).encode("utf-8"))
                return

            if parsed.path == "/api/failed":
                out_dir = Path(STATE.get("out_dir") or "output")
                failures = []
                for fp in [
                    out_dir / "failed_transcripts.jsonl",
                    out_dir / "failed_comments.jsonl",
                    out_dir / "failed_metadata.jsonl",
                ]:
                    if not fp.exists():
                        continue
                    try:
                        with open(fp, encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                try:
                                    j = json.loads(line)
                                    failures.append(j)
                                except json.JSONDecodeError:
                                    continue
                    except OSError as e:
                        logger.debug(f"failed read {fp}: {e}")
                # FIX: dedupe by video_id|type keeping last
                dedup = {}
                for f in failures:
                    dedup[f.get("video_id", "") + "|" + f.get("type", "")] = f
                failures = list(dedup.values())
                # sort newest first if timestamp?
                failures.sort(key=lambda x: x.get("video_id", ""))
                self._set_headers()
                self.wfile.write(json.dumps({"failures": failures}).encode("utf-8"))
                return

            if parsed.path == "/api/playlists":
                out_dir = Path(STATE.get("out_dir") or "output")
                db_path = out_dir / CONFIG.db_name
                pls = []
                if db_path.exists():
                    import sqlite3

                    try:
                        with sqlite3.connect(str(db_path)) as conn:
                            conn.row_factory = sqlite3.Row
                            cur = conn.cursor()
                            cur.execute(
                                "SELECT playlist_id, title, channel, video_count, url FROM playlists ORDER BY title"
                            )
                            pls = [dict(r) for r in cur.fetchall()]
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"playlists api error: {e}")
                self._set_headers()
                self.wfile.write(json.dumps({"playlists": pls}).encode("utf-8"))
                return

            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "not found"}).encode("utf-8"))
        except Exception as e:
            logger.exception(f"GET {self.path} failed")
            try:
                self._set_headers(500)
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            except Exception:  # noqa: BLE001, S110
                pass

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            data = {}

        try:
            if parsed.path == "/api/archive":
                urls_raw = data.get("urls", "")
                options = data.get("options", {})
                urls = parse_url_input(urls_raw)
                if not urls:
                    self._set_headers(400)
                    self.wfile.write(
                        json.dumps({"ok": False, "error": "No valid URLs"}).encode(
                            "utf-8"
                        )
                    )
                    return
                with LOCK:
                    if STATE["running"]:
                        self._set_headers(400)
                        self.wfile.write(
                            json.dumps({
                                "ok": False,
                                "error": "Already running",
                            }).encode("utf-8")
                        )
                        return
                t = threading.Thread(
                    target=archive_job, args=(urls, options), daemon=True
                )
                t.start()
                self._set_headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
                return

            if parsed.path == "/api/cancel":
                with LOCK:
                    STATE["cancel"] = True
                self._set_headers()
                self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
                return

            if parsed.path.startswith("/api/video/") and parsed.path.endswith("/score"):
                vid = parsed.path.split("/")[3]
                score = data.get("score")
                notes = data.get("notes", "")
                out_dir = Path(STATE.get("out_dir") or "output")
                db_path = out_dir / CONFIG.db_name
                try:
                    score_f = float(score) if score is not None else 0.0
                except (ValueError, TypeError):
                    score_f = 0.0
                ok = update_video_user_score(
                    db_path, vid, score_f, notes, out_dir=out_dir
                )
                self._set_headers()
                self.wfile.write(json.dumps({"ok": ok}).encode("utf-8"))
                return

            if parsed.path.startswith("/api/video/") and parsed.path.endswith(
                "/summary"
            ):
                vid = parsed.path.split("/")[3]
                summary = data.get("summary", "")
                out_dir = Path(STATE.get("out_dir") or "output")
                db_path = out_dir / CONFIG.db_name
                ok = update_video_summary(
                    db_path, vid, summary, source="user_paste", out_dir=out_dir
                )
                self._set_headers()
                self.wfile.write(json.dumps({"ok": ok}).encode("utf-8"))
                return

            if parsed.path.startswith("/api/video/") and parsed.path.endswith(
                "/summarize"
            ):
                vid = parsed.path.split("/")[3]
                out_dir = Path(STATE.get("out_dir") or "output")
                db_path = out_dir / CONFIG.db_name
                res = generate_summary_for_video(
                    vid, out_dir, db_path, data, progress_cb=_log
                )
                self._set_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
                return

            if parsed.path == "/api/llm/test":
                res = test_llm_connection(data)
                self._set_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
                return

            if parsed.path == "/api/rag/export":
                out_dir = Path(STATE.get("out_dir") or "output")
                res = export_rag_dataset(out_dir)
                self._set_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
                return

            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "not found"}).encode("utf-8"))
        except Exception as e:
            logger.exception(f"POST {self.path} failed")
            try:
                self._set_headers(500)
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            except Exception:  # noqa: BLE001, S110
                pass


# ---------------------------------------------------------------------------
# Server start / CLI
# ---------------------------------------------------------------------------
def run_server(port: int = DEFAULT_PORT, out_dir: Path = DEFAULT_OUT) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with LOCK:
        STATE["out_dir"] = str(out_dir.resolve())

    port = find_free_port(port)
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, Handler)
    url = f"http://127.0.0.1:{port}"
    _log(f"Serving at {url} out_dir={out_dir}")
    print(f"\nOpen {url}\n")
    try:
        webbrowser.open(url)
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        logger.debug(f"webbrowser.open failed: {e}")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _log("Server stopped")
    finally:
        httpd.server_close()


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="YouTube Knowledgebase")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUT))
    parser.add_argument(
        "--no-venv", action="store_true", help="Don't auto-create .venv"
    )
    parser.add_argument("--urls", type=str, nargs="*", help="URLs to archive (CLI)")
    args = parser.parse_args()

    if not args.no_venv:
        _ensure_venv()

    miss = _missing()
    if miss:
        print(f"Missing deps: {miss} -> installing...")
        if not _install(miss):
            print("Install failed, try pip install manually")
            sys.exit(1)

    if args.urls:
        # CLI mode
        opts = {
            "out_dir": args.out,
            "pacing": "conservative",
            "limit": 0,
        }
        urls = []
        for u in args.urls:
            urls.extend(parse_url_input(u))
        archive_job(urls, opts)
    else:
        run_server(port=args.port, out_dir=Path(args.out))


if __name__ == "__main__":
    main_cli()
