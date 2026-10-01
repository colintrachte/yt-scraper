# ytrag — local YouTube transcript harvester + hybrid RAG search

Harvest transcripts from your YouTube playlists, then search them locally with a
hybrid **BM25 + vector** engine that lives entirely in one SQLite file. No API
keys, no cloud vector DB, no data leaving your machine.

## Why this design

- **One file, three indexes.** `videos` + `chunks` metadata, `chunks_fts` (FTS5
  BM25 lexical), and `chunks_vec` (sqlite-vec dense KNN) share the same chunk id.
  Copy the `.db`, back it up, or delete it — that's the whole system.
- **Hybrid retrieval, RRF fusion.** BM25 nails exact terms (part numbers, macro
  names like `MANUAL_STEPPER`); embeddings catch paraphrase and intent. Results
  are merged with Reciprocal Rank Fusion, so there's no fragile score
  normalization between cosine distance and BM25.
- **No LLM in the query path.** Search is deterministic and millisecond-class.
  Answer synthesis (`ask`) is a separate, optional step against a local Ollama.
- **OSI-licensed, no revocable cloud dependency.** `sqlite-vec` (Apache/MIT) is
  chosen deliberately over Elastic-licensed alternatives. Embeddings run locally
  via fastembed (ONNX/CPU).
- **Source of record on disk.** Every video is archived as `raw/<id>.json` and
  `raw/<id>.md`, so the SQLite index is always rebuildable.

## Install

    pip install -r requirements.txt
    pip install -e .          # gives you the `ytrag` command
    # optional local transcription fallback:
    pip install faster-whisper   # plus ffmpeg on PATH

First run downloads the embedding model (~90 MB) once, then works offline.

## Harvest

Public/unlisted playlist:

    ytrag ingest --playlist "https://www.youtube.com/playlist?list=PLxxxx"

Private **Saved** / **Watch Later** (`list=WL`): export cookies with the
"Get cookies.txt LOCALLY" browser extension while logged in, then:

    ytrag ingest --playlist "https://www.youtube.com/playlist?list=WL" --cookies cookies.txt

Whole-account fallback if flat extraction is blocked — export via **Google
Takeout > YouTube > history/playlists (JSON)**:

    ytrag ingest --takeout watch-history.json

Or a hand-made list (one video id or URL per line):

    ytrag ingest --ids ids.txt

Add `--whisper` to transcribe caption-disabled videos locally, and `--delay 1`
for large playlists.

## Search & ask

    ytrag search "why does the Y axis drift across tool changes"
    ytrag search "MANUAL_STEPPER" --mode lexical      # exact-term
    ytrag search "petg stringing fix" --mode semantic # intent-only
    ytrag ask   "what nozzle temp for PETG on the 5M Pro?" --model llama3.1
    ytrag stats

Every result includes a `&t=<seconds>s` deep link back to the exact moment.

## 2026 caveats (read before scaling)

- The old `YouTubeTranscriptApi.get_transcript()` was **removed** in v1.0.0.
  This project uses the current `.fetch()` API.
- **PoToken / Ip blocking:** YouTube's bot detection can return
  `PoTokenRequired` / `RequestBlocked`, especially from cloud/VPS IPs. Run from a
  residential connection; those videos are recorded with status `blocked` and
  can be retried later or routed through `--whisper`.
- **Cookie auth into the transcript library is currently limited** upstream, so
  cookies reliably help *playlist enumeration* (yt-dlp) more than transcript
  fetching. Takeout + `--whisper` is the robust path for private content.
- Respect YouTube's Terms of Service; only transcribe content you have rights to.

## Layout

    ytrag/
      config.py            paths, model, chunk + fusion tunables
      db.py                schema: videos, chunks, chunks_fts, chunks_vec
      fetch.py             yt-dlp enumeration + 1.2.4 transcript fetch
      index.py             chunk -> embed -> store (all three indexes)
      search.py            BM25 + vector + RRF hybrid retrieval
      whisper_fallback.py  optional local faster-whisper
      answer.py            optional local Ollama RAG synthesis
      cli.py               ingest / search / ask / stats
