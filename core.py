#!/usr/bin/env python3
"""
core.py - Refactored knowledgebase core
- Structured logging, type hints, dataclasses
- Repository layer with context managers and migrations
- Append-only JSONL with deduplication
- Preserves DB on rebuild, single-open failure logs, transcript truncation
- Retry logic for LLM calls
"""

from __future__ import annotations

import html
import json
import logging
import math
import os
import random
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("ytkb.core")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

ProgressCb = Optional[Callable[[str], None]]
CancelCheck = Optional[Callable[[], bool]]

# ---------------------------------------------------------------------------
# Config & Pacing Profiles
# ---------------------------------------------------------------------------
PACING_PROFILES = {
    "conservative": {
        "discovery": (2.0, 4.0),
        "metadata": (3.0, 6.0),
        "transcripts": (5.0, 10.0),
        "comments": (4.0, 8.0),
    },
    "normal": {
        "discovery": (1.5, 3.0),
        "metadata": (2.0, 4.0),
        "transcripts": (3.0, 6.0),
        "comments": (3.0, 5.0),
    },
    "fast": {
        "discovery": (0.5, 1.2),
        "metadata": (1.0, 2.0),
        "transcripts": (1.5, 3.0),
        "comments": (1.5, 3.0),
    },
}


@dataclass
class AppConfig:
    out_dir: Path = field(
        default_factory=lambda: Path(os.getenv("YTK_OUT_DIR", "output"))
    )
    db_name: str = os.getenv("YTK_DB_NAME", "archive.db")
    ollama_endpoint: str = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434")
    lmstudio_endpoint: str = os.getenv("LMSTUDIO_ENDPOINT", "http://localhost:1234/v1")
    openai_compat_endpoint: str = os.getenv("OPENAI_ENDPOINT", "")
    default_provider: str = os.getenv("YTK_LLM_PROVIDER", "ollama")
    default_model: str = os.getenv("YTK_LLM_MODEL", "llama3.1")
    transcript_max_chars: int = int(os.getenv("YTK_TRANSCRIPT_MAX", "15000"))
    combined_max_chars: int = int(os.getenv("YTK_COMBINED_MAX", "20000"))
    request_timeout: int = int(os.getenv("YTK_TIMEOUT", "300"))
    llm_retries: int = int(os.getenv("YTK_LLM_RETRIES", "3"))

    # Rate limiting & Anti-blocking settings
    pacing_mode: str = os.getenv("YTK_PACING_MODE", "conservative")
    chunk_size: int = int(os.getenv("YTK_CHUNK_SIZE", "25"))
    chunk_pause_sec: float = float(os.getenv("YTK_CHUNK_PAUSE", "30.0"))
    max_retries: int = int(os.getenv("YTK_MAX_RETRIES", "3"))
    backoff_base_sec: float = float(os.getenv("YTK_BACKOFF_BASE", "5.0"))
    backoff_max_sec: float = float(os.getenv("YTK_BACKOFF_MAX", "60.0"))
    circuit_breaker_threshold: int = int(os.getenv("YTK_CIRCUIT_BREAKER", "4"))
    circuit_breaker_pause_sec: float = float(os.getenv("YTK_CB_PAUSE", "90.0"))
    cookies_file: str | None = os.getenv("YTK_COOKIES_FILE", None)
    cookies_browser: str | None = os.getenv("YTK_COOKIES_BROWSER", None)
    proxy: str | None = os.getenv("YTK_PROXY", None)
    max_breaker_trips: int = int(os.getenv("YTK_MAX_BREAKER_TRIPS", "2"))
    comments_per_video: int = int(os.getenv("YTK_COMMENTS_PER_VIDEO", "100"))
    comment_page_sleep: float = float(os.getenv("YTK_COMMENT_PAGE_SLEEP", "1.0"))

    @property
    def db_path(self) -> Path:
        return Path(self.out_dir) / self.db_name

    def get_pacing_range(self, service: str) -> tuple[float, float]:
        profile = PACING_PROFILES.get(
            self.pacing_mode.lower(), PACING_PROFILES["conservative"]
        )
        return profile.get(service, (2.0, 4.0))


CONFIG = AppConfig()

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_PREFIXES = ("PL", "UU", "OL", "LL", "RD", "FL", "UL", "PU")
SCHEMA_VERSION = 2


@dataclass
class VideoMeta:
    id: str
    title: str = ""
    url: str = ""
    channel: str = ""
    channel_id: str = ""
    uploader: str = ""
    description: str = ""
    duration: int = 0
    view_count: int = 0


@dataclass
class PlaylistRecord:
    playlist_id: str
    title: str = ""
    description: str = ""
    channel: str = ""
    channel_id: str = ""
    uploader: str = ""
    video_count: int = 0
    url: str = ""
    tags: list[str] = field(default_factory=list)
    meta_json: str = ""


@dataclass
class MappingRecord:
    playlist_id: str
    video_id: str
    position: int = 0


@dataclass
class TranscriptRecord:
    video_id: str
    title: str = ""
    url: str = ""
    channel: str = ""
    channel_id: str = ""
    language: str = "en"
    text: str = ""
    segments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class QualityDetails:
    completeness: float = 0
    authority: float = 0
    depth: float = 0
    freshness: float = 0
    consensus: float = 0
    total: float = 0
    label: str = "poor"
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers - validation & parsing
# ---------------------------------------------------------------------------
def parse_vote_count(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, int):
        return v
    s = str(v).strip().replace(",", "").lower()
    try:
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        if s.endswith("m"):
            return int(float(s[:-1]) * 1_000_000)
        return int(float(s))
    except (ValueError, TypeError) as e:
        logger.debug(f"parse_vote_count failed for {v!r}: {e}")
        return 0


def is_valid_video_id(vid: str) -> bool:
    if not vid:
        return False
    if len(vid) != 11:
        if vid.startswith(PLAYLIST_PREFIXES) and len(vid) > 11:
            return False
        if len(vid) > 11:
            return False
    return bool(VIDEO_ID_RE.match(vid))


def extract_ids_from_url(url: str) -> tuple[str | None, str | None]:
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        v = qs.get("v", [None])[0]
        p = qs.get("list", [None])[0]
        if "youtu.be" in parsed.netloc and parsed.path:
            possible = parsed.path.lstrip("/").split("/")[0].split("?")[0]
            if is_valid_video_id(possible):
                v = possible
        return v, p
    except Exception as e:
        logger.debug(f"extract_ids_from_url failed for {url}: {e}")
        return None, None


def _log(msg: str, cb: ProgressCb = None) -> None:
    logger.info(msg)
    if cb:
        try:
            cb(msg)
        except Exception as e:
            logger.debug(f"progress_cb failed: {e}")


# ---------------------------------------------------------------------------
# Rate Limiting & Throttling
# ---------------------------------------------------------------------------
class ThrottleAbort(Exception):
    """Raised when YouTube keeps blocking us. Callers save progress and stop."""


def is_rate_limit_error(e: Exception) -> bool:
    """Detects YouTube throttling, HTTP 429, RequestBlocked, IpBlocked, or bot detection."""
    msg = str(e).lower()
    err_cls = e.__class__.__name__.lower()
    patterns = [
        "429",
        "too many requests",
        "requestblocked",
        "ipblocked",
        "not a bot",
        "sign in to confirm",
        "captcha",
        "potoken",
        "rate limit",
        "temporarily blocked",
        "throttled",
        "quotaexceeded",
    ]
    return any(p in msg or p in err_cls for p in patterns)


def is_hard_block_error(e: Exception) -> bool:
    """IP-level blocks. Do NOT try a second extraction path when these happen."""
    msg = str(e).lower()
    err_cls = e.__class__.__name__.lower()
    return any(
        p in msg or p in err_cls
        for p in (
            "429",
            "too many requests",
            "requestblocked",
            "ipblocked",
            "not a bot",
        )
    )


YDL_POLITE_OPTS = {
    "quiet": True,
    "skip_download": True,
    "no_warnings": True,
    "no_playlist": True,
    # Must be False. With True, yt-dlp swallows 429 and bot errors and the
    # AdaptiveThrottler never sees them.
    "ignoreerrors": False,
    # Let AdaptiveThrottler own backoff instead of yt-dlp silently retrying.
    "retries": 1,
    "extractor_retries": 1,
    "sleep_interval_requests": 1.0,
    # Fewer requests per video: skip manifests and translated captions.
    "extractor_args": {"youtube": {"skip": ["hls", "dash", "translated_subs"]}},
}


def is_permanent_transcript_error(e: Exception) -> bool:
    """Detects errors where transcripts simply do not exist or are disabled (non-retryable)."""
    msg = str(e).lower()
    err_cls = e.__class__.__name__.lower()
    permanent_patterns = [
        "notranscriptfound",
        "transcriptsdisabled",
        "videounavailable",
        "videounplayable",
        "subtitles are disabled",
        "no transcript available",
    ]
    return any(p in msg or p in err_cls for p in permanent_patterns)


def get_ydl_opts(
    base_opts: dict[str, Any] | None = None, config: AppConfig | None = None
) -> dict[str, Any]:
    """Applies proxy and cookie configurations to yt-dlp options."""
    opts = base_opts.copy() if base_opts else {}
    cfg = config or CONFIG
    if cfg.cookies_browser:
        opts["cookiesfrombrowser"] = (cfg.cookies_browser, None, None, None)
    elif cfg.cookies_file and Path(cfg.cookies_file).exists():
        opts["cookiefile"] = str(Path(cfg.cookies_file).resolve())
    if cfg.proxy:
        opts["proxy"] = cfg.proxy
    return opts


class AdaptiveThrottler:
    """
    Manages request pacing with jitter, exponential backoff on 429/blocks,
    circuit breaker cooldowns, and chunk rest pauses.
    """

    def __init__(
        self,
        service_name: str,
        pacing_range: tuple[float, float] = (2.0, 4.0),
        chunk_size: int = 25,
        chunk_pause_sec: float = 30.0,
        max_retries: int = 3,
        backoff_base_sec: float = 5.0,
        backoff_max_sec: float = 60.0,
        circuit_breaker_threshold: int = 4,
        circuit_breaker_pause_sec: float = 90.0,
        max_breaker_trips: int = 2,
    ):
        self.service_name = service_name
        self.min_delay, self.max_delay = pacing_range
        self.chunk_size = chunk_size
        self.chunk_pause_sec = chunk_pause_sec
        self.max_retries = max_retries
        self.backoff_base_sec = backoff_base_sec
        self.backoff_max_sec = backoff_max_sec
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_pause_sec = circuit_breaker_pause_sec
        self.max_breaker_trips = max_breaker_trips
        self.breaker_trips = 0
        self.success_streak = 0
        self.processed_count = 0
        self.consecutive_blocks = 0

    def sleep_interruptible(
        self,
        duration: float,
        cancel_check: CancelCheck = None,
        progress_cb: ProgressCb = None,
        message: str = "",
    ) -> bool:
        """Sleeps in small slices (0.25s) so cancel_check is promptly honored."""
        if duration <= 0:
            return True
        if message and progress_cb:
            _log(message, progress_cb)
        step = 0.25
        elapsed = 0.0
        while elapsed < duration:
            if cancel_check and cancel_check():
                return False
            time.sleep(min(step, duration - elapsed))
            elapsed += step
        return True

    def pace(
        self, cancel_check: CancelCheck = None, progress_cb: ProgressCb = None
    ) -> bool:
        """Applies random jittered pacing delay and periodic chunk rest pauses."""
        self.processed_count += 1
        if self.chunk_size > 0 and (self.processed_count % self.chunk_size == 0):
            msg = f"[{self.service_name} cooldown] Processed {self.processed_count} items; resting for {self.chunk_pause_sec:.1f}s to avoid rate limits..."
            if not self.sleep_interruptible(
                self.chunk_pause_sec, cancel_check, progress_cb, msg
            ):
                return False

        delay = random.uniform(self.min_delay, self.max_delay)
        return self.sleep_interruptible(delay, cancel_check)

    def record_success(self) -> None:
        """Resets consecutive blocks; forgives old breaker trips after a healthy streak."""
        self.consecutive_blocks = 0
        self.success_streak += 1
        if self.success_streak >= 10:
            self.breaker_trips = 0

    def handle_rate_limit(
        self,
        attempt: int,
        error_msg: str,
        cancel_check: CancelCheck = None,
        progress_cb: ProgressCb = None,
        retry_after: float | None = None,
    ) -> bool:
        """Applies backoff; raises ThrottleAbort when cooldowns clearly are not helping."""
        self.consecutive_blocks += 1
        self.success_streak = 0
        if (
            self.circuit_breaker_threshold > 0
            and self.consecutive_blocks >= self.circuit_breaker_threshold
        ):
            self.breaker_trips += 1
            self.consecutive_blocks = 0
            if (
                self.max_breaker_trips > 0
                and self.breaker_trips >= self.max_breaker_trips
            ):
                raise ThrottleAbort(
                    f"{self.service_name}: still blocked after {self.breaker_trips} cooldowns. "
                    f"Wait a few hours, change network/proxy, or use browser cookies. "
                    f"Progress is saved; re-run to resume."
                )
            msg = (
                f"[{self.service_name} CIRCUIT BREAKER] repeated rate limits! "
                f"Cooling down for {self.circuit_breaker_pause_sec:.0f}s..."
            )
            return self.sleep_interruptible(
                self.circuit_breaker_pause_sec, cancel_check, progress_cb, msg
            )

        backoff_sec = self.calculate_backoff(attempt, retry_after)
        short_err = error_msg[:80] + "..." if len(error_msg) > 80 else error_msg
        msg = f"[{self.service_name} 429/throttle] {short_err} -> retry {attempt + 1}/{self.max_retries} after {backoff_sec:.1f}s"
        return self.sleep_interruptible(backoff_sec, cancel_check, progress_cb, msg)

    def calculate_backoff(
        self, attempt: int, retry_after: float | None = None
    ) -> float:
        """Computes exponential backoff with full jitter, honoring Retry-After if present."""
        if retry_after is not None and retry_after > 0:
            return retry_after + random.uniform(0.5, 2.0)
        delay = min(self.backoff_max_sec, self.backoff_base_sec * (2**attempt))
        jitter = random.uniform(0.5, 2.5)
        return delay + jitter


def make_throttler(service: str, cfg: AppConfig) -> AdaptiveThrottler:
    return AdaptiveThrottler(
        service_name=service,
        pacing_range=cfg.get_pacing_range(service),
        chunk_size=cfg.chunk_size,
        chunk_pause_sec=cfg.chunk_pause_sec,
        max_retries=cfg.max_retries,
        backoff_base_sec=cfg.backoff_base_sec,
        backoff_max_sec=cfg.backoff_max_sec,
        circuit_breaker_threshold=cfg.circuit_breaker_threshold,
        circuit_breaker_pause_sec=cfg.circuit_breaker_pause_sec,
        max_breaker_trips=cfg.max_breaker_trips,
    )


def safe_json_loads(s: str, default: Any = None) -> Any:
    try:
        return json.loads(s) if s else default
    except (json.JSONDecodeError, TypeError) as e:
        logger.debug(f"JSON decode failed: {e}")
        return default


def parse_tags_field(raw: Any) -> list[str]:
    """Correct tag parsing - deserializes JSON array and checks membership exactly."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).lower() for t in raw]
    if isinstance(raw, str):
        # try json
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                return [str(t).lower() for t in arr]
        except json.JSONDecodeError:
            pass
        # fallback: space separated? return lower split
        # but for original substring bug, we avoid substring matching
        # If raw is like '["python", "ai"]' failed above, we still try to split
        return [raw.lower()] if raw else []
    return []


def highlight_search_snippet(snippet: str, query: str) -> str:
    """Render untrusted source text safely with only our own highlight markup."""
    terms = sorted(
        {term for term in query.split() if len(term) >= 2}, key=len, reverse=True
    )
    if not terms:
        return html.escape(snippet)
    pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    parts = []
    previous = 0
    for match in pattern.finditer(snippet):
        parts.append(html.escape(snippet[previous : match.start()]))
        parts.append("<mark>" + html.escape(match.group()) + "</mark>")
        previous = match.end()
    parts.append(html.escape(snippet[previous:]))
    return "".join(parts)


# ---------------------------------------------------------------------------
# JSONL helpers - append-only with deduplication
# ---------------------------------------------------------------------------
def load_jsonl_deduped(path: Path, key: str = "video_id") -> dict[str, dict[str, Any]]:
    """Load JSONL keeping last occurrence (append-only semantics)."""
    result: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return result
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    j = json.loads(line)
                    k = j.get(key)
                    if k:
                        result[k] = j
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning(f"Failed to read {path}: {e}")
    return result


def read_existing_ids_jsonl(path: Path, key: str = "video_id") -> set[str]:
    return set(load_jsonl_deduped(path, key).keys())


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.error(f"Failed to append to {path}: {e}")
        raise


def rewrite_jsonl_deduped(path: Path, records: dict[str, dict[str, Any]]) -> None:
    """Used only for compaction/export, not on every edit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(
                json.dumps(v, ensure_ascii=False) + "\n" for v in records.values()
            )
    except OSError as e:
        logger.error(f"Failed to rewrite {path}: {e}")


# ---------------------------------------------------------------------------
# DB layer - context managers + migrations
# ---------------------------------------------------------------------------
@contextmanager
def db_connection(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cur.fetchone() is not None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS schema_version(
        version INTEGER PRIMARY KEY,
        applied_at TEXT
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS videos(
        video_id TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        channel TEXT,
        channel_id TEXT,
        uploader TEXT,
        upload_date TEXT,
        timestamp INTEGER,
        duration INTEGER,
        view_count INTEGER,
        like_count INTEGER,
        comment_count INTEGER,
        tags TEXT,
        categories TEXT,
        thumbnail TEXT,
        url TEXT,
        language TEXT,
        chapters TEXT,
        quality_score REAL,
        quality_label TEXT,
        quality_details TEXT,
        user_score REAL,
        user_notes TEXT,
        summary TEXT,
        summary_source TEXT,
        summary_date TEXT,
        status TEXT,
        meta_json TEXT
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS playlists(
        playlist_id TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        channel TEXT,
        channel_id TEXT,
        uploader TEXT,
        video_count INTEGER,
        url TEXT,
        meta_json TEXT
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS playlist_videos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        playlist_id TEXT,
        video_id TEXT,
        position INTEGER,
        UNIQUE(playlist_id, video_id)
    )""")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS transcripts(video_id TEXT PRIMARY KEY, title TEXT, url TEXT, channel TEXT, channel_id TEXT, text TEXT, word_count INTEGER, json TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, author TEXT, text TEXT, likes INTEGER, time TEXT, json TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS failures(id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, type TEXT, error TEXT, url TEXT, title TEXT, timestamp TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS summaries(id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, summary TEXT, source TEXT, created_at TEXT)"
    )

    # FTS tables - create if not exists
    for fts_sql in [
        "CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(video_id, title, description, tags, channel, tokenize='porter unicode61')",
        "CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(video_id, title, text, tokenize='porter unicode61')",
        "CREATE VIRTUAL TABLE IF NOT EXISTS comments_fts USING fts5(video_id, author, text, tokenize='porter unicode61')",
        "CREATE VIRTUAL TABLE IF NOT EXISTS playlists_fts USING fts5(playlist_id, title, description, channel, tokenize='porter unicode61')",
        "CREATE VIRTUAL TABLE IF NOT EXISTS summaries_fts USING fts5(video_id, summary, tokenize='porter unicode61')",
    ]:
        try:
            conn.execute(fts_sql)
        except sqlite3.OperationalError as e:
            logger.debug(f"FTS creation note: {e}")

    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_videos_quality ON videos(quality_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_videos_user_score ON videos(user_score DESC)",
        "CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status)",
        "CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id)",
        "CREATE INDEX IF NOT EXISTS idx_playlist_videos_pid ON playlist_videos(playlist_id)",
        "CREATE INDEX IF NOT EXISTS idx_playlist_videos_vid ON playlist_videos(video_id)",
        "CREATE INDEX IF NOT EXISTS idx_transcripts_vid ON transcripts(video_id)",
        "CREATE INDEX IF NOT EXISTS idx_comments_vid ON comments(video_id)",
    ]:
        conn.execute(idx_sql)

    # version bump
    cur = conn.execute(
        "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
    )
    row = cur.fetchone()
    current = row[0] if row else 0
    if current < SCHEMA_VERSION:
        conn.execute(
            "INSERT OR REPLACE INTO schema_version(version, applied_at) VALUES (?,?)",
            (SCHEMA_VERSION, datetime.now().isoformat()),
        )
        logger.info(f"DB migrated to version {SCHEMA_VERSION}")


def read_preserved_user_data(db_path: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Preserve user scores and summaries from existing DB to avoid data loss on rebuild."""
    scores: dict[str, dict] = {}
    summaries: dict[str, dict] = {}
    if not db_path.exists():
        return scores, summaries
    try:
        with db_connection(db_path) as conn:
            if not _table_exists(conn, "videos"):
                return scores, summaries
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT video_id, user_score, user_notes FROM videos WHERE user_score IS NOT NULL"
                )
                for r in cur.fetchall():
                    scores[r["video_id"]] = {
                        "video_id": r["video_id"],
                        "user_score": r["user_score"],
                        "user_notes": r["user_notes"] or "",
                        "updated_at": datetime.now().isoformat(),
                    }
            except sqlite3.OperationalError as e:
                logger.debug(f"read preserved scores failed: {e}")
            try:
                cur.execute(
                    "SELECT video_id, summary, summary_source, summary_date FROM videos WHERE summary IS NOT NULL AND summary != ''"
                )
                for r in cur.fetchall():
                    summaries[r["video_id"]] = {
                        "video_id": r["video_id"],
                        "summary": r["summary"],
                        "source": r["summary_source"] or "preserved_db",
                        "created_at": r["summary_date"] or datetime.now().isoformat(),
                    }
            except sqlite3.OperationalError as e:
                logger.debug(f"read preserved summaries failed: {e}")
    except Exception as e:
        logger.warning(f"Could not read preserved data from DB: {e}")
    return scores, summaries


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def get_playlist_data(
    url: str,
    limit: int = 0,
    progress_cb: ProgressCb = None,
    cancel_check: CancelCheck = None,
    config: AppConfig | None = None,
    throttler: AdaptiveThrottler | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    import yt_dlp

    cfg = config or CONFIG
    _log(f"Fetching: {url}", progress_cb)
    v_id, p_id = extract_ids_from_url(url)
    _log(f"Parsed -> video={v_id} playlist={p_id}", progress_cb)
    urls_to_try = []
    if p_id:
        urls_to_try.append(f"https://www.youtube.com/playlist?list={p_id}")
    urls_to_try.append(url)

    all_videos: dict[str, dict] = {}
    all_playlists: dict[str, dict] = {}
    mappings: list[dict] = []

    for attempt_url in urls_to_try:
        if cancel_check and cancel_check():
            logger.info("Discovery cancelled")
            break
        if throttler and not throttler.pace(cancel_check, progress_cb):
            logger.info("Discovery cancelled during pacing")
            break
        is_playlist_attempt = any(
            x in attempt_url
            for x in ("playlist?list=", "/@", "/channel/", "/c/", "/user/")
        ) or (p_id and p_id in attempt_url)
        ydl_opts = get_ydl_opts(YDL_POLITE_OPTS, cfg)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(attempt_url, download=False)
                if not info:
                    continue
                if throttler:
                    throttler.record_success()
                playlist_id = info.get("id") if is_playlist_attempt else p_id
                if "entries" in info and info.get("entries") is not None:
                    if not playlist_id:
                        playlist_id = (
                            info.get("id") or p_id or f"unknown_{len(all_playlists)}"
                        )
                    if playlist_id not in all_playlists:
                        all_playlists[playlist_id] = {
                            "playlist_id": playlist_id,
                            "title": info.get("title") or "",
                            "description": info.get("description") or "",
                            "channel": info.get("channel")
                            or info.get("uploader")
                            or "",
                            "channel_id": info.get("channel_id")
                            or info.get("uploader_id")
                            or "",
                            "uploader": info.get("uploader") or "",
                            "url": f"https://www.youtube.com/playlist?list={playlist_id}"
                            if str(playlist_id).startswith("PL")
                            else attempt_url,
                            "video_count": info.get("playlist_count") or 0,
                            "tags": info.get("tags") or [],
                            "meta_json": "",
                        }
                    pos = 0
                    for e in info.get("entries") or []:
                        if not e:
                            continue
                        vid = e.get("id")
                        if not vid or not is_valid_video_id(vid):
                            continue
                        pos += 1
                        if vid not in all_videos:
                            all_videos[vid] = {
                                "id": vid,
                                "title": e.get("title") or "",
                                "url": f"https://www.youtube.com/watch?v={vid}",
                                "channel": e.get("channel")
                                or e.get("uploader")
                                or info.get("channel")
                                or "",
                                "channel_id": e.get("channel_id")
                                or e.get("uploader_id")
                                or info.get("channel_id")
                                or "",
                                "uploader": e.get("uploader") or "",
                                "description": e.get("description") or "",
                                "duration": e.get("duration") or 0,
                                "view_count": e.get("view_count") or 0,
                            }
                        mappings.append({
                            "playlist_id": playlist_id,
                            "video_id": vid,
                            "position": pos,
                        })
                        if limit and len(all_videos) >= limit:
                            break
                    if all_videos and is_playlist_attempt and p_id:
                        break
                else:
                    vid = info.get("id")
                    if not vid or not is_valid_video_id(vid):
                        continue
                    if vid not in all_videos:
                        all_videos[vid] = {
                            "id": vid,
                            "title": info.get("title") or "",
                            "url": f"https://www.youtube.com/watch?v={vid}",
                            "channel": info.get("channel")
                            or info.get("uploader")
                            or "",
                            "channel_id": info.get("channel_id") or "",
                            "uploader": info.get("uploader") or "",
                            "description": info.get("description") or "",
                            "duration": info.get("duration") or 0,
                            "view_count": info.get("view_count") or 0,
                        }
                    if p_id:
                        if p_id not in all_playlists:
                            all_playlists[p_id] = {
                                "playlist_id": p_id,
                                "title": f"Playlist {p_id}",
                                "description": "",
                                "channel": info.get("channel") or "",
                                "channel_id": info.get("channel_id") or "",
                                "uploader": info.get("uploader") or "",
                                "url": f"https://www.youtube.com/playlist?list={p_id}",
                                "video_count": 0,
                                "tags": [],
                                "meta_json": "",
                            }
                        mappings.append({
                            "playlist_id": p_id,
                            "video_id": vid,
                            "position": 0,
                        })
        except Exception as e:
            _log(f"Attempt {attempt_url} failed: {e}", progress_cb)

    if not all_videos and v_id and is_valid_video_id(v_id):
        all_videos[v_id] = {
            "id": v_id,
            "title": "",
            "url": f"https://www.youtube.com/watch?v={v_id}",
            "channel": "",
            "channel_id": "",
            "uploader": "",
            "description": "",
            "duration": 0,
            "view_count": 0,
        }

    videos = list(all_videos.values())
    playlists = list(all_playlists.values())
    if limit and len(videos) > limit:
        videos = videos[:limit]
    _log(
        f"Discovery done: {len(videos)} videos, {len(playlists)} playlists, {len(mappings)} mappings",
        progress_cb,
    )
    return videos, playlists, mappings


# ---------------------------------------------------------------------------
# Full metadata - failure log opened once per batch
# ---------------------------------------------------------------------------
def fetch_videos_full_metadata(
    video_ids: list[str],
    out_path: Path,
    progress_cb: ProgressCb = None,
    resume: bool = True,
    sleep_sec: float = 1.5,
    cancel_check: CancelCheck = None,
    throttler: AdaptiveThrottler | None = None,
    config: AppConfig | None = None,
) -> None:
    import yt_dlp

    cfg = config or CONFIG
    if throttler is None:
        throttler = make_throttler(
            "metadata", cfg
        )  # "transcripts" / "comments" in the other two

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_existing_ids_jsonl(out_path, key="id") if resume else set()
    mode = "a" if resume and out_path.exists() else "w"
    failures_path = out_path.parent / "failed_metadata.jsonl"
    ok = 0
    ydl_opts = get_ydl_opts(YDL_POLITE_OPTS, cfg)

    with (
        open(out_path, mode, encoding="utf-8") as f_out,
        open(failures_path, "a", encoding="utf-8") as f_fail,
    ):
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for vid in video_ids:
                if cancel_check and cancel_check():
                    logger.info("Metadata fetch cancelled")
                    break
                if not is_valid_video_id(vid):
                    continue
                if vid in existing:
                    continue

                if not throttler.pace(cancel_check, progress_cb):
                    logger.info("Metadata fetch cancelled during pacing")
                    break

                for attempt in range(throttler.max_retries + 1):
                    try:
                        info = ydl.extract_info(
                            f"https://www.youtube.com/watch?v={vid}", download=False
                        )
                        if not info:
                            raise ValueError("No info returned by yt-dlp")
                        rec = {
                            "id": vid,
                            "title": info.get("title") or "",
                            "fulltitle": info.get("fulltitle") or "",
                            "description": info.get("description") or "",
                            "tags": info.get("tags") or [],
                            "categories": info.get("categories") or [],
                            "channel": info.get("channel") or "",
                            "channel_id": info.get("channel_id") or "",
                            "channel_url": info.get("channel_url") or "",
                            "uploader": info.get("uploader") or "",
                            "uploader_id": info.get("uploader_id") or "",
                            "upload_date": info.get("upload_date") or "",
                            "timestamp": info.get("timestamp") or 0,
                            "duration": info.get("duration") or 0,
                            "view_count": info.get("view_count") or 0,
                            "like_count": info.get("like_count") or 0,
                            "comment_count": info.get("comment_count") or 0,
                            "thumbnail": info.get("thumbnail") or "",
                            "language": info.get("language") or "",
                            "chapters": info.get("chapters") or [],
                            "url": f"https://www.youtube.com/watch?v={vid}",
                        }
                        f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        f_out.flush()
                        ok += 1
                        throttler.record_success()
                        _log(f"[meta OK] {vid} tags={len(rec['tags'])}", progress_cb)
                        break
                    except Exception as e:
                        if attempt < throttler.max_retries and is_rate_limit_error(e):
                            if not throttler.handle_rate_limit(
                                attempt,
                                str(e),
                                cancel_check=cancel_check,
                                progress_cb=progress_cb,
                            ):
                                logger.info("Metadata fetch cancelled during backoff")
                                return
                        else:
                            f_fail.write(
                                json.dumps(
                                    {
                                        "video_id": vid,
                                        "type": "metadata",
                                        "error": str(e),
                                        "url": f"https://www.youtube.com/watch?v={vid}",
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                            f_fail.flush()
                            _log(f"[meta FAIL] {vid}: {e}", progress_cb)
                            break
    _log(f"Full metadata: {ok} -> {out_path}", progress_cb)


# ---------------------------------------------------------------------------
# Transcripts - Multi-tier retrieval (Primary API + yt-dlp caption fallback)
# ---------------------------------------------------------------------------
def _fetch_transcript_ytdlp_fallback(
    vid: str, config: AppConfig | None = None
) -> tuple[str, list[dict[str, Any]], str]:
    """Fallback transcript extraction using yt-dlp subtitles and automatic captions (timedtext json3)."""
    import requests
    import yt_dlp

    cfg = config or CONFIG
    ydl_opts = get_ydl_opts(YDL_POLITE_OPTS, cfg)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={vid}", download=False
        )
    if not info:
        raise ValueError(f"yt-dlp could not extract info for {vid}")

    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    pref_langs = ["en", "en-US", "en-GB"]
    chosen_sub = None
    lang_code = "en"

    for l in pref_langs:
        if subs.get(l):
            chosen_sub = subs[l]
            lang_code = l
            break
    if not chosen_sub:
        for l in pref_langs:
            if auto.get(l):
                chosen_sub = auto[l]
                lang_code = l
                break
    if not chosen_sub and subs:
        first_lang = next(iter(subs))
        chosen_sub = subs[first_lang]
        lang_code = first_lang
    if not chosen_sub and auto:
        first_lang = next(iter(auto))
        chosen_sub = auto[first_lang]
        lang_code = first_lang

    if not chosen_sub:
        raise ValueError(
            f"No subtitles or automatic captions available in yt-dlp for {vid}"
        )

    fmt = next((x for x in chosen_sub if x.get("ext") == "json3"), chosen_sub[0])
    sub_url = fmt.get("url")
    if not sub_url:
        raise ValueError(f"No subtitle URL found in yt-dlp stream for {vid}")

    session = requests.Session()
    if cfg.proxy:
        session.proxies = {"http": cfg.proxy, "https": cfg.proxy}
    resp = session.get(sub_url, timeout=25)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Failed to fetch subtitle stream from {sub_url}: HTTP {resp.status_code}"
        )

    data = resp.json()
    events = data.get("events") or []
    segs = []
    full_text = []
    for ev in events:
        t_start = ev.get("tStartMs", 0) / 1000.0
        t_dur = ev.get("dDurationMs", 0) / 1000.0
        ev_segs = ev.get("segs") or []
        txt = "".join(s.get("utf8", "") for s in ev_segs).strip()
        if txt and txt != "\n":
            segs.append({
                "text": txt,
                "start": float(t_start),
                "duration": float(t_dur),
            })
            full_text.append(txt)

    if not segs:
        raise ValueError(f"Subtitle stream for {vid} contained no text segments")

    return " ".join(full_text), segs, lang_code


def _fetch_one_transcript_primary(
    vid: str, config: AppConfig | None = None
) -> tuple[str, list[dict[str, Any]], str]:
    """Primary transcript extraction using youtube_transcript_api 1.2+ (one listing, one fetch)."""
    from youtube_transcript_api import NoTranscriptFound, YouTubeTranscriptApi
    from youtube_transcript_api.proxies import GenericProxyConfig

    # update youtube-transcript-api>=1.2.4 where stubs are fixed.
    cfg = config or CONFIG
    proxy_cfg = None
    if cfg.proxy:
        proxy_cfg = (
            GenericProxyConfig(http_url=cfg.proxy, https_url=cfg.proxy)
            if cfg.proxy
            else None
        )  # type: ignore[arg-type]
    api = YouTubeTranscriptApi(proxy_config=proxy_cfg)  # type: ignore

    # Errors (TranscriptsDisabled, VideoUnavailable, RequestBlocked, IpBlocked) propagate
    # unchanged so callers can classify them as permanent vs. throttled.
    t_list = api.list(vid)
    wanted = ["en", "en-US", "en-GB"]
    chosen = None
    for finder in (
        t_list.find_manually_created_transcript,
        t_list.find_generated_transcript,
    ):
        try:
            chosen = finder(wanted)
            break
        except NoTranscriptFound:
            continue
    if chosen is None:
        chosen = next(iter(t_list), None)
    if chosen is None:
        raise ValueError("no transcript available")

    fetched = chosen.fetch()
    raw = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else fetched
    lang = getattr(fetched, "language_code", None) or getattr(
        chosen, "language_code", "en"
    )

    segs = []
    full = []
    for item in raw or []:
        if isinstance(item, dict):
            txt = item.get("text", "")
            st = float(item.get("start", 0))
            du = float(item.get("duration", 0))
        else:
            txt = getattr(item, "text", "")
            st = float(getattr(item, "start", 0))
            du = float(getattr(item, "duration", 0))
        if txt:
            segs.append({"text": txt, "start": st, "duration": du})
            full.append(txt)
    return " ".join(full), segs, lang


def _fetch_one_transcript(
    vid: str, config: AppConfig | None = None
) -> tuple[str, list[dict[str, Any]], str]:
    """Attempts primary transcript API, then seamlessly falls back to yt-dlp caption extraction."""
    if not is_valid_video_id(vid):
        raise ValueError(f"Invalid video ID {vid}")
    primary_err = None
    try:
        return _fetch_one_transcript_primary(vid, config=config)
    except Exception as e:
        primary_err = e
        if is_permanent_transcript_error(e) or is_hard_block_error(e):
            raise
        logger.debug(
            f"Primary transcript API failed for {vid} ({e}), trying yt-dlp subtitle fallback..."
        )

    try:
        return _fetch_transcript_ytdlp_fallback(vid, config=config)
    except Exception as e_fallback:
        raise RuntimeError(
            f"Both transcript extraction methods failed: primary={primary_err} fallback={e_fallback}"
        ) from e_fallback


def fetch_transcripts_bulk(
    video_infos: list[dict],
    out_path: Path,
    resume: bool = True,
    progress_cb: ProgressCb = None,
    sleep_sec: float = 2.0,
    cancel_check: CancelCheck = None,
    throttler: AdaptiveThrottler | None = None,
    config: AppConfig | None = None,
) -> None:
    cfg = config or CONFIG
    if throttler is None:
        throttler = make_throttler("transcripts", cfg)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_existing_ids_jsonl(out_path, key="video_id") if resume else set()
    failures_path = out_path.parent / "failed_transcripts.jsonl"
    mode = "a" if resume and out_path.exists() else "w"
    ok = skip = err = 0

    with (
        open(out_path, mode, encoding="utf-8") as f_out,
        open(failures_path, "a", encoding="utf-8") as f_fail,
    ):
        for info in video_infos:
            if cancel_check and cancel_check():
                logger.info("Transcript fetch cancelled")
                break
            vid = info.get("id") or info.get("video_id") or ""
            if not is_valid_video_id(vid):
                continue
            if vid in existing:
                skip += 1
                continue

            if not throttler.pace(cancel_check, progress_cb):
                logger.info("Transcript fetch cancelled during pacing")
                break

            for attempt in range(throttler.max_retries + 1):
                try:
                    full, segs, lang = _fetch_one_transcript(vid, config=cfg)
                    rec = {
                        "video_id": vid,
                        "title": info.get("title", ""),
                        "url": info.get("url", ""),
                        "channel": info.get("channel", ""),
                        "channel_id": info.get("channel_id", ""),
                        "language": lang,
                        "text": full,
                        "segments": segs,
                    }
                    f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f_out.flush()
                    ok += 1
                    throttler.record_success()
                    _log(f"[transcript OK] {vid}", progress_cb)
                    break
                except Exception as e:
                    fail_rec = {
                        "video_id": vid,
                        "type": "transcript",
                        "error": str(e),
                        "url": info.get("url", ""),
                        "title": info.get("title", ""),
                    }
                    if is_permanent_transcript_error(e):
                        # Captions truly do not exist: placeholder so resume skips this video.
                        rec = {
                            "video_id": vid,
                            "title": info.get("title", ""),
                            "url": info.get("url", ""),
                            "error": str(e),
                            "text": "",
                            "segments": [],
                        }
                        f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        f_out.flush()
                        f_fail.write(json.dumps(fail_rec, ensure_ascii=False) + "\n")
                        f_fail.flush()
                        err += 1
                        _log(
                            f"[transcript NONE] {vid}: No transcripts available",
                            progress_cb,
                        )
                        break
                    if is_rate_limit_error(e):
                        if attempt < throttler.max_retries:
                            # May raise ThrottleAbort, which propagates and closes the files cleanly.
                            if not throttler.handle_rate_limit(
                                attempt,
                                str(e),
                                cancel_check=cancel_check,
                                progress_cb=progress_cb,
                            ):
                                logger.info("Transcript fetch cancelled during backoff")
                                return
                            continue
                        # Retries exhausted while blocked: log only, NO placeholder,
                        # so the next run retries this video.
                        fail_rec["type"] = "transcript_blocked"
                    # Blocked or unknown error: failure log only (not marked done in transcripts.jsonl).
                    f_fail.write(json.dumps(fail_rec, ensure_ascii=False) + "\n")
                    f_fail.flush()
                    err += 1
                    _log(f"[transcript FAIL] {vid}: {e}", progress_cb)
                    break
    _log(
        f"Transcripts: {ok} ok, {skip} skipped, {err} failed -> {out_path} + {failures_path}",
        progress_cb,
    )


# ---------------------------------------------------------------------------
# Comments - single failure handle
# ---------------------------------------------------------------------------
def fetch_comments_bulk(
    video_ids: list[str],
    out_path: Path,
    limit_per_video: int = 0,
    resume: bool = True,
    progress_cb: ProgressCb = None,
    throttler: AdaptiveThrottler | None = None,
    config: AppConfig | None = None,
    cancel_check: CancelCheck = None,
) -> None:
    try:
        from youtube_comment_downloader import YoutubeCommentDownloader
    except ImportError:
        _log("youtube-comment-downloader not installed, skipping comments", progress_cb)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("", encoding="utf-8")
        return

    cfg = config or CONFIG
    if throttler is None:
        throttler = make_throttler("comments", cfg)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    failures_path = out_path.parent / "failed_comments.jsonl"
    existing_vids: set[str] = set()
    if resume and out_path.exists():
        try:
            with open(out_path, encoding="utf-8", errors="ignore") as rf:
                for line in rf:
                    try:
                        j = json.loads(line)
                        if j.get("video_id"):
                            existing_vids.add(j["video_id"])
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            logger.warning(f"Failed to read comments for resume: {e}")

    downloader = YoutubeCommentDownloader()
    total = 0
    with (
        open(
            out_path, "a" if resume and out_path.exists() else "w", encoding="utf-8"
        ) as f_out,
        open(failures_path, "a", encoding="utf-8") as f_fail,
    ):
        for vid in video_ids:
            if cancel_check and cancel_check():
                logger.info("Comments fetch cancelled")
                break
            if not is_valid_video_id(vid):
                continue
            if resume and vid in existing_vids:
                continue

            if not throttler.pace(cancel_check, progress_cb):
                logger.info("Comments fetch cancelled during pacing")
                break
            limit = limit_per_video or cfg.comments_per_video
            for attempt in range(throttler.max_retries + 1):
                try:
                    gen = downloader.get_comments_from_url(
                        f"https://www.youtube.com/watch?v={vid}",
                        sort_by=0,
                        sleep=cfg.comment_page_sleep,  # delay between comment pages
                    )
                    batch = []
                    for cm in gen:
                        if cancel_check and cancel_check():
                            break
                        text = cm.get("text", "")
                        if not text:
                            continue
                        batch.append({
                            "video_id": vid,
                            "author": cm.get("author", ""),
                            "text": text,
                            "votes": parse_vote_count(cm.get("votes")),
                            "time": cm.get("time", ""),
                        })
                        if limit and len(batch) >= limit:
                            break
                    if cancel_check and cancel_check():
                        logger.info("Comments fetch cancelled")
                        return  # discard the partial video so a resume re-fetches it whole
                    f_out.writelines(
                        json.dumps(rec, ensure_ascii=False) + "\n" for rec in batch
                    )
                    f_out.flush()
                    total += len(batch)
                    throttler.record_success()
                    _log(f"[comments OK] {vid}: {len(batch)}", progress_cb)
                    break
                except Exception as e:
                    if attempt < throttler.max_retries and is_rate_limit_error(e):
                        if not throttler.handle_rate_limit(
                            attempt,
                            str(e),
                            cancel_check=cancel_check,
                            progress_cb=progress_cb,
                        ):
                            logger.info("Comments fetch cancelled during backoff")
                            return
                    else:
                        f_fail.write(
                            json.dumps(
                                {
                                    "video_id": vid,
                                    "type": "comments",
                                    "error": str(e),
                                    "url": f"https://www.youtube.com/watch?v={vid}",
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        f_fail.flush()
                        _log(f"[comments FAIL] {vid}: {e}", progress_cb)
                        break
    _log(f"Comments done: {total} -> {out_path}", progress_cb)


# ---------------------------------------------------------------------------
# Quality scoring - typed, specific exceptions
# ---------------------------------------------------------------------------
def compute_quality_score(
    video: dict,
    transcript: dict | None,
    comment_stats: dict | None,
    playlist_count: int,
    channel_video_count: int,
) -> tuple[float, dict]:
    score = 0
    details: dict[str, Any] = {}
    completeness = 0

    if transcript and transcript.get("text"):
        wc = len(transcript["text"].split())
        completeness += 10 if wc > 500 else (wc / 500 * 10)
        details["has_transcript"] = True
        details["transcript_words"] = wc
    else:
        details["has_transcript"] = False
        details["transcript_words"] = 0

    desc = video.get("description", "") or ""
    if len(desc) > 100:
        completeness += 5
    elif len(desc) > 20:
        completeness += 2

    tags = video.get("tags") or []
    if len(tags) >= 3:
        completeness += 5
    elif len(tags) >= 1:
        completeness += 2
    details["tag_count"] = len(tags)
    if video.get("chapters"):
        completeness += 5
        details["has_chapters"] = True
    else:
        details["has_chapters"] = False
    completeness = min(completeness, 25)
    details["completeness"] = round(completeness, 1)

    authority = 0
    views = video.get("view_count", 0) or 0
    likes = video.get("like_count", 0) or 0
    comments = video.get("comment_count", 0) or (
        comment_stats.get("count", 0) if comment_stats else 0
    )
    if views > 0:
        authority += min(math.log10(views + 1) / 6 * 15, 15)
    if views > 0 and likes > 0:
        ratio = likes / views
        if ratio > 0.02:
            authority += 5
        elif ratio > 0.005:
            authority += 3
    if comments > 50:
        authority += 5
    elif comments > 10:
        authority += 2
    authority = min(authority, 25)
    details["authority"] = round(authority, 1)
    details["views"] = views
    details["likes"] = likes
    details["comment_count"] = comments

    depth = 0
    duration = video.get("duration", 0) or 0
    if 240 <= duration <= 2700:
        depth += 10
    elif 120 <= duration <= 3600:
        depth += 6
    elif duration > 60:
        depth += 3
    if transcript and duration > 0:
        wpm = (
            details.get("transcript_words", 0) / (duration / 60) if duration > 0 else 0
        )
        details["wpm"] = round(wpm, 1)
        if 100 <= wpm <= 180:
            depth += 5
        elif wpm > 50:
            depth += 3
    if len(desc) > 500:
        depth += 5
    elif len(desc) > 200:
        depth += 2
    depth = min(depth, 20)
    details["depth"] = round(depth, 1)
    details["duration"] = duration

    freshness = 0
    upload_date = video.get("upload_date", "")
    try:
        if upload_date and len(upload_date) == 8:
            dt = datetime.strptime(upload_date, "%Y%m%d")
            age_days = (datetime.now() - dt).days
            details["age_days"] = age_days
            if age_days < 365:
                freshness = 10
            elif age_days < 730:
                freshness = 7
            elif age_days < 1460:
                freshness = 4
            else:
                freshness = 1
        else:
            freshness = 5
    except (ValueError, TypeError) as e:
        logger.debug(f"Freshness parse failed for {upload_date}: {e}")
        freshness = 5
    details["freshness"] = freshness

    consensus = 0
    details["playlist_count"] = playlist_count
    if playlist_count >= 3:
        consensus += 10
    elif playlist_count == 2:
        consensus += 6
    elif playlist_count == 1:
        consensus += 3
    if channel_video_count >= 10:
        consensus += 5
    elif channel_video_count >= 3:
        consensus += 3
    if len(tags) >= 8:
        consensus += 5
    elif len(tags) >= 4:
        consensus += 2
    consensus = min(consensus, 20)
    details["consensus"] = round(consensus, 1)

    total = round(completeness + authority + depth + freshness + consensus, 1)
    details["total"] = total
    if total >= 80:
        label = "excellent"
    elif total >= 60:
        label = "good"
    elif total >= 40:
        label = "ok"
    elif total >= 20:
        label = "low"
    else:
        label = "poor"
    details["label"] = label
    return total, details


# ---------------------------------------------------------------------------
# DB building - refactored into small functions, preserves data
# ---------------------------------------------------------------------------
def _load_playlists_batch(out_dir: Path) -> list[dict]:
    path = out_dir / "playlists.jsonl"
    if not path.exists():
        return []
    out = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    j = json.loads(line)
                    if j.get("playlist_id"):
                        out.append(j)
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning(f"Failed to load playlists: {e}")
    return out


def _load_mappings_batch(out_dir: Path) -> list[dict]:
    path = out_dir / "playlist_videos.jsonl"
    if not path.exists():
        return []
    out = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    j = json.loads(line)
                    if (
                        j.get("playlist_id")
                        and j.get("video_id")
                        and is_valid_video_id(j["video_id"])
                    ):
                        out.append(j)
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning(f"Failed to load mappings: {e}")
    return out


def _load_videos_full(out_dir: Path) -> dict[str, dict]:
    videos_full_path = out_dir / "videos_full.jsonl"
    videos_meta_path = out_dir / "videos_meta.json"
    all_vids: dict[str, dict] = {}
    if videos_meta_path.exists():
        try:
            for j in json.loads(videos_meta_path.read_text(encoding="utf-8")):
                vid = j.get("id")
                if vid and is_valid_video_id(vid):
                    all_vids[vid] = j
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load videos_meta: {e}")
    if videos_full_path.exists():
        try:
            with open(videos_full_path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    try:
                        j = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    vid = j.get("id") or j.get("video_id")
                    if vid and is_valid_video_id(vid):
                        all_vids[vid] = {**all_vids.get(vid, {}), **j}
        except OSError as e:
            logger.warning(f"Failed to load videos_full: {e}")
    return all_vids


def _load_transcript_map(out_dir: Path) -> dict[str, dict]:
    path = out_dir / "transcripts.jsonl"
    m: dict[str, dict] = {}
    if not path.exists():
        return m
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    j = json.loads(line)
                    if j.get("video_id") and j.get("text"):
                        m[j["video_id"]] = j
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning(f"Failed to load transcripts map: {e}")
    return m


def _load_comment_stats(out_dir: Path) -> dict[str, dict]:
    path = out_dir / "comments.jsonl"
    stats: dict[str, dict] = {}
    if not path.exists():
        return stats
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    j = json.loads(line)
                    vid = j.get("video_id")
                    if not vid:
                        continue
                    if vid not in stats:
                        stats[vid] = {"count": 0, "likes": 0}
                    stats[vid]["count"] += 1
                    stats[vid]["likes"] += j.get("votes", 0) or 0
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning(f"Failed to load comment stats: {e}")
    return stats


def build_db(
    out_dir: Path,
    db_path: Path,
    progress_cb: ProgressCb = None,
    cancel_check: CancelCheck = None,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Preserve user data instead of deleting DB ---
    preserved_scores, preserved_summaries = read_preserved_user_data(db_path)
    # Also merge JSONL sources (deduped, last wins)
    jsonl_scores = load_jsonl_deduped(out_dir / "user_scores.jsonl", "video_id")
    jsonl_summaries = load_jsonl_deduped(out_dir / "summaries.jsonl", "video_id")

    # JSONL takes precedence if it has newer data, but keep union
    merged_scores = {**preserved_scores, **jsonl_scores}
    merged_summaries = {**preserved_summaries, **jsonl_summaries}

    if db_path.exists():
        # backup instead of unlink
        backup_path = db_path.with_suffix(".bak.db")
        try:
            with (
                sqlite3.connect(str(db_path)) as source,
                sqlite3.connect(str(backup_path)) as destination,
            ):
                source.backup(destination)
            logger.info(f"Backed up existing DB to {backup_path}")
        except Exception as e:
            logger.warning(f"Backup failed: {e}")

    # Load source files
    playlists_raw = _load_playlists_batch(out_dir)
    mappings_raw = _load_mappings_batch(out_dir)
    all_vids = _load_videos_full(out_dir)
    transcript_map = _load_transcript_map(out_dir)
    comment_stats = _load_comment_stats(out_dir)

    # failure ids
    failed_ids: set[str] = set()
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
                        if j.get("video_id"):
                            failed_ids.add(j["video_id"])
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            logger.debug(f"Failed to read failure file {fp}: {e}")

    # counts for quality
    playlist_count_map: dict[str, int] = {}
    for m in mappings_raw:
        playlist_count_map[m["video_id"]] = playlist_count_map.get(m["video_id"], 0) + 1

    channel_count_map: dict[str, int] = {}
    for v in all_vids.values():
        cid = v.get("channel_id") or v.get("channel") or "unknown"
        channel_count_map[cid] = channel_count_map.get(cid, 0) + 1

    # Build DB with context manager
    with db_connection(db_path) as conn:
        ensure_schema(conn)
        cur = conn.cursor()

        # Clear and repopulate derived tables (but keep videos user columns via upsert logic)
        # For simplicity, delete FTS content and re-insert; videos table uses INSERT OR REPLACE with preserved fields
        try:
            cur.execute("DELETE FROM videos_fts")
            cur.execute("DELETE FROM transcripts_fts")
            cur.execute("DELETE FROM comments_fts")
            cur.execute("DELETE FROM playlists_fts")
            cur.execute("DELETE FROM summaries_fts")
            cur.execute("DELETE FROM playlist_videos")
            cur.execute("DELETE FROM playlists")
            cur.execute("DELETE FROM transcripts")
            cur.execute("DELETE FROM comments")
            cur.execute("DELETE FROM failures")
            cur.execute("DELETE FROM summaries")
        except sqlite3.OperationalError as e:
            logger.debug(f"Clear tables note: {e}")

        # Insert playlists
        pl_batch = []
        fts_batch = []
        for j in playlists_raw:
            pid = j.get("playlist_id")
            if not pid:
                continue
            pl_batch.append((
                pid,
                j.get("title", ""),
                j.get("description", ""),
                j.get("channel", ""),
                j.get("channel_id", ""),
                j.get("uploader", ""),
                j.get("video_count", 0),
                j.get("url", ""),
                json.dumps(j, ensure_ascii=False),
            ))
            fts_batch.append((
                pid,
                j.get("title", ""),
                j.get("description", ""),
                j.get("channel", ""),
            ))
            if len(pl_batch) >= 200:
                cur.executemany(
                    "INSERT OR REPLACE INTO playlists VALUES (?,?,?,?,?,?,?, ?, ?)",
                    pl_batch,
                )
                cur.executemany("INSERT INTO playlists_fts VALUES (?,?,?,?)", fts_batch)
                pl_batch.clear()
                fts_batch.clear()
        if pl_batch:
            cur.executemany(
                "INSERT OR REPLACE INTO playlists VALUES (?,?,?,?,?,?,?, ?, ?)",
                pl_batch,
            )
            cur.executemany("INSERT INTO playlists_fts VALUES (?,?,?,?)", fts_batch)

        # Insert mappings
        map_batch = []
        for j in mappings_raw:
            map_batch.append((j["playlist_id"], j["video_id"], j.get("position", 0)))
            if len(map_batch) >= 1000:
                cur.executemany(
                    "INSERT OR IGNORE INTO playlist_videos(playlist_id, video_id, position) VALUES (?,?,?)",
                    map_batch,
                )
                map_batch.clear()
        if map_batch:
            cur.executemany(
                "INSERT OR IGNORE INTO playlist_videos(playlist_id, video_id, position) VALUES (?,?,?)",
                map_batch,
            )

        # Insert videos with quality and preserved user data
        v_batch = []
        v_fts = []
        for vid, j in all_vids.items():
            if cancel_check and cancel_check():
                logger.info("DB build cancelled")
                raise RuntimeError("DB build cancelled; database changes rolled back")
            try:
                trans = transcript_map.get(vid)
                cstats = comment_stats.get(vid)
                pcount = playlist_count_map.get(vid, 0)
                chid = j.get("channel_id") or j.get("channel") or "unknown"
                chcount = channel_count_map.get(chid, 0)
                score, details = compute_quality_score(
                    j, trans, cstats, pcount, chcount
                )

                status = "ok"
                if vid in failed_ids and not trans:
                    status = "no_transcript"
                if not trans and not j.get("description"):
                    status = "needs_review"
                if trans and len(trans.get("text", "").split()) < 100:
                    status = "short_transcript"

                # preserved
                ps = merged_scores.get(vid, {})
                user_score = ps.get("user_score")
                user_notes = ps.get("user_notes", "")
                summ = merged_summaries.get(vid, {})
                summary = summ.get("summary", "")
                summary_source = summ.get("source", "")
                summary_date = summ.get("created_at", "")

                tags_str = " ".join(j.get("tags") or [])
                v_batch.append((
                    vid,
                    j.get("title", ""),
                    j.get("description", ""),
                    j.get("channel", ""),
                    j.get("channel_id", ""),
                    j.get("uploader", ""),
                    j.get("upload_date", ""),
                    j.get("timestamp", 0),
                    j.get("duration", 0),
                    j.get("view_count", 0),
                    j.get("like_count", 0),
                    j.get("comment_count", 0)
                    or (cstats.get("count", 0) if cstats else 0),
                    json.dumps(j.get("tags") or []),
                    json.dumps(j.get("categories") or []),
                    j.get("thumbnail", ""),
                    j.get("url", ""),
                    j.get("language", ""),
                    json.dumps(j.get("chapters") or []),
                    score,
                    details.get("label", ""),
                    json.dumps(details, ensure_ascii=False),
                    user_score,
                    user_notes,
                    summary,
                    summary_source,
                    summary_date,
                    status,
                    json.dumps(j, ensure_ascii=False),
                ))
                v_fts.append((
                    vid,
                    j.get("title", ""),
                    (j.get("description", "") or "")[:5000],
                    tags_str,
                    j.get("channel", ""),
                ))
                if len(v_batch) >= 500:
                    cur.executemany(
                        "INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        v_batch,
                    )
                    cur.executemany("INSERT INTO videos_fts VALUES (?,?,?,?,?)", v_fts)
                    v_batch.clear()
                    v_fts.clear()
            except Exception as e:
                logger.warning(f"Video insert error {vid}: {e}")

        if v_batch:
            cur.executemany(
                "INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                v_batch,
            )
            cur.executemany("INSERT INTO videos_fts VALUES (?,?,?,?,?)", v_fts)

        # Transcripts
        t_batch = []
        tf_batch = []
        for vid, j in transcript_map.items():
            if not j.get("text"):
                continue
            if not is_valid_video_id(vid):
                continue
            wc = len(j.get("text", "").split())
            t_batch.append((
                vid,
                j.get("title", ""),
                j.get("url", ""),
                j.get("channel", ""),
                j.get("channel_id", ""),
                j.get("text", ""),
                wc,
                json.dumps(j, ensure_ascii=False),
            ))
            tf_batch.append((vid, j.get("title", ""), j.get("text", "")))
            if len(t_batch) >= 500:
                cur.executemany(
                    "INSERT OR REPLACE INTO transcripts VALUES (?,?,?,?,?,?,?,?)",
                    t_batch,
                )
                cur.executemany("INSERT INTO transcripts_fts VALUES (?,?,?)", tf_batch)
                t_batch.clear()
                tf_batch.clear()
        if t_batch:
            cur.executemany(
                "INSERT OR REPLACE INTO transcripts VALUES (?,?,?,?,?,?,?,?)", t_batch
            )
            cur.executemany("INSERT INTO transcripts_fts VALUES (?,?,?)", tf_batch)

        # Comments
        comments_path = out_dir / "comments.jsonl"
        if comments_path.exists():
            c_batch = []
            cf_batch = []
            try:
                with open(comments_path, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            j = json.loads(line)
                            if not j.get("text"):
                                continue
                            if not is_valid_video_id(j.get("video_id", "")):
                                continue
                            likes = parse_vote_count(
                                j.get("votes") if "votes" in j else j.get("likes")
                            )
                            c_batch.append((
                                j.get("video_id", ""),
                                j.get("author", ""),
                                j.get("text", ""),
                                likes,
                                j.get("time", ""),
                                json.dumps(j, ensure_ascii=False),
                            ))
                            cf_batch.append((
                                j.get("video_id", ""),
                                j.get("author", ""),
                                j.get("text", ""),
                            ))
                            if len(c_batch) >= 1000:
                                cur.executemany(
                                    "INSERT INTO comments(video_id, author, text, likes, time, json) VALUES (?,?,?,?,?,?)",
                                    c_batch,
                                )
                                cur.executemany(
                                    "INSERT INTO comments_fts VALUES (?,?,?)", cf_batch
                                )
                                c_batch.clear()
                                cf_batch.clear()
                        except json.JSONDecodeError:
                            continue
            except OSError as e:
                logger.warning(f"Comments load failed: {e}")
            if c_batch:
                cur.executemany(
                    "INSERT INTO comments(video_id, author, text, likes, time, json) VALUES (?,?,?,?,?,?)",
                    c_batch,
                )
                cur.executemany("INSERT INTO comments_fts VALUES (?,?,?)", cf_batch)

        # Failures
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
                            cur.execute(
                                "INSERT INTO failures(video_id, type, error, url, title, timestamp) VALUES (?,?,?,?,?,?)",
                                (
                                    j.get("video_id"),
                                    j.get("type") or fp.stem.replace("failed_", ""),
                                    j.get("error", ""),
                                    j.get("url", ""),
                                    j.get("title", ""),
                                    datetime.now().isoformat(),
                                ),
                            )
                        except json.JSONDecodeError:
                            continue
            except OSError as e:
                logger.debug(f"Failed reading {fp}: {e}")

        # Summaries FTS
        for vid, s in merged_summaries.items():
            try:
                cur.execute(
                    "INSERT INTO summaries_fts(video_id, summary) VALUES (?,?)",
                    (vid, s.get("summary", "")),
                )
                cur.execute(
                    "INSERT OR IGNORE INTO summaries(video_id, summary, source, created_at) VALUES (?,?,?,?)",
                    (
                        vid,
                        s.get("summary", ""),
                        s.get("source", ""),
                        s.get("created_at", ""),
                    ),
                )
            except sqlite3.OperationalError as e:
                logger.debug(f"Summaries FTS insert failed for {vid}: {e}")

        # Stats
        cur.execute("SELECT COUNT(*) FROM videos")
        vc = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM playlists")
        pc = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM playlist_videos")
        mc = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM transcripts")
        tc = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM comments")
        cc = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM failures")
        fc = cur.fetchone()[0]
        cur.execute("SELECT AVG(quality_score) FROM videos")
        avg_q = cur.fetchone()[0] or 0

    _log(
        f"DB built: {vc} videos avgQ {avg_q:.1f}, {pc} playlists, {mc} mappings, {tc} transcripts, {cc} comments, {fc} failures",
        progress_cb,
    )
    return {
        "videos": vc,
        "playlists": pc,
        "mappings": mc,
        "transcripts": tc,
        "comments": cc,
        "failures": fc,
        "avg_quality": round(avg_q, 1),
    }


# ---------------------------------------------------------------------------
# User edits - append-only
# ---------------------------------------------------------------------------
def update_video_user_score(
    db_path: Path,
    video_id: str,
    user_score: float,
    user_notes: str = "",
    out_dir: Path | None = None,
) -> bool:
    out_dir = Path(out_dir) if out_dir else Path(db_path).parent
    db_path = Path(db_path)
    scores_path = out_dir / "user_scores.jsonl"
    record = {
        "video_id": video_id,
        "user_score": user_score,
        "user_notes": user_notes,
        "updated_at": datetime.now().isoformat(),
    }
    # Append-only O(1)
    try:
        append_jsonl(scores_path, record)
    except Exception as e:
        logger.error(f"Failed to append score: {e}")

    if db_path.exists():
        try:
            with db_connection(db_path) as conn:
                conn.execute(
                    "UPDATE videos SET user_score=?, user_notes=? WHERE video_id=?",
                    (user_score, user_notes, video_id),
                )
        except sqlite3.OperationalError as e:
            logger.error(f"DB update score failed for {video_id}: {e}")
    return True


def update_video_summary(
    db_path: Path,
    video_id: str,
    summary: str,
    source: str = "user_paste",
    out_dir: Path | None = None,
) -> bool:
    out_dir = Path(out_dir) if out_dir else Path(db_path).parent
    db_path = Path(db_path)
    summaries_path = out_dir / "summaries.jsonl"
    rag_path = out_dir / "rag_dataset.jsonl"
    record = {
        "video_id": video_id,
        "summary": summary,
        "source": source,
        "created_at": datetime.now().isoformat(),
    }
    try:
        append_jsonl(summaries_path, record)
        # Export RAG on demand - write deduped
        all_summ = load_jsonl_deduped(summaries_path, "video_id")
        rewrite_jsonl_deduped(
            rag_path,
            {
                vid: {"video_id": vid, "summary": s["summary"], "source": s["source"]}
                for vid, s in all_summ.items()
            },
        )
    except Exception as e:
        logger.error(f"Failed to append summary: {e}")

    if db_path.exists():
        try:
            with db_connection(db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE videos SET summary=?, summary_source=?, summary_date=? WHERE video_id=?",
                    (summary, source, datetime.now().isoformat(), video_id),
                )
                try:
                    cur.execute(
                        "DELETE FROM summaries_fts WHERE video_id=?", (video_id,)
                    )
                    cur.execute(
                        "INSERT INTO summaries_fts(video_id, summary) VALUES (?,?)",
                        (video_id, summary),
                    )
                    cur.execute(
                        "INSERT OR REPLACE INTO summaries(video_id, summary, source, created_at) VALUES (?,?,?,?)",
                        (video_id, summary, source, datetime.now().isoformat()),
                    )
                except sqlite3.OperationalError as e:
                    logger.debug(f"FTS summary update failed: {e}")
        except sqlite3.OperationalError as e:
            logger.error(f"DB update summary failed for {video_id}: {e}")
    return True


# ---------------------------------------------------------------------------
# Get video copy data - with truncation to avoid OOM
# ---------------------------------------------------------------------------
def get_video_copy_data(
    db_path: Path,
    video_id: str,
    max_transcript_chars: int = 15000,
    max_combined_chars: int = 20000,
) -> dict[str, Any]:
    db_path = Path(db_path)
    if not db_path.exists():
        return {"error": "DB not found"}
    try:
        with db_connection(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM videos WHERE video_id=?", (video_id,))
            row = cur.fetchone()
            if not row:
                return {"error": f"Video {video_id} not found"}
            video = dict(row)
            cur.execute("SELECT text FROM transcripts WHERE video_id=?", (video_id,))
            t_row = cur.fetchone()
            transcript = t_row["text"] if t_row else ""
            # Truncate transcript to avoid OOM
            if len(transcript) > max_transcript_chars:
                transcript = (
                    transcript[:max_transcript_chars]
                    + f"\n...[truncated {len(transcript) - max_transcript_chars} chars]"
                )

            cur.execute(
                "SELECT author, text, likes FROM comments WHERE video_id=? ORDER BY likes DESC LIMIT 50",
                (video_id,),
            )
            comments = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT playlist_id FROM playlist_videos WHERE video_id=?", (video_id,)
            )
            playlists = [r[0] for r in cur.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"DB error in get_video_copy_data: {e}")
        return {"error": str(e)}

    combined = (
        f"TITLE: {video.get('title', '')}\nCHANNEL: {video.get('channel', '')} | URL: {video.get('url', '')}\nTAGS: {video.get('tags', '')}\n\nDESCRIPTION:\n{(video.get('description', '') or '')[:2000]}\n\nTRANSCRIPT:\n{transcript}\n\nTOP COMMENTS:\n"
        + "\n".join([f"- {c['author']}: {c['text'][:200]}" for c in comments[:20]])
    )
    if len(combined) > max_combined_chars:
        combined = combined[:max_combined_chars] + "\n...[truncated]"

    return {
        "video": video,
        "transcript": transcript,
        "comments": comments,
        "playlists": playlists,
        "combined_for_ai": combined,
    }


def export_rag_dataset(out_dir: Path, db_path: Path | None = None) -> dict[str, Any]:
    out_dir = Path(out_dir)
    db_path = Path(db_path) if db_path else out_dir / "archive.db"
    rag_jsonl = out_dir / "rag_dataset.jsonl"
    rag_md = out_dir / "rag_context.md"
    summaries_path = out_dir / "summaries.jsonl"

    if not summaries_path.exists():
        return {"error": "No summaries yet"}

    # Build RAG files from deduped source
    all_summ = load_jsonl_deduped(summaries_path, "video_id")
    try:
        with open(rag_jsonl, "w", encoding="utf-8") as fout:
            fout.writelines(
                json.dumps(s, ensure_ascii=False) + "\n" for s in all_summ.values()
            )
    except OSError as e:
        logger.error(f"Failed to write rag_jsonl: {e}")
        return {"error": str(e)}

    md = "# Knowledgebase Summaries\n\n"
    if db_path.exists():
        try:
            with db_connection(db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT video_id, title, channel, url, summary, tags FROM videos WHERE summary IS NOT NULL AND summary != ''"
                )
                for r in cur.fetchall():
                    md += f"## {r['title']} [{r['video_id']}]\nChannel: {r['channel']} | {r['url']}\nTags: {r['tags']}\n\nSummary:\n{r['summary']}\n\n---\n\n"
        except sqlite3.Error as e:
            logger.warning(f"Failed to build rag md from DB: {e}")

    try:
        rag_md.write_text(md, encoding="utf-8")
    except OSError as e:
        logger.error(f"Failed to write rag_md: {e}")
        return {"error": str(e)}

    try:
        count = sum(1 for _ in open(rag_jsonl, encoding="utf-8"))
    except OSError:
        count = len(all_summ)
    return {"rag_jsonl": str(rag_jsonl), "rag_md": str(rag_md), "count": count}


# ---------------------------------------------------------------------------
# LLM - retry logic
# ---------------------------------------------------------------------------
DEFAULT_SUMMARY_PROMPT = """You are a knowledgebase summarizer. Summarize the YouTube video below for later retrieval.

Requirements:
- 3-5 bullet key takeaways
- 1 paragraph concise summary (2-4 sentences)
- List any tools, libraries, commands, or people mentioned
- Keep it factual, no fluff

TITLE: {title}
CHANNEL: {channel}
TAGS: {tags}
DESCRIPTION:
{description}

TRANSCRIPT (truncated):
{transcript}

TOP COMMENTS:
{comments}

Return in this format:
SUMMARY: <paragraph>
KEY POINTS:
- ...
TOOLS/LIBS:
- ...
"""


def _retry_with_backoff(attempts: int = 3, backoff_base: float = 1.5):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    # Only retry on transient errors
                    transient = (
                        isinstance(e, (ConnectionError, TimeoutError))
                        or "503" in str(e)
                        or "502" in str(e)
                        or "connection" in str(e).lower()
                    )
                    if not transient and attempt == 0:
                        # still retry once for local LLM busy
                        pass
                    if attempt < attempts - 1:
                        sleep = backoff_base**attempt
                        logger.warning(
                            f"{fn.__name__} failed attempt {attempt + 1}/{attempts}: {e}, retrying in {sleep}s"
                        )
                        time.sleep(sleep)
                    else:
                        logger.error(
                            f"{fn.__name__} failed after {attempts} attempts: {e}"
                        )
            raise last_exc if last_exc else RuntimeError("Retry failed")

        return wrapper

    return decorator


@_retry_with_backoff(attempts=3)
def _call_ollama(
    prompt: str,
    endpoint: str,
    model: str,
    temperature: float = 0.2,
    num_ctx: int = 8192,
) -> str:
    import requests

    url = endpoint.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    resp = requests.post(url, json=payload, timeout=CONFIG.request_timeout)
    resp.raise_for_status()
    data = resp.json()
    return data.get("response", "").strip()


@_retry_with_backoff(attempts=3)
def _call_lmstudio(
    prompt: str,
    endpoint: str,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> str:
    import requests

    base = endpoint.rstrip("/")
    url = (
        base + "/chat/completions"
        if base.endswith("/v1")
        else base + "/v1/chat/completions"
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful knowledgebase summarizer. Be concise and factual.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    resp = requests.post(url, json=payload, timeout=CONFIG.request_timeout)
    if resp.status_code == 404:
        # fallback to completions
        url2 = base + "/completions"
        payload2 = {
            "model": model,
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        resp = requests.post(url2, json=payload2, timeout=CONFIG.request_timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("choices"):
            return (
                data["choices"][0].get("text", "").strip()
                or data["choices"][0].get("message", {}).get("content", "").strip()
            )
        return str(data)
    resp.raise_for_status()
    data = resp.json()
    if data.get("choices"):
        choice = data["choices"][0]
        if "message" in choice:
            return choice["message"].get("content", "").strip()
        return choice.get("text", "").strip()
    return ""


@_retry_with_backoff(attempts=3)
def _call_openai_compat(
    prompt: str,
    endpoint: str,
    model: str,
    api_key: str = "",
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> str:
    import requests

    base = endpoint.rstrip("/")
    url = base + "/chat/completions" if not base.endswith("/chat/completions") else base
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful knowledgebase summarizer.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(
        url, json=payload, headers=headers, timeout=CONFIG.request_timeout
    )
    resp.raise_for_status()
    data = resp.json()
    return (
        data["choices"][0]["message"]["content"].strip() if data.get("choices") else ""
    )


def build_summary_prompt(
    video: dict,
    transcript: str,
    comments: list[dict],
    custom_template: str | None = None,
) -> str:
    tags_list = (
        safe_json_loads(video.get("tags") or "[]", default=[])
        if isinstance(video.get("tags"), str)
        else (video.get("tags") or [])
    )
    tags = ", ".join(tags_list[:15]) if isinstance(tags_list, list) else ""
    desc = (video.get("description") or "")[:2000]
    trans = (transcript or "")[:12000]
    comm_text = "\n".join([
        f"- {c.get('author', '')}: {c.get('text', '')[:200]}"
        for c in (comments or [])[:15]
    ])
    template = custom_template or DEFAULT_SUMMARY_PROMPT
    try:
        return template.format(
            title=video.get("title", ""),
            channel=video.get("channel", ""),
            tags=tags,
            description=desc,
            transcript=trans,
            comments=comm_text,
        )
    except KeyError as e:
        logger.warning(f"Custom prompt missing key {e}, using default")
        return DEFAULT_SUMMARY_PROMPT.format(
            title=video.get("title", ""),
            channel=video.get("channel", ""),
            tags=tags,
            description=desc,
            transcript=trans,
            comments=comm_text,
        )


def summarize_with_local_llm(
    video: dict,
    transcript: str,
    comments: list[dict],
    llm_config: dict,
    progress_cb: ProgressCb = None,
) -> str:
    provider = llm_config.get("provider", CONFIG.default_provider)
    endpoint = llm_config.get(
        "endpoint",
        CONFIG.ollama_endpoint if provider == "ollama" else CONFIG.lmstudio_endpoint,
    )
    model = llm_config.get("model", CONFIG.default_model)
    temperature = float(llm_config.get("temperature", 0.2))
    custom_prompt = llm_config.get("prompt_template", "")

    prompt = build_summary_prompt(video, transcript, comments, custom_prompt)
    _log(
        f"LLM summarizing {video.get('video_id')} via {provider} {model} @ {endpoint}",
        progress_cb,
    )

    if provider == "ollama":
        return _call_ollama(prompt, endpoint, model, temperature)
    elif provider in ("lmstudio", "lm_studio"):
        return _call_lmstudio(
            prompt,
            endpoint,
            model,
            temperature,
            max_tokens=int(llm_config.get("max_tokens", 1024)),
        )
    else:
        return _call_openai_compat(
            prompt,
            endpoint,
            model,
            llm_config.get("api_key", ""),
            temperature,
            int(llm_config.get("max_tokens", 1024)),
        )


def generate_summary_for_video(
    video_id: str,
    out_dir: Path,
    db_path: Path,
    llm_config: dict,
    progress_cb: ProgressCb = None,
    cancel_check: CancelCheck = None,
) -> dict:
    out_dir = Path(out_dir)
    db_path = Path(db_path)
    data = get_video_copy_data(
        db_path,
        video_id,
        max_transcript_chars=CONFIG.transcript_max_chars,
        max_combined_chars=CONFIG.combined_max_chars,
    )
    if "error" in data:
        return {"error": data["error"]}
    video = data.get("video", {})
    transcript = data.get("transcript", "")
    comments = data.get("comments", [])

    if cancel_check and cancel_check():
        return {"error": "Cancelled", "video_id": video_id}

    if not transcript and not video.get("description"):
        return {
            "error": "No transcript or description to summarize",
            "video_id": video_id,
        }

    try:
        summary = summarize_with_local_llm(
            video, transcript, comments, llm_config, progress_cb=progress_cb
        )
        if not summary:
            return {"error": "Empty summary from LLM", "video_id": video_id}
        update_video_summary(
            db_path,
            video_id,
            summary,
            source=f"{llm_config.get('provider')}:{llm_config.get('model')}",
            out_dir=out_dir,
        )
        return {"video_id": video_id, "summary": summary, "status": "ok"}
    except Exception as e:
        _log(f"LLM summarize fail {video_id}: {e}", progress_cb)
        logger.exception(f"LLM summarize fail {video_id}")
        return {"error": str(e), "video_id": video_id}


def batch_summarize(
    out_dir: Path,
    db_path: Path,
    llm_config: dict,
    only_missing: bool = True,
    limit: int = 0,
    progress_cb: ProgressCb = None,
    cancel_check: CancelCheck = None,
) -> dict:
    out_dir = Path(out_dir)
    db_path = Path(db_path)
    if not db_path.exists():
        return {"error": f"DB not found at {db_path}"}

    try:
        with db_connection(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if only_missing:
                cur.execute(
                    "SELECT video_id FROM videos WHERE summary IS NULL OR summary = ''"
                )
            else:
                cur.execute("SELECT video_id FROM videos")
            rows = cur.fetchall()
            vids = [r["video_id"] for r in rows]
            if limit and limit > 0:
                vids = vids[:limit]
    except sqlite3.Error as e:
        logger.error(f"Batch summarize DB error: {e}")
        return {"error": str(e)}

    _log(
        f"Batch summarize: {len(vids)} videos to process (only_missing={only_missing})",
        progress_cb,
    )

    ok = 0
    failed = 0
    results = []

    for vid in vids:
        if cancel_check and cancel_check():
            _log("Batch summarize cancelled", progress_cb)
            break
        res = generate_summary_for_video(
            vid,
            out_dir,
            db_path,
            llm_config,
            progress_cb=progress_cb,
            cancel_check=cancel_check,
        )
        results.append(res)
        if "error" in res:
            failed += 1
        else:
            ok += 1
        time.sleep(0.5)

    _log(f"Batch done: {ok} ok, {failed} failed", progress_cb)
    return {"ok": ok, "failed": failed, "results": results}


def test_llm_connection(llm_config: dict) -> dict:
    try:
        provider = llm_config.get("provider", "ollama")
        endpoint = llm_config.get("endpoint", "")
        model = llm_config.get("model", "")
        if provider == "ollama":
            import requests

            url = endpoint.rstrip("/") + "/api/tags"
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("name") for m in data.get("models", [])]
            return {
                "ok": True,
                "provider": provider,
                "endpoint": endpoint,
                "models_available": models,
                "requested_model": model,
                "model_found": model in models or len(models) == 0,
            }
        elif provider in ("lmstudio", "lm_studio"):
            import requests

            base = endpoint.rstrip("/")
            url = base + "/models" if base.endswith("/v1") else base + "/v1/models"
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("id") for m in data.get("data", [])]
            return {
                "ok": True,
                "provider": provider,
                "endpoint": endpoint,
                "models_available": models,
            }
        else:
            return {
                "ok": True,
                "provider": provider,
                "endpoint": endpoint,
                "note": "Custom OpenAI compat, not tested",
            }
    except Exception as e:
        logger.warning(f"LLM test failed: {e}")
        return {"ok": False, "error": str(e), "config": llm_config}
