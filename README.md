
# yt-archive-seed — bulk YouTube transcripts + comments → searchable archive

Feed it a playlist URL and it scrapes everything in there.

**Windows-first bundle** — includes .bat and .ps1 installers so you don't need bash.

### What it does
- Input: any public playlist or channel URL
- Output: `transcripts.jsonl + comments.jsonl + archive.db` (SQLite FTS5 searchable by grep and AI)

Stack: yt-dlp for IDs, ytranscript (npm) for transcripts, youtube-comment-downloader for comments.

Cost: $0.

## Windows Quick Start

1. Install prerequisites:
   - Python 3.10+ from https://www.python.org/downloads/  CHECK "Add python.exe to PATH"
   - Node.js LTS from https://nodejs.org/  (for best playlist transcript fetching)

2. Double-click `install.bat`  (or right-click PowerShell -> `install.ps1`)

3. Run a playlist:
   ```
   run.bat "https://www.youtube.com/playlist?list=PLxxx"
   ```
   Or best for playlists:
   ```
   run_ytranscript.bat "https://www.youtube.com/playlist?list=PLxxx"
   ```

   Limit test:
   ```
   run.bat "https://www.youtube.com/playlist?list=PLxxx" 20
   ```

4. Search:
   ```
   python src\search.py "neural networks"
   python src\search.py "keyword" --in comments
   ```

## Outputs
- `output/transcripts.jsonl` - each line: video_id, title, url, text, segments[{text,start,duration}]
- `output/comments.jsonl` - video_id, author, text, votes, time
- `output/archive.db` - SQLite with FTS5 tables transcripts_fts, comments_fts

## Why ytranscript?
ytranscript uses YouTube's innertube API directly, no API key, bulk + resume-safe, multiple formats, and MCP server for Claude/Cursor. It needs a file list, so we use yt-dlp to get IDs first.
