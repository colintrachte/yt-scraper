"""Optional local transcription for videos whose captions are disabled.

Only runs when you pass --whisper AND the caption path failed. It downloads
just the audio track with yt-dlp, then transcribes locally with
faster-whisper (CTranslate2, CPU or GPU). Nothing leaves your machine.

Install extras only if you need this:
    pip install faster-whisper
    # plus ffmpeg on your system PATH
Respect YouTube's Terms of Service and only transcribe content you have
rights to process.
"""
from pathlib import Path
from . import config


def transcribe(video_id: str, model_size: str = "base") -> list[dict]:
    from faster_whisper import WhisperModel  # lazy import; optional dep
    from yt_dlp import YoutubeDL

    config.ensure_dirs()
    out = config.AUDIO_DIR / f"{video_id}.m4a"
    if not out.exists():
        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(config.AUDIO_DIR / f"{video_id}.%(ext)s"),
            "quiet": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }],
        }
        with YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

    audio = next(config.AUDIO_DIR.glob(f"{video_id}.*"))
    model = WhisperModel(model_size, device="auto", compute_type="int8")
    segments, _info = model.transcribe(str(audio), vad_filter=True)
    snippets = [{"text": seg.text.strip(), "start": seg.start} for seg in segments]

    try:
        Path(audio).unlink()  # clean up audio; we keep only text
    except OSError:
        pass
    return snippets
