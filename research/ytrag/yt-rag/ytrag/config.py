"""Central configuration. Everything local, no cloud dependency."""
from pathlib import Path

# Root data directory (override with YTRAG_HOME env var)
import os
HOME = Path(os.environ.get("YTRAG_HOME", Path.home() / ".ytrag"))
DB_PATH = HOME / "ytrag.db"
RAW_DIR = HOME / "raw"          # per-video json/txt/markdown archive
AUDIO_DIR = HOME / "audio"      # temp audio for whisper fallback

# Embedding model: BAAI/bge-small-en-v1.5 -> 384 dims, CPU/ONNX via fastembed.
# Swap to 'BAAI/bge-base-en-v1.5' (768) for higher quality at ~3x cost.
EMBED_MODEL = os.environ.get("YTRAG_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DIM = int(os.environ.get("YTRAG_EMBED_DIM", "384"))

# Chunking (token-ish; we approximate with characters -> ~4 chars/token)
CHUNK_CHARS = 1200             # ~300 tokens per chunk
CHUNK_OVERLAP = 200            # sliding-window overlap for context continuity

# Hybrid retrieval
BM25_TOPK = 40
VECTOR_TOPK = 40
RRF_K = 60                     # reciprocal-rank-fusion constant
FINAL_TOPK = 10

# Fetch politeness
DEFAULT_DELAY = 0.5            # seconds between transcript fetches
PREFERRED_LANGS = ["en", "en-US", "en-GB"]

def ensure_dirs():
    for d in (HOME, RAW_DIR, AUDIO_DIR):
        d.mkdir(parents=True, exist_ok=True)
