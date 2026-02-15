import logging

from openai import AsyncOpenAI

from app.config import settings
from app.models import BallEvent, LogicResult, MatchState
from app.commentary.prompts import (
    get_system_prompt,
    get_narrative_system_prompt,
    format_user_prompt,
    build_narrative_prompt,
)

logger = logging.getLogger(__name__)

# Lazy-initialized client
_client: AsyncOpenAI | None = None

# Token budgets for max_completion_tokens.
# Sized to accommodate audio tags (e.g. [excited], [gasps]) which the LLM
# embeds for ElevenLabs v3 TTS — these add ~15-25 tokens on dramatic balls.
# Indic scripts use 2-3x more tokens per word than Latin scripts.
_BALL_TOKENS_EN = 120
_BALL_TOKENS_INDIC = 260
_NARRATIVE_TOKENS_EN = 200
_NARRATIVE_TOKENS_INDIC = 400


def _max_tokens(base_en: int, base_indic: int, language: str) -> int:
    """Return the appropriate token limit for the language."""
    if language == "en":
        return base_en
    return base_indic


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def generate_commentary(
    state: MatchState,
    ball: BallEvent,
    logic_result: LogicResult,
    language: str = "en",
) -> str:
    """
    Generate a single line of cricket commentary using OpenAI GPT.
    Returns the commentary text (15-30 words).
    """
    client = _get_client()
    user_prompt = format_user_prompt(state, ball, logic_result, language=language)
    system_prompt = get_system_prompt(language)
    max_tokens = _max_tokens(_BALL_TOKENS_EN, _BALL_TOKENS_INDIC, language)

    try:
        response = await client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.9,
            max_completion_tokens=max_tokens,
        )
        commentary = response.choices[0].message.content.strip()
        # Strip quotes if the model wraps in quotes
        commentary = commentary.strip('"').strip("'")
        return commentary

    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        # Fallback commentary
        return _fallback_commentary(ball, logic_result)


async def generate_narrative(
    moment_type: str,
    state: MatchState | None = None,
    language: str = "en",
    **kwargs,
) -> str:
    """
    Generate a narrative commentary moment (between deliveries).
    These are scene-setting, reflective, or transitional lines.
    state can be None for pre-match narratives (first innings start/end).
    """
    client = _get_client()
    user_prompt = build_narrative_prompt(moment_type, state, language=language, **kwargs)
    system_prompt = get_narrative_system_prompt(language)
    max_tokens = _max_tokens(_NARRATIVE_TOKENS_EN, _NARRATIVE_TOKENS_INDIC, language)

    if not user_prompt:
        logger.warning(f"No narrative template for moment: {moment_type}")
        return ""

    try:
        response = await client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.9,
            max_completion_tokens=max_tokens,
        )
        commentary = response.choices[0].message.content.strip()
        commentary = commentary.strip('"').strip("'")
        return commentary

    except Exception as e:
        logger.error(f"Narrative generation failed: {e}")
        return _fallback_narrative(moment_type, state, **kwargs)


def _fallback_commentary(ball: BallEvent, logic_result: LogicResult) -> str:
    """Generate basic fallback commentary when the API fails."""
    if ball.is_wicket:
        return f"Wicket! {ball.dismissal_batter or ball.batter} is out, {ball.wicket_type}."
    if ball.is_six:
        return f"{ball.batter} smashes it for six!"
    if ball.is_boundary:
        return f"{ball.batter} finds the boundary for four."
    if ball.runs == 0 and ball.extras == 0:
        return f"Dot ball from {ball.bowler}. Good delivery."
    return f"{ball.batter} picks up {ball.runs + ball.extras} run(s)."


def _fallback_narrative(moment_type: str, state: MatchState | None, **kwargs) -> str:
    """Generate basic fallback narrative when the API fails."""
    bt = state.batting_team if state else kwargs.get("batting_team", "")
    bwt = state.bowling_team if state else kwargs.get("bowling_team", "")

    if moment_type == "first_innings_start":
        return f"Welcome! {bt} vs {bwt}. The match is about to begin."
    if moment_type == "first_innings_end":
        ft = kwargs.get("first_batting_team", bt)
        fr = kwargs.get("first_innings_runs", "")
        fw = kwargs.get("first_innings_wickets", "")
        return f"{ft} finish at {fr}/{fw}. The first innings is done."
    if moment_type == "second_innings_start":
        target = state.target if state else kwargs.get("target", "")
        return f"{bt} need {target} to win. The chase is on!"
    if moment_type == "match_result":
        return kwargs.get("result_text", "And that's the match!")
    if moment_type == "end_of_over":
        if state:
            return (
                f"End of over {state.overs_completed}. "
                f"{bt} {state.total_runs}/{state.wickets}."
            )
        return "End of the over."
    if moment_type == "new_batter":
        name = kwargs.get("new_batter", "The new batter")
        return f"{name} walks out to the middle."
    if moment_type == "phase_change":
        phase = state.match_phase if state else ""
        return f"{phase.title()} overs begin."
    if moment_type == "milestone":
        name = kwargs.get("batter_name", "The batter")
        mtype = kwargs.get("milestone_type", "milestone")
        return f"{mtype} for {name}!"
    return ""
