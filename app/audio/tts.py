"""
Multi-vendor TTS facade.

Delegates to the configured TTS provider:
  - "elevenlabs" → ElevenLabs eleven_v3  (stability-based emotion control)
  - "sarvam"     → Sarvam AI Bulbul v3   (pace + temperature emotion control)
  - "openai"     → OpenAI gpt-4o-mini-tts (instruction-based emotion control)

Set TTS_PROVIDER in .env to switch providers.
"""

import logging
from typing import Callable, Awaitable

from app.config import settings
from app.models import NarrativeBranch

logger = logging.getLogger(__name__)

# Type alias for the synthesize function signature
SynthesizeFn = Callable[
    [str, NarrativeBranch, bool, str],
    Awaitable[str | None],
]

# Provider registry — lazy-loaded to avoid importing unused providers
_PROVIDERS: dict[str, str] = {
    "elevenlabs": "app.audio.elevenlabs",
    "sarvam": "app.audio.sarvam",
    "openai": "app.audio.openai_tts",
}

_cached_synthesize: SynthesizeFn | None = None


def _get_provider_fn() -> SynthesizeFn:
    """Lazily import and return the synthesize function for the configured provider."""
    global _cached_synthesize
    if _cached_synthesize is not None:
        return _cached_synthesize

    provider = settings.tts_provider.lower().strip()
    module_path = _PROVIDERS.get(provider)

    if module_path is None:
        logger.error(
            f"Unknown TTS provider '{provider}'. "
            f"Valid options: {', '.join(_PROVIDERS.keys())}. "
            f"Falling back to elevenlabs."
        )
        module_path = _PROVIDERS["elevenlabs"]

    import importlib
    mod = importlib.import_module(module_path)
    fn = getattr(mod, "synthesize")

    logger.info(f"TTS provider initialized: {provider} ({module_path})")
    _cached_synthesize = fn
    return fn


async def synthesize_speech(
    text: str,
    branch: NarrativeBranch,
    is_pivot: bool = False,
    language: str = "en",
) -> str | None:
    """
    Convert commentary text to speech using the configured TTS provider.
    Returns base64-encoded MP3 audio string, or None if TTS fails.

    This is the public API — callers don't need to know which provider is active.
    """
    fn = _get_provider_fn()
    return await fn(text, branch, is_pivot, language)
