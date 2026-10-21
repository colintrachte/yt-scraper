
# yt-scraper — local YouTube knowledge archive

Feed it a playlist URL and it scrapes everything in there.

A single Windows launcher is included.

### What it does
- Input: public video, Shorts, Live, playlist, or channel URLs; `@handles` and bare video IDs also work
- Output: `transcripts.jsonl + comments.jsonl + archive.db` (SQLite FTS5 searchable by grep and AI)

Stack: yt-dlp, youtube-transcript-api, youtube-comment-downloader, SQLite FTS5, and optional LLM summaries. The shipped Python implementation does not require Node or ytranscript.

Cost: $0.

## Windows Quick Start

1. Install prerequisites:
   - Python 3.10+ from https://www.python.org/downloads/  CHECK "Add python.exe to PATH"

2. Double-click `START_YouTubeKnowledge.bat`. This is the only launcher you need. It creates the project environment and installs any missing dependencies automatically before opening the dashboard. Ready environments skip installation. Existing `venv` environments remain supported; new environments use `.venv`.

3. Run a playlist:
   ```
   START_YouTubeKnowledge.bat "https://www.youtube.com/playlist?list=PLxxx"
   ```
   Double-click `START_YouTubeKnowledge.bat` without arguments to open the local dashboard. The optional second argument is a video limit; omitting it uses zero (unlimited).

   The browser opens after the server is ready. Existing instances of this checkout are reused, and busy ports get a safe fallback. Keep the console open while using the dashboard; Ctrl+C stops a server started by that console. Diagnostics are in `logs/setup.log` and `logs/server.log`.

   A video record is saved as soon as its ID is discovered, even when YouTube blocks optional metadata, transcript, or comment requests. Partial records remain visible in the dashboard's **Failures** tab and are retried on a later run where appropriate.

   ```powershell
   .\START_YouTubeKnowledge.bat --check
   .\START_YouTubeKnowledge.bat --setup-only --update
   .\START_YouTubeKnowledge.bat --no-browser --port 8768
   ```

   Limit test:
   ```
   START_YouTubeKnowledge.bat "https://www.youtube.com/playlist?list=PLxxx" 20
   ```

4. Search:
   ```
   python search.py "neural networks" --type transcripts
   python search.py "python" --type videos --tag python --limit 20 --offset 0
   python search.py --stats
   python search.py --rag
   ```

## Outputs
- `output/transcripts.jsonl` - each line: video_id, title, url, text, segments[{text,start,duration}]
- `output/comments.jsonl` - video_id, author, text, votes, time
- `output/archive.db` - SQLite with FTS5 tables transcripts_fts, comments_fts

## Summaries and retrieval

Optional summaries support Ollama, LM Studio, and a configurable OpenAI-compatible endpoint. Choose a local endpoint to keep summary input on this machine. Long transcripts are currently truncated when building summary prompts.

Search types are `summaries`, `videos`, `transcripts`, and `all`. `all` currently prefers summaries and then videos; it does not merge all source matches. Comment search is not exposed by this CLI. Tags match exact values without case sensitivity, before pagination. RAG export requires existing summaries and writes `rag_dataset.jsonl` and `rag_context.md`.

## Offline regression tests

```powershell
python -m pip install pytest
python -m pytest -q tests
```

Tests cover filtered pagination, transcript fallback search, query-free RAG export, WAL-aware backups, and cancellation rollback. They do not contact YouTube or an LLM. Live collection depends on upstream availability and access limits.
