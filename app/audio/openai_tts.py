"""
OpenAI TTS Provider — gpt-4o-mini-tts model.

Supports emotion/style control via `instructions` parameter.
The model interprets natural language instructions to adjust
tone, pace, and emotion of the generated speech.

Voices: alloy, ash, ballad, coral, echo, fable, onyx, nova, sage, shimmer
"""

import logging

from openai import AsyncOpenAI

from app.config import settings
from app.models import NarrativeBranch

logger = logging.getLogger(__name__)

# Lazy-initialized client
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


# Map narrative branches → TTS instructions for emotional delivery
VOICE_INSTRUCTIONS: dict[NarrativeBranch, str] = {
    NarrativeBranch.WICKET_DRAMA: (
        "You are a cricket commentator. Deliver this with HIGH energy and excitement. "
        "Raise your voice on key words. Sound thrilled, dramatic, like a big wicket just fell. "
        "Slightly faster pace, punchy delivery."
    ),
    NarrativeBranch.BOUNDARY_MOMENTUM: (
        "You are a cricket commentator. Deliver this with excitement and enthusiasm. "
        "Sound impressed and energetic. Emphasize the boundary with a rising tone. "
        "Faster pace, celebratory energy."
    ),
    NarrativeBranch.EXTRA_GIFT: (
        "You are a cricket commentator. Deliver this with mild surprise and commentary flair. "
        "Sound slightly amused — extras are free runs. Brief, punchy."
    ),
    NarrativeBranch.PRESSURE_BUILDER: (
        "You are a cricket commentator. Deliver this with a tense, building tone. "
        "Sound thoughtful and slightly serious. Slower, measured pace. "
        "Convey the pressure of dot balls and tight bowling."
    ),
    NarrativeBranch.OVER_TRANSITION: (
        "You are a cricket commentator. Deliver this in a conversational, analytical tone. "
        "Sound balanced and informative. Normal pace. "
        "This is a reflective moment between overs."
    ),
    NarrativeBranch.ROUTINE: (
        "You are a cricket commentator. Deliver this calmly and naturally. "
        "Understated, professional tone. Normal pace. Brief and clean."
    ),
}

PIVOT_INSTRUCTION = (
    "You are a cricket commentator at a PIVOTAL moment in the match. "
    "Maximum energy, maximum drama. This is a game-changing moment. "
    "Sound electric, raise your voice, fast-paced delivery. "
    "The crowd is going wild."
)

DEFAULT_INSTRUCTION = (
    "You are a professional cricket commentator. "
    "Deliver this naturally with appropriate energy for the moment."
)


def _get_instructions(branch: NarrativeBranch, is_pivot: bool) -> str:
    """Get the TTS instruction string for the given narrative context."""
    if is_pivot:
        return PIVOT_INSTRUCTION
    return VOICE_INSTRUCTIONS.get(branch, DEFAULT_INSTRUCTION)


async def synthesize(
    text: str,
    branch: NarrativeBranch,
    is_pivot: bool = False,
    language: str = "en",
    voice_id: str = "",
    model_id: str = "",
) -> bytes | None:
    """
    Convert commentary text to speech using OpenAI TTS API.
    Returns raw MP3 audio bytes, or None if TTS fails.

    Args:
        voice_id: OpenAI voice name (from languages.json tts_voice_id).
        model_id: OpenAI TTS model name (from languages.json tts_model).
    """
    if not settings.openai_api_key:
        logger.warning("OpenAI API key not configured, skipping TTS")
        return None

    client = _get_client()
    instructions = _get_instructions(branch, is_pivot)
    voice = voice_id or settings.openai_tts_voice
    model = model_id or "gpt-4o-mini-tts"

    try:
        response = await client.audio.speech.create(
            model=model,
            input=text,
            voice=voice,
            instructions=instructions,
            response_format="mp3",
        )

        audio_bytes = response.read()
        if audio_bytes:
            return audio_bytes

        logger.warning("OpenAI TTS returned empty audio")
        return None

    except Exception as e:
        logger.error(f"OpenAI TTS error: {e}")
        return None
