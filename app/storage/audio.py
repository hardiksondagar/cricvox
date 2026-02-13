"""
Local audio file storage with content-hashed filenames.

Saves MP3 files to static/audio/{match_id}/{hash}.mp3.
The hash is deterministic: sha256(text + voice_id + tts_provider + language)[:16].

If the file already exists on disk, the write is skipped (cache hit).
This means re-generating the same text with the same voice reuses cached audio.
"""

import hashlib
import logging
from pathlib import Path

from app.models import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

AUDIO_DIR = Path("static/audio")


def _compute_hash(text: str, language: str) -> str:
    """Compute a deterministic 16-char hash for audio deduplication.
    
    Uses the language's tts_vendor + tts_voice_id from languages.json
    so changing vendor/voice invalidates the cache.
    """
    lang_cfg = SUPPORTED_LANGUAGES.get(language, {})
    vendor = lang_cfg.get("tts_vendor", "unknown")
    voice_id = lang_cfg.get("tts_voice_id", "default")
    key = f"{text}|{vendor}|{voice_id}|{language}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def save_audio(match_id: int, text: str, language: str, audio_bytes: bytes) -> str:
    """
    Write MP3 bytes to disk and return the URL path.
    Uses content-hashed filename for deduplication.

    Args:
        match_id:    Integer match ID (directory name).
        text:        The commentary text (used in hash).
        language:    Language code (used in hash).
        audio_bytes: Raw MP3 audio data.

    Returns:
        URL path like "/static/audio/1/a3f8c1d2e7b4f9a1.mp3"
    """
    match_dir = AUDIO_DIR / str(match_id)
    match_dir.mkdir(parents=True, exist_ok=True)

    file_hash = _compute_hash(text, language)
    filename = f"{file_hash}.mp3"
    filepath = match_dir / filename

    # Cache hit — skip write
    if filepath.exists():
        url_path = f"/static/audio/{match_id}/{filename}"
        logger.debug(f"Audio cache hit: {url_path}")
        return url_path

    filepath.write_bytes(audio_bytes)

    url_path = f"/static/audio/{match_id}/{filename}"
    logger.debug(f"Saved audio: {url_path} ({len(audio_bytes)} bytes)")
    return url_path


def clear_audio(match_id: int) -> int:
    """Delete all audio files for a match. Returns count deleted."""
    match_dir = AUDIO_DIR / str(match_id)
    if not match_dir.exists():
        return 0
    count = 0
    for f in match_dir.glob("*.mp3"):
        f.unlink()
        count += 1
    logger.info(f"Cleared {count} audio files for match {match_id}")
    return count
