"""
Sarvam AI TTS Provider — Bulbul v3 model.

Purpose-built for Indian languages and accents.
Supports 11 Indian languages with 30+ voices.

API: POST https://api.sarvam.ai/text-to-speech
Response: JSON with base64-encoded audio in `audios` field.

Controls:
  pace:        0.5 – 2.0  (speech speed)
  temperature: 0.01 – 2.0 (expressiveness; v3 only)
"""

import base64
import logging
# Note: base64 still needed — Sarvam API returns base64-encoded audio in JSON response

import httpx

from app.config import settings
from app.models import NarrativeBranch, SUPPORTED_LANGUAGES
# SUPPORTED_LANGUAGES still needed for sarvam_language_code lookup

logger = logging.getLogger(__name__)

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

# Map narrative branches → (pace, temperature) for expressiveness control
# Higher temperature = more expressive (max 1.0); faster pace for excitement
VOICE_PARAMS: dict[NarrativeBranch, dict] = {
    # Dramatic moments — high expressiveness, faster pace
    NarrativeBranch.WICKET_DRAMA: {"pace": 1.2, "temperature": 1.0},
    NarrativeBranch.BOUNDARY_MOMENTUM: {"pace": 1.15, "temperature": 0.95},
    NarrativeBranch.EXTRA_GIFT: {"pace": 1.1, "temperature": 0.9},
    # Balanced moments — natural pace, moderate expressiveness
    NarrativeBranch.PRESSURE_BUILDER: {"pace": 0.95, "temperature": 0.7},
    NarrativeBranch.OVER_TRANSITION: {"pace": 1.0, "temperature": 0.6},
    # Calm, measured — steady pace, low expressiveness
    NarrativeBranch.ROUTINE: {"pace": 1.0, "temperature": 0.4},
}

DEFAULT_PARAMS = {"pace": 1.0, "temperature": 0.6}


def _get_voice_params(branch: NarrativeBranch, is_pivot: bool) -> dict:
    """Get pace and temperature for the given narrative context."""
    if is_pivot:
        return {"pace": 1.25, "temperature": 1.0}
    return VOICE_PARAMS.get(branch, DEFAULT_PARAMS)


def _get_sarvam_language_code(language: str) -> str:
    """Return the BCP-47 language code for Sarvam API."""
    lang_cfg = SUPPORTED_LANGUAGES.get(language, {})
    return lang_cfg.get("sarvam_language_code", "en-IN")


async def synthesize(
    text: str,
    branch: NarrativeBranch,
    is_pivot: bool = False,
    language: str = "en",
    voice_id: str = "",
    model_id: str = "",
) -> bytes | None:
    """
    Convert commentary text to speech using Sarvam AI API.
    Returns raw MP3 audio bytes, or None if TTS fails.

    Args:
        voice_id: Sarvam speaker name (from languages.json tts_voice_id).
        model_id: Sarvam model name (from languages.json tts_model).
    """
    if not settings.sarvam_api_key:
        logger.warning("Sarvam API key not configured, skipping TTS")
        return None

    voice_params = _get_voice_params(branch, is_pivot)
    language_code = _get_sarvam_language_code(language)
    speaker = voice_id or settings.sarvam_speaker
    model = model_id or "bulbul:v3"

    payload: dict = {
        "text": text,
        "target_language_code": language_code,
        "model": model,
        "speaker": speaker,
        "pace": voice_params["pace"],
        "temperature": voice_params["temperature"],
        "speech_sample_rate": 44100,
        "output_audio_codec": "mp3",
    }

    headers = {
        "api-subscription-key": settings.sarvam_api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                SARVAM_TTS_URL, json=payload, headers=headers
            )
            response.raise_for_status()

            data = response.json()
            audios = data.get("audios")
            if audios and len(audios) > 0:
                # Sarvam returns base64-encoded audio — decode to raw bytes
                return base64.b64decode(audios[0])

            logger.warning("Sarvam returned empty audio")
            return None

    except httpx.HTTPStatusError as e:
        logger.error(
            f"Sarvam API error {e.response.status_code}: {e.response.text}"
        )
        return None
    except Exception as e:
        logger.error(f"Sarvam TTS error: {e}")
        return None
