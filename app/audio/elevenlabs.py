"""
ElevenLabs TTS Provider — eleven_v3 model.

Supports 70+ languages including 12 Indian languages.
Auto-detects language from text content — no language_code needed.

stability (v3 discrete values only):
  0.0 = Creative  (most expressive, dramatic)
  0.5 = Natural   (balanced)
  1.0 = Robust    (stable, calm)
"""

import base64
import logging

import httpx

from app.config import settings
from app.models import NarrativeBranch, SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# Map narrative branches → stability value
VOICE_STABILITY: dict[NarrativeBranch, float] = {
    # Dramatic moments — Creative (0.0)
    NarrativeBranch.WICKET_DRAMA: 0.0,
    NarrativeBranch.BOUNDARY_MOMENTUM: 0.0,
    NarrativeBranch.EXTRA_GIFT: 0.0,
    # Balanced moments — Natural (0.5)
    NarrativeBranch.PRESSURE_BUILDER: 0.5,
    NarrativeBranch.OVER_TRANSITION: 0.5,
    # Calm, measured — Robust (1.0)
    NarrativeBranch.ROUTINE: 1.0,
}

DEFAULT_STABILITY = 0.5


def _get_stability(branch: NarrativeBranch, is_pivot: bool) -> float:
    """Get the stability value for the given narrative context."""
    if is_pivot:
        return 0.0
    return VOICE_STABILITY.get(branch, DEFAULT_STABILITY)


def _get_elevenlabs_model(language: str) -> str:
    """Return the best ElevenLabs model for the given language."""
    lang_cfg = SUPPORTED_LANGUAGES.get(language, SUPPORTED_LANGUAGES["en"])
    return lang_cfg.get("elevenlabs_model", "eleven_v3")


async def synthesize(
    text: str,
    branch: NarrativeBranch,
    is_pivot: bool = False,
    language: str = "en",
) -> str | None:
    """
    Convert commentary text to speech using ElevenLabs API.
    Returns base64-encoded MP3 audio string, or None if TTS fails.
    """
    if not settings.elevenlabs_api_key:
        logger.warning("ElevenLabs API key not configured, skipping TTS")
        return None

    stability = _get_stability(branch, is_pivot)
    voice_id = settings.elevenlabs_voice_id
    model_id = _get_elevenlabs_model(language)

    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)

    payload: dict = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": stability,
        },
    }

    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    params = {
        "output_format": "mp3_44100_128",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url, json=payload, headers=headers, params=params
            )
            response.raise_for_status()

            audio_bytes = response.content
            if audio_bytes:
                return base64.b64encode(audio_bytes).decode("utf-8")

            logger.warning("ElevenLabs returned empty audio")
            return None

    except httpx.HTTPStatusError as e:
        logger.error(
            f"ElevenLabs API error {e.response.status_code}: {e.response.text}"
        )
        return None
    except Exception as e:
        logger.error(f"ElevenLabs TTS error: {e}")
        return None
