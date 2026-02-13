"""
Multi-vendor TTS facade — per-language routing.

Each language in languages.json specifies its own tts_vendor, tts_voice_id,
and tts_model. This facade reads that config and dispatches to the correct
provider module for each language.

Providers:
  - "elevenlabs" → ElevenLabs eleven_v3
  - "sarvam"     → Sarvam AI Bulbul v3
  - "openai"     → OpenAI gpt-4o-mini-tts
"""

import importlib
import logging

from app.models import NarrativeBranch, SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

# Provider module registry
_PROVIDER_MODULES: dict[str, str] = {
    "elevenlabs": "app.audio.elevenlabs",
    "sarvam": "app.audio.sarvam",
    "openai": "app.audio.openai_tts",
}

# Cache imported modules
_module_cache: dict[str, object] = {}


def _get_provider_module(vendor: str):
    """Lazily import and cache a provider module."""
    if vendor in _module_cache:
        return _module_cache[vendor]

    module_path = _PROVIDER_MODULES.get(vendor)
    if module_path is None:
        logger.error(
            f"Unknown TTS vendor '{vendor}'. "
            f"Valid options: {', '.join(_PROVIDER_MODULES.keys())}. "
            f"Falling back to elevenlabs."
        )
        module_path = _PROVIDER_MODULES["elevenlabs"]

    mod = importlib.import_module(module_path)
    _module_cache[vendor] = mod
    logger.info(f"TTS provider loaded: {vendor} ({module_path})")
    return mod


async def synthesize_speech(
    text: str,
    branch: NarrativeBranch,
    is_pivot: bool = False,
    language: str = "en",
) -> bytes | None:
    """
    Convert commentary text to speech using the language's configured TTS vendor.
    Returns raw MP3 audio bytes, or None if TTS fails.

    Reads tts_vendor, tts_voice_id, tts_model from SUPPORTED_LANGUAGES[language].
    """
    lang_cfg = SUPPORTED_LANGUAGES.get(language, SUPPORTED_LANGUAGES.get("en", {}))
    vendor = lang_cfg.get("tts_vendor", "elevenlabs")
    voice_id = lang_cfg.get("tts_voice_id", "")
    model_id = lang_cfg.get("tts_model", "")

    mod = _get_provider_module(vendor)
    return await mod.synthesize(text, branch, is_pivot, language, voice_id, model_id)
