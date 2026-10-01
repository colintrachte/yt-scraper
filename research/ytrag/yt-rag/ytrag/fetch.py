"""Enumerate playlists (yt-dlp) and fetch transcripts (youtube-transcript-api 1.2.4).

The transcript library API changed in v1.0.0. The correct current usage is:
    api = YouTubeTranscriptApi()
    fetched = api.fetch(video_id, languages=[...])   # -> FetchedTranscript
    fetched.snippets -> list of FetchedTranscriptSnippet(text, start, duration)
The removed methods (get_transcript / list_transcripts) will crash on 1.x.
"""
import re
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api import (
    NoTranscriptFound, TranscriptsDisabled, RequestBlocked,
    IpBlocked, PoTokenRequired, CouldNotRetrieveTranscript,
)

_api = YouTubeTranscriptApi()


def enumerate_playlist(playlist_url: str, cookies: str | None = None):
    """Return (list[dict video meta], playlist_title). Uses flat extraction,
    so it never downloads video files. Cookies enable private playlists
    (Watch Later = list=WL, or your 'Saved' playlist URL)."""
    opts = {"extract_flat": True, "skip_download": True, "quiet": True,
            "ignoreerrors": True}
    if cookies:
        opts["cookiefile"] = cookies
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
    entries = [e for e in (info.get("entries") or []) if e]
    videos = []
    for e in entries:
        vid = e.get("id")
        if not vid:
            continue
        videos.append({
            "video_id": vid,
            "title": e.get("title") or vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channel": e.get("channel") or e.get("uploader"),
            "duration": e.get("duration"),
            "published": e.get("upload_date"),
        })
    return videos, (info.get("title") or "playlist")


def fetch_transcript(video_id: str, langs):
    """Return (snippets, lang_code, is_generated).
    snippets = list of dicts {text, start}. Raises on hard failures so the
    caller can record status and optionally trigger the whisper fallback."""
    try:
        fetched = _api.fetch(video_id, languages=langs)
        return (
            [{"text": s.text, "start": s.start} for s in fetched.snippets],
            fetched.language_code,
            fetched.is_generated,
        )
    except NoTranscriptFound:
        # Try any available transcript, translating to English if needed.
        tlist = _api.list(video_id)
        for t in tlist:
            try:
                f = t.fetch()
                return ([{"text": s.text, "start": s.start} for s in f.snippets],
                        f.language_code, f.is_generated)
            except Exception:
                continue
        first = next(iter(tlist))
        f = first.translate("en").fetch()
        return ([{"text": s.text, "start": s.start} for s in f.snippets],
                "en", True)


# Exception groups the caller uses to classify outcomes.
DISABLED = (TranscriptsDisabled, NoTranscriptFound)
BLOCKED = (RequestBlocked, IpBlocked, PoTokenRequired)
RETRIEVE_ERR = (CouldNotRetrieveTranscript,)


def clean_filename(s: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", s)[:100]
