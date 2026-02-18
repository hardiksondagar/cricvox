"""
Commentary generation engine.

Decoupled from the web server — reads balls from DB, generates LLM
commentary text for each configured language, writes results to
match_commentaries.

Audio (TTS) generation is a **separate step** — see generate_match_audio()
and generate_commentary_audio().  This separation allows callers to:
  1. Generate all commentary text first (fast, LLM-only).
  2. Generate audio later, incrementally, or skip it entirely.

Usage (text only):
    python -m app.generate <match_id> [start_over]

Usage (audio for existing text):
    python -m app.generate --audio <match_id> [language]
"""

import asyncio
import logging
import sys

from app.models import BallEvent, LogicResult, MatchState, NarrativeBranch, SUPPORTED_LANGUAGES
from app.engine.state_manager import StateManager
from app.engine.logic_engine import LogicEngine
from app.commentary.generator import generate_commentary, generate_narrative
from app.commentary.prompts import NARRATIVE_PROMPTS
from app.commentary.prompts import strip_audio_tags
from app.audio.tts import synthesize_speech
from app.storage.database import (
    init_db, close_db, get_match, get_deliveries, update_match_status,
    insert_commentary,
    get_commentaries_pending_audio, get_commentary_by_id,
    update_commentary_audio, get_delivery_by_id, get_max_seq,
    get_recent_commentary_texts, row_to_delivery_event,
    get_deliveries_by_overs, get_commentaries_pending_audio_by_ball_ids,
    get_skeleton_to_update, update_commentary_text,
    mark_skeleton_generated, mark_event_skeleton_generated,
    get_commentaries_by_ball_id,
)
from app.storage.audio import save_audio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Per-ball text generation (no TTS)
# ------------------------------------------------------------------ #

async def _generate_one_lang(
    match_id: int,
    ball_id: int | None,
    seq: int,
    event_type: str,
    text: str,
    branch: NarrativeBranch,
    is_pivot: bool,
    language: str,
    extra_data: dict,
    include_generated: bool = False,
):
    """
    Generate commentary text. Updates existing skeleton (is_generated=0) if found,
    otherwise inserts new row. Does not delete existing commentaries.
    Saves raw text (with ElevenLabs audio tags) so TTS gets emotion cues.
    Returns stripped text for commentary history / display.
    Set include_generated=True to also update already-generated rows.
    """
    skeleton_id = await get_skeleton_to_update(
        match_id, ball_id, event_type, language, include_generated=include_generated
    )
    if skeleton_id:
        await update_commentary_text(
            skeleton_id, text, extra_data, language, clear_audio=include_generated
        )
    else:
        # Guard against duplicates: if a generated row already exists, skip the insert.
        # This prevents re-runs (without force_regenerate) from creating duplicate rows.
        if not include_generated:
            existing_id = await get_skeleton_to_update(
                match_id, ball_id, event_type, language, include_generated=True
            )
            if existing_id:
                return strip_audio_tags(text)
        await insert_commentary(
            match_id=match_id,
            ball_id=ball_id,
            seq=seq,
            event_type=event_type,
            language=language,
            text=text,
            audio_url=None,
            data=extra_data,
            is_generated=True,
        )
    return strip_audio_tags(text)


async def _generate_commentary_all_langs(
    match_id: int,
    ball_id: int | None,
    seq: int,
    state,
    ball: BallEvent,
    logic_result,
    languages: list[str],
    is_narrative: bool = False,
    narrative_type: str | None = None,
    extra_data: dict | None = None,
    force_regenerate: bool = False,
):
    """Generate ball commentary text + TTS for all languages in parallel, insert rows."""
    branch = logic_result.branch if not is_narrative else NarrativeBranch.OVER_TRANSITION
    is_pivot = logic_result.is_pivot if not is_narrative else False

    data = {
        "branch": branch.value,
        "is_pivot": is_pivot,
        "is_narrative": is_narrative,
    }
    if narrative_type:
        data["narrative_type"] = narrative_type
    if extra_data:
        data.update(extra_data)

    # Process sequentially so skeleton (language=NULL) is claimed by first language only
    results = []
    for lang in languages:
        try:
            text = await generate_commentary(state, ball, logic_result, language=lang)
        except Exception as e:
            logger.error(f"Commentary generation failed ({lang}): {e}")
            text = f"{ball.batter} — {ball.runs} run(s)."
        display = await _generate_one_lang(
            match_id, ball_id, seq, "delivery", text, branch, is_pivot, lang, data,
            include_generated=force_regenerate,
        )
        results.append(display)
    return results[0] if results else ""  # return first lang text for commentary history


async def _generate_narrative_all_langs(
    match_id: int,
    ball_id: int | None,
    seq: int,
    moment_type: str,
    state,
    languages: list[str],
    branch: NarrativeBranch = NarrativeBranch.OVER_TRANSITION,
    force_regenerate: bool = False,
    **kwargs,
):
    """Generate a narrative moment for all languages in parallel."""
    data = {
        "branch": branch.value,
        "is_pivot": False,
        "is_narrative": True,
        "narrative_type": moment_type,
    }

    # Process sequentially so skeleton (language=NULL) is claimed by first language only
    results = []
    for lang in languages:
        try:
            text = await generate_narrative(moment_type, state, language=lang, **kwargs)
        except Exception as e:
            logger.error(f"Narrative generation failed ({moment_type}, {lang}): {e}")
            continue
        if not text:
            continue
        display = await _generate_one_lang(
            match_id, ball_id, seq, moment_type, text, branch, False, lang, data,
            include_generated=force_regenerate,
        )
        results.append(display)
    # Return first non-None for commentary history
    return results[0] if results else ""


# ------------------------------------------------------------------ #
#  Main generation runner
# ------------------------------------------------------------------ #

async def generate_match(match_id: int, start_over: int = 1, force_regenerate: bool = False):
    """
    Generate all commentary for a match. Reads balls from DB, writes commentaries.

    If balls have pre-computed context (match_balls.context), uses that directly.
    Otherwise falls back to computing state/logic on the fly.

    Args:
        match_id:         Integer match ID (must exist in DB with balls loaded).
        start_over:       1-indexed over to start commentary from.
        force_regenerate: If True, re-generate even when commentary already exists.
    """
    match = await get_match(match_id)
    if not match:
        logger.error(f"Match {match_id} not found")
        return

    match_info = match["match_info"]
    languages = match["languages"]
    if not languages:
        languages = ["hi"]

    # Validate languages
    languages = [lang for lang in languages if lang in SUPPORTED_LANGUAGES]
    if not languages:
        logger.error("No valid languages configured")
        return

    # Load balls from DB
    ball_rows = await get_deliveries(match_id, innings=2)
    if not ball_rows:
        logger.error(f"No balls found for match {match_id} innings 2")
        return

    # Check if pre-computed context is available
    has_precomputed = ball_rows[0].get("context") is not None

    await update_match_status(match_id, "generating")

    lang_names = ", ".join(SUPPORTED_LANGUAGES.get(lang, {}).get("name", lang) for lang in languages)
    logger.info(
        f"Generating commentary for match {match_id}: "
        f"{match_info.get('batting_team', '')} vs {match_info.get('bowling_team', '')}, "
        f"start_over={start_over}, languages=[{lang_names}], "
        f"precomputed={'yes' if has_precomputed else 'no'}"
    )

    first_innings = match_info.get("first_innings", {})

    # Build state manager + logic engine (needed for fallback or warmup)
    state_mgr = StateManager(
        batting_team=match_info.get("batting_team", ""),
        bowling_team=match_info.get("bowling_team", ""),
        target=match_info.get("target", 0),
    )
    logic_engine = LogicEngine()

    # Convert to BallEvent objects + context
    all_balls = []
    for br in ball_rows:
        all_balls.append((br["id"], row_to_delivery_event(br), br.get("context")))

    start_over_0 = max(start_over - 1, 0)
    warmup = [(bid, b, ctx) for bid, b, ctx in all_balls if b.over < start_over_0]
    live = [(bid, b, ctx) for bid, b, ctx in all_balls if b.over >= start_over_0]

    # Fast-forward: replay warmup balls through StateManager
    if warmup:
        logger.info(f"Fast-forwarding {len(warmup)} balls to build state...")
        for _, ball, _ in warmup:
            state_mgr.update(ball)
    state = state_mgr.get_state()
    if warmup:
        logger.info(f"Fast-forward: {state.total_runs}/{state.wickets} after {state.overs_display}")

    # Commentary history — maintained at runtime (not pre-computable)
    commentary_history: list[str] = []
    seq = await get_max_seq(match_id)

    # ============================================================ #
    #  first_innings_start event — mark skeleton as generated (if exists)
    # ============================================================ #
    await mark_event_skeleton_generated(match_id, "first_innings_start")

    # ============================================================ #
    #  Pre-second-innings narratives (generate_match only loads innings 2)
    # ============================================================ #
    if start_over <= 1:
        inn1_balls = await get_deliveries(match_id, innings=1)
        last_inn1_id = inn1_balls[-1]["id"] if inn1_balls else None
        first_inn2_id = live[0][0] if live else None
        # Innings break + second innings start — NOT first_innings_start (that’s for match start)
        if first_innings:
            seq += 1
            await _generate_narrative_all_langs(
                match_id, last_inn1_id, seq, "first_innings_end", None, languages,
                force_regenerate=force_regenerate,
                first_batting_team=first_innings.get("batting_team", ""),
                first_innings_runs=first_innings.get("total_runs", 0),
                first_innings_wickets=first_innings.get("total_wickets", 0),
                top_scorers=first_innings.get("top_scorers", "N/A"),
                top_bowlers=first_innings.get("top_bowlers", "N/A"),
                first_innings_fours=first_innings.get("total_fours", 0),
                first_innings_sixes=first_innings.get("total_sixes", 0),
                first_innings_extras=first_innings.get("total_extras", 0),
            )
            await mark_event_skeleton_generated(match_id, "first_innings_end", last_inn1_id)

        seq += 1
        await _generate_narrative_all_langs(
            match_id, first_inn2_id, seq, "second_innings_start", state, languages,
            force_regenerate=force_regenerate,
            first_batting_team=first_innings.get("batting_team", ""),
            first_innings_runs=first_innings.get("total_runs", 0),
            first_innings_wickets=first_innings.get("total_wickets", 0),
            venue=match_info.get("venue", ""),
            match_title=match_info.get("title", ""),
        )
        await mark_event_skeleton_generated(match_id, "second_innings_start", first_inn2_id)
    # ============================================================ #
    #  Ball-by-ball loop
    # ============================================================ #
    for ball_db_id, ball, precomputed_ctx in live:

        # Always replay through StateManager for accurate state
        state = state_mgr.update(ball)

        if precomputed_ctx:
            # Use pre-computed logic + narratives (avoids re-running LogicEngine)
            logic_result = LogicResult.model_validate(precomputed_ctx["logic"])
            match_over = precomputed_ctx["match_over"]
            narrative_triggers = precomputed_ctx.get("narratives", [])
        else:
            # Fallback: compute logic on the fly
            logic_result = logic_engine.analyze(state, ball)
            match_over = (
                state.runs_needed <= 0
                or state.wickets >= 10
                or state.balls_remaining <= 0
            )
            narrative_triggers = None  # handled inline below

        # Inject runtime commentary history into state
        state.commentary_history = list(commentary_history)

        # 2. Ball commentary (one row per language)
        seq += 1
        display_text = await _generate_commentary_all_langs(
            match_id, ball_db_id, seq, state, ball, logic_result, languages,
            force_regenerate=force_regenerate,
        )

        # 3. Mark the skeleton 'ball' row as generated
        await mark_skeleton_generated(match_id, ball_db_id)

        # 4. Update commentary history
        if display_text:
            commentary_history.append(display_text)
            if len(commentary_history) > 6:
                commentary_history.pop(0)

        logger.info(
            f"[{state.overs_display}] {ball.batter}: {ball.runs}"
            f"{'W' if ball.is_wicket else ''} "
            f"| {state.total_runs}/{state.wickets} | {logic_result.branch.value}"
        )

        # ============================================================ #
        #  Post-ball narratives (from pre-computed triggers or inline)
        # ============================================================ #
        if narrative_triggers is not None:
            # Use pre-computed narrative triggers
            for narr in narrative_triggers:
                ntype = narr["type"]
                nbranch = NarrativeBranch(narr.get("branch", "over_transition"))
                nkwargs = narr.get("kwargs", {})

                if ntype == "second_innings_end":
                    seq += 1
                    await _generate_narrative_all_langs(
                        match_id, ball_db_id, seq, "second_innings_end", state, languages,
                        force_regenerate=force_regenerate, branch=nbranch, **nkwargs,
                    )
                else:
                    seq += 1
                    text = await _generate_narrative_all_langs(
                        match_id, ball_db_id, seq, ntype, state, languages,
                        force_regenerate=force_regenerate, branch=nbranch, **nkwargs,
                    )
                    if text:
                        commentary_history.append(text)
                        if len(commentary_history) > 6:
                            commentary_history.pop(0)

            if match_over:
                break

        else:
            # Fallback: inline narrative detection (original logic)
            _inline_post_ball_narratives_result = await _inline_post_ball_narratives(
                match_id, ball_db_id, ball, state, languages,
                commentary_history, first_innings, match_over, seq,
                force_regenerate=force_regenerate,
            )
            seq = _inline_post_ball_narratives_result["seq"]
            commentary_history = _inline_post_ball_narratives_result["commentary_history"]
            if match_over:
                break

    await update_match_status(match_id, "generated")
    logger.info(f"Match {match_id} generation complete ({seq} events)")


async def _inline_post_ball_narratives(
    match_id: int,
    ball_db_id: int,
    ball: BallEvent,
    state: MatchState,
    languages: list[str],
    commentary_history: list[str],
    first_innings: dict,
    match_over: bool,
    seq: int,
    force_regenerate: bool = False,
) -> dict:
    """
    Fallback: detect and generate narrative moments inline (when pre-computed
    context is not available). Returns updated seq and commentary_history.
    """
    # We need to track previous phase/overs across calls — use state attributes
    # This is a simplified version; for production, these should be tracked properly
    previous_phase = getattr(state, "_prev_phase", state.match_phase)
    previous_overs_completed = getattr(state, "_prev_overs", state.overs_completed)

    # --- MILESTONE ---
    batter_name = ball.batter
    if not match_over and batter_name in state.batters:
        batter = state.batters[batter_name]
        milestone_type = None
        if batter.just_reached_fifty and batter.runs < 100:
            milestone_type = "FIFTY"
        elif batter.just_reached_hundred:
            milestone_type = "HUNDRED"
        if milestone_type:
            seq += 1
            text = await _generate_narrative_all_langs(
                match_id, ball_db_id, seq, "milestone", state, languages,
                force_regenerate=force_regenerate,
                branch=NarrativeBranch.BOUNDARY_MOMENTUM,
                milestone_type=milestone_type,
                batter_name=batter_name,
                batter_runs=batter.runs,
                batter_balls=batter.balls_faced,
                batter_fours=batter.fours,
                batter_sixes=batter.sixes,
                batter_sr=batter.strike_rate,
                situation=f"Need {state.runs_needed} from {state.balls_remaining} balls",
            )
            if text:
                commentary_history.append(text)
                if len(commentary_history) > 6:
                    commentary_history.pop(0)

    # --- NEW BATTER ---
    if not match_over and ball.is_wicket and state.wickets < 10:
        dismissed = ball.dismissal_batter or ball.batter
        partnership_broken = ""
        last_fow = state.fall_of_wickets[-1] if state.fall_of_wickets else None
        if last_fow:
            partnership_broken = (
                f"{dismissed} out for {last_fow.batter_runs} at "
                f"{last_fow.team_score}/{last_fow.wicket_number}"
            )
        situation = f"Need {state.runs_needed} from {state.balls_remaining} balls"
        if state.is_collapse:
            situation += " — collapse in progress!"
        seq += 1
        text = await _generate_narrative_all_langs(
            match_id, ball_db_id, seq, "new_batter", state, languages,
            force_regenerate=force_regenerate,
            branch=NarrativeBranch.WICKET_DRAMA,
            new_batter="",
            position=state.wickets + 1,
            partnership_broken=partnership_broken,
            situation=situation,
        )
        if text:
            commentary_history.append(text)
            if len(commentary_history) > 6:
                commentary_history.pop(0)

    # --- END OF OVER / PHASE CHANGE ---
    if not match_over and state.overs_completed > previous_overs_completed:
        bowler_name = ball.bowler
        bowler_figures = ""
        if bowler_name in state.bowlers:
            bowler_figures = state.bowlers[bowler_name].figures_str
        over_runs = state.over_runs_history[-1] if state.over_runs_history else 0

        current_phase = state.match_phase
        phase_changed = current_phase != previous_phase

        seq += 1
        if phase_changed:
            phase_summary = ""
            if previous_phase == "powerplay":
                phase_summary = f"Powerplay done: {state.powerplay_runs} runs, {state.wickets} wickets"
            elif previous_phase == "middle":
                phase_summary = f"Middle overs done: {state.middle_overs_runs} runs scored"
            await _generate_narrative_all_langs(
                match_id, ball_db_id, seq, "phase_change", state, languages,
                force_regenerate=force_regenerate,
                new_phase=current_phase.title() + " Overs",
                phase_summary=phase_summary,
            )
        else:
            completed_over_num = state.overs_completed - 1
            over_wickets = sum(
                1 for f in state.fall_of_wickets
                if f.overs.startswith(f"{completed_over_num}.")
            )
            await _generate_narrative_all_langs(
                match_id, ball_db_id, seq, "end_of_over", state, languages,
                force_regenerate=force_regenerate,
                over_runs=over_runs,
                over_wickets=over_wickets,
                bowler=bowler_name,
                bowler_figures=bowler_figures,
                phase_info=f"Phase: {state.match_phase}",
            )

    # --- Match result ---
    if match_over:
        if state.runs_needed <= 0:
            wickets_in_hand = 10 - state.wickets
            result_text = (
                f"{state.batting_team} WIN by {wickets_in_hand} wicket(s)! "
                f"They chased down {state.target} with "
                f"{state.balls_remaining} balls to spare."
            )
        elif state.wickets >= 10:
            result_text = (
                f"{state.bowling_team} WIN by {state.runs_needed} runs! "
                f"{state.batting_team} all out for {state.total_runs}."
            )
        else:
            result_text = (
                f"{state.bowling_team} WIN by {state.runs_needed} runs! "
                f"{state.batting_team} could only manage "
                f"{state.total_runs}/{state.wickets} in {state.overs_display} overs."
            )

        highlights = []
        if state.batters:
            top_bat = max(state.batters.values(), key=lambda b: b.runs)
            if top_bat.runs >= 15:
                highlights.append(f"Top scorer: {top_bat.name} {top_bat.runs}({top_bat.balls_faced})")
        if state.bowlers:
            top_bowl = max(state.bowlers.values(), key=lambda b: b.wickets)
            if top_bowl.wickets > 0:
                highlights.append(f"Best bowler: {top_bowl.name} {top_bowl.figures_str}")
        if first_innings:
            highlights.append(
                f"First innings: {first_innings.get('batting_team', '')} "
                f"{first_innings.get('total_runs', 0)}/{first_innings.get('total_wickets', 0)}"
            )

        seq += 1
        await _generate_narrative_all_langs(
            match_id, ball_db_id, seq, "second_innings_end", state, languages,
            force_regenerate=force_regenerate,
            branch=NarrativeBranch.WICKET_DRAMA,
            result_text=result_text,
            match_highlights="\n".join(highlights) if highlights else "",
        )

    return {"seq": seq, "commentary_history": commentary_history}


def _narrative_kwargs_for_event(
    event_type: str,
    row_data: dict,
    match_info: dict,
    first_innings: dict,
    state,
) -> dict:
    """Build kwargs for generate_narrative from row data, match_info, and state."""
    kwargs = dict(row_data) if row_data else {}
    first_innings = first_innings or {}

    # Common from match_info
    kwargs.setdefault("match_title", match_info.get("title", match_info.get("match_title", "")))
    kwargs.setdefault("venue", match_info.get("venue", ""))
    kwargs.setdefault("match_format", match_info.get("format", "T20"))
    kwargs.setdefault("team1", match_info.get("team1", first_innings.get("batting_team", "")))
    kwargs.setdefault("team2", match_info.get("team2", first_innings.get("bowling_team", "")))

    # first_innings_start: who bats first only — NO first innings results (we're before any ball)
    first_batting = first_innings.get("batting_team", match_info.get("batting_team", ""))
    first_bowling = first_innings.get("bowling_team", match_info.get("bowling_team", ""))
    kwargs.setdefault("first_batting_team", first_batting)
    kwargs.setdefault("first_bowling_team", first_bowling)
    if event_type == "first_innings_start":
        kwargs.setdefault("first_innings_context", "")
    else:
        ctx_parts = []
        if first_innings.get("total_runs") is not None:
            ctx_parts.append(
                f"{first_batting} posted {first_innings.get('total_runs', 0)}/{first_innings.get('total_wickets', 0)}."
            )
        if first_innings.get("top_scorers"):
            ctx_parts.append(f"Top scorers: {first_innings.get('top_scorers', 'N/A')}.")
        if first_innings.get("top_bowlers"):
            ctx_parts.append(f"Top bowlers: {first_innings.get('top_bowlers', 'N/A')}.")
        kwargs.setdefault("first_innings_context", " ".join(ctx_parts) if ctx_parts else "")

    # From first_innings (other narrative types)
    kwargs.setdefault("first_innings_runs", first_innings.get("total_runs", 0))
    kwargs.setdefault("first_innings_wickets", first_innings.get("total_wickets", 0))
    kwargs.setdefault("top_scorers", first_innings.get("top_scorers", "N/A"))
    kwargs.setdefault("top_bowlers", first_innings.get("top_bowlers", "N/A"))
    kwargs.setdefault("first_innings_fours", first_innings.get("total_fours", 0))
    kwargs.setdefault("first_innings_sixes", first_innings.get("total_sixes", 0))
    kwargs.setdefault("first_innings_extras", first_innings.get("total_extras", 0))

    # From state
    if state:
        kwargs.setdefault("batting_team", state.batting_team)
        kwargs.setdefault("bowling_team", state.bowling_team)
        kwargs.setdefault("runs", state.total_runs)
        kwargs.setdefault("wickets", state.wickets)
        kwargs.setdefault("overs", state.overs_display)
        kwargs.setdefault("overs_completed", state.overs_completed)
        kwargs.setdefault("target", state.target)
        kwargs.setdefault("crr", state.crr)
        kwargs.setdefault("rrr", state.rrr)
        kwargs.setdefault("runs_needed", state.runs_needed)
        kwargs.setdefault("balls_remaining", state.balls_remaining)

    return kwargs


# ================================================================== #
#  Single-ball generation
# ================================================================== #

async def generate_ball_commentary(
    match_id: int,
    ball_id: int,
    languages: list[str] | None = None,
    force_regenerate: bool = False,
) -> dict:
    """
    Generate LLM commentary for a single ball delivery.

    Requires the ball to have pre-computed context (run precompute first).
    Fetches recent commentary from DB for history injection.

    Returns dict with status, seq range, and generated commentary IDs.
    """
    ball_row = await get_delivery_by_id(ball_id)
    if not ball_row:
        return {"status": "error", "message": f"Ball {ball_id} not found"}

    if ball_row["match_id"] != match_id:
        return {"status": "error", "message": "Ball does not belong to this match"}

    ctx = ball_row.get("context")
    if not ctx:
        return {"status": "error", "message": "Ball has no pre-computed context. Run precompute first."}

    match = await get_match(match_id)
    if not match:
        return {"status": "error", "message": "Match not found"}

    # Resolve languages
    if not languages:
        languages = match.get("languages", ["hi"])
    languages = [lang for lang in languages if lang in SUPPORTED_LANGUAGES]
    if not languages:
        return {"status": "error", "message": "No valid languages"}

    match_info = match["match_info"]
    innings_num = ball_row["innings"]
    innings_summaries = match_info.get("innings_summary", [])
    inn_meta = next(
        (s for s in innings_summaries if s.get("innings_number") == innings_num), {},
    )
    batting_team = inn_meta.get("batting_team", match_info.get("batting_team", ""))
    bowling_team = inn_meta.get("bowling_team", match_info.get("bowling_team", ""))
    target = match_info.get("target", 0) if innings_num == 2 else 0

    # Replay all balls up to (and including) this one through StateManager
    all_balls = await get_deliveries(match_id, innings=innings_num)
    state_mgr = StateManager(
        batting_team=batting_team,
        bowling_team=bowling_team,
        target=target,
    )
    for br in all_balls:
        state_mgr.update(row_to_delivery_event(br))
        if br["id"] == ball_id:
            break
    state = state_mgr.get_state()
    ball = row_to_delivery_event(ball_row)

    # Unpack pre-computed context (logic + narratives only)
    logic_result = LogicResult.model_validate(ctx["logic"])
    match_over = ctx["match_over"]

    # Get commentary history from DB (last 6 texts in the first language)
    history = await get_recent_commentary_texts(match_id, languages[0], limit=6)
    state.commentary_history = history

    # Query all commentary rows for this ball_id and generate LLM text for each skeleton
    commentaries = await get_commentaries_by_ball_id(match_id, ball_id)
    first_innings = match_info.get("first_innings", {})
    start_seq = (await get_max_seq(match_id)) + 1
    display_text = ""
    narratives_updated = 0

    for row in commentaries:
        if row["is_generated"] and not force_regenerate:
            continue
        event_type = row["event_type"]
        lang = row.get("language")
        if not lang:
            continue  # Skip rows with no language (shouldn't happen for skeletons)
        if lang not in languages:
            continue

        try:
            if event_type == "delivery":
                text = await generate_commentary(state, ball, logic_result, language=lang)
                data = {
                    "branch": logic_result.branch.value,
                    "is_pivot": logic_result.is_pivot,
                    "is_narrative": False,
                }
                if not display_text:
                    display_text = strip_audio_tags(text)
            elif event_type in NARRATIVE_PROMPTS:
                kwargs = _narrative_kwargs_for_event(
                    event_type, row["data"], match_info, first_innings, state,
                )
                text = await generate_narrative(
                    event_type, state, language=lang, **kwargs
                )
                if not text:
                    continue
                data = {
                    "branch": NarrativeBranch.OVER_TRANSITION.value,
                    "is_pivot": False,
                    "is_narrative": True,
                    "narrative_type": event_type,
                }
                narratives_updated += 1
            else:
                continue  # second_innings_end etc. use precomputed, skip LLM

            # Save raw text (with audio tags) so TTS receives emotion cues
            await update_commentary_text(
                row["id"], text, data, lang, clear_audio=force_regenerate
            )
            if state.commentary_history is not None:
                state.commentary_history = list(state.commentary_history)[-5:]
                state.commentary_history.append(strip_audio_tags(text))
        except Exception as e:
            logger.error(f"Generation failed for {event_type} ({lang}): {e}")

    seq = await get_max_seq(match_id)
    return {
        "status": "ok",
        "match_id": match_id,
        "ball_id": ball_id,
        "commentary_text": display_text,
        "seq_start": start_seq,
        "seq_end": seq,
        "match_over": match_over,
        "narratives_generated": narratives_updated,
    }


# ================================================================== #
#  Overs-based generation
# ================================================================== #

async def generate_overs_commentary(
    match_id: int,
    innings: int,
    overs_0indexed: list[int],
    force_regenerate: bool = False,
) -> dict:
    """
    Generate LLM commentary for all deliveries in specific overs of an innings.

    Iterates over each delivery in the requested overs and generates
    commentary using generate_ball_commentary (which handles full state
    replay, narratives, etc.).

    Args:
        match_id:       Integer match ID.
        innings:        Innings number (1 or 2).
        overs_0indexed: List of 0-indexed over numbers to generate for.

    Returns dict with status, counts of generated/errored deliveries.
    """
    match = await get_match(match_id)
    if not match:
        logger.error(f"Match {match_id} not found")
        return {"status": "error", "message": "Match not found"}

    languages = match.get("languages", ["hi"])
    languages = [lang for lang in languages if lang in SUPPORTED_LANGUAGES]
    if not languages:
        return {"status": "error", "message": "No valid languages"}

    deliveries = await get_deliveries_by_overs(match_id, innings, overs_0indexed)
    if not deliveries:
        return {
            "status": "error",
            "message": f"No deliveries found for innings {innings} overs {[o + 1 for o in overs_0indexed]}",
        }

    logger.info(
        f"Generating commentary for {len(deliveries)} deliveries "
        f"in overs {[o + 1 for o in overs_0indexed]} (match {match_id})"
    )

    results = []
    for delivery in deliveries:
        result = await generate_ball_commentary(
            match_id=match_id,
            ball_id=delivery["id"],
            languages=languages,
            force_regenerate=force_regenerate,
        )
        results.append(result)
        status = result.get("status", "unknown")
        logger.info(f"  Ball {delivery['id']} (over {delivery['over']}.{delivery['ball']}): {status}")

    generated = sum(1 for r in results if r.get("status") == "ok")
    errors = sum(1 for r in results if r.get("status") == "error")

    logger.info(
        f"Overs generation complete for match {match_id}: "
        f"{generated} generated, {errors} errors"
    )

    return {
        "status": "ok",
        "match_id": match_id,
        "overs": [o + 1 for o in overs_0indexed],
        "total_deliveries": len(deliveries),
        "generated": generated,
        "errors": errors,
    }


# ================================================================== #
#  Audio generation (separate from text)
# ================================================================== #

async def generate_commentary_audio(commentary_id: int, regenerate: bool = False) -> dict:
    """
    Generate TTS audio for a single commentary row.

    Reads the commentary from DB, generates audio via TTS, saves the file,
    and updates the DB row with the audio_url.

    When regenerate=True, re-generates audio even if audio_url already exists.
    The old URL is only replaced once the new file is saved (no null gap).

    Returns a dict with commentary_id, audio_url (or None on failure),
    and status ("generated", "skipped", or "failed").
    """
    row = await get_commentary_by_id(commentary_id)
    if not row:
        return {"commentary_id": commentary_id, "status": "not_found", "audio_url": None}

    if row["audio_url"] and not regenerate:
        return {"commentary_id": commentary_id, "status": "already_exists", "audio_url": row["audio_url"]}

    if not row["text"] or not row["language"]:
        return {"commentary_id": commentary_id, "status": "skipped", "audio_url": None}

    data = row.get("data", {})
    branch = NarrativeBranch(data.get("branch", "routine"))
    is_pivot = data.get("is_pivot", False)
    language = row["language"]
    match_id = row["match_id"]

    try:
        audio_bytes = await synthesize_speech(
            row["text"], branch, is_pivot, language=language,
        )
        if audio_bytes:
            audio_url = save_audio(match_id, row["text"], language, audio_bytes)
            await update_commentary_audio(commentary_id, audio_url)
            return {"commentary_id": commentary_id, "status": "generated", "audio_url": audio_url}
        else:
            return {"commentary_id": commentary_id, "status": "failed", "audio_url": None}
    except Exception as e:
        logger.error(f"TTS failed for commentary {commentary_id} ({language}): {e}")
        return {"commentary_id": commentary_id, "status": "failed", "audio_url": None, "error": str(e)}


async def generate_match_audio(
    match_id: int,
    language: str | None = None,
    regenerate: bool = False,
) -> dict:
    """
    Generate TTS audio for all commentaries in a match that don't have audio yet.

    Args:
        match_id:    Integer match ID.
        language:    Optional language filter. If None, processes all languages.
        regenerate:  If True, re-generate audio even for rows that already have it.

    Returns a summary dict with total, generated, skipped, failed counts.
    """
    match = await get_match(match_id)
    if not match:
        return {"match_id": match_id, "error": "Match not found"}

    pending = await get_commentaries_pending_audio(
        match_id, language=language, include_existing=regenerate
    )

    if not pending:
        return {
            "match_id": match_id,
            "language": language,
            "total": 0,
            "generated": 0,
            "skipped": 0,
            "failed": 0,
            "message": "No commentaries pending audio generation",
        }

    logger.info(
        f"Generating audio for match {match_id}: "
        f"{len(pending)} commentaries pending"
        f"{f' (language={language})' if language else ''}"
    )

    results = await asyncio.gather(
        *(generate_commentary_audio(row["id"], regenerate=regenerate) for row in pending)
    )

    generated = sum(1 for r in results if r["status"] == "generated")
    failed = sum(1 for r in results if r["status"] == "failed")
    skipped = len(results) - generated - failed

    logger.info(
        f"Audio generation complete for match {match_id}: "
        f"{generated} generated, {failed} failed, {skipped} skipped"
    )

    return {
        "match_id": match_id,
        "language": language,
        "total": len(pending),
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
    }


async def generate_overs_audio(
    match_id: int,
    innings: int,
    overs_0indexed: list[int],
    language: str | None = None,
    regenerate: bool = False,
) -> dict:
    """
    Generate TTS audio for commentaries belonging to deliveries in specific overs.

    Only processes commentaries that have text but no audio yet,
    unless regenerate=True which re-generates all.

    Args:
        match_id:       Integer match ID.
        innings:        Innings number (1 or 2).
        overs_0indexed: List of 0-indexed over numbers.
        language:       Optional language filter.
        regenerate:     If True, re-generate audio even for rows that already have it.

    Returns summary dict with total, generated, skipped, failed counts.
    """
    deliveries = await get_deliveries_by_overs(match_id, innings, overs_0indexed)
    if not deliveries:
        return {
            "match_id": match_id,
            "total": 0,
            "generated": 0,
            "skipped": 0,
            "failed": 0,
            "message": f"No deliveries found for innings {innings} overs {[o + 1 for o in overs_0indexed]}",
        }

    ball_ids = [d["id"] for d in deliveries]
    pending = await get_commentaries_pending_audio_by_ball_ids(
        match_id, ball_ids, language=language, include_existing=regenerate
    )

    if not pending:
        return {
            "match_id": match_id,
            "overs": [o + 1 for o in overs_0indexed],
            "total": 0,
            "generated": 0,
            "skipped": 0,
            "failed": 0,
            "message": "No commentaries pending audio generation for these overs",
        }

    logger.info(
        f"Generating audio for {len(pending)} commentaries "
        f"in overs {[o + 1 for o in overs_0indexed]} (match {match_id})"
    )

    results = await asyncio.gather(
        *(generate_commentary_audio(row["id"], regenerate=regenerate) for row in pending)
    )

    generated = sum(1 for r in results if r["status"] == "generated")
    failed = sum(1 for r in results if r["status"] == "failed")
    skipped = len(results) - generated - failed

    logger.info(
        f"Overs audio complete for match {match_id}: "
        f"{generated} generated, {failed} failed, {skipped} skipped"
    )

    return {
        "match_id": match_id,
        "overs": [o + 1 for o in overs_0indexed],
        "total": len(pending),
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
    }


async def generate_ball_audio(
    match_id: int,
    ball_id: int,
    regenerate: bool = False,
) -> dict:
    """
    Generate TTS audio for all pending commentaries of a specific delivery.

    Returns summary dict with ball_id, total, generated, failed counts.
    """
    pending = await get_commentaries_pending_audio_by_ball_ids(
        match_id, [ball_id], include_existing=regenerate
    )

    if not pending:
        return {
            "ball_id": ball_id,
            "total": 0,
            "generated": 0,
            "failed": 0,
            "message": "No commentaries pending audio for this delivery",
        }

    results = await asyncio.gather(
        *(generate_commentary_audio(row["id"], regenerate=regenerate) for row in pending)
    )

    generated = sum(1 for r in results if r["status"] == "generated")
    failed = len(results) - generated

    return {
        "ball_id": ball_id,
        "total": len(pending),
        "generated": generated,
        "failed": failed,
    }


# ------------------------------------------------------------------ #
#  CLI entry point
# ------------------------------------------------------------------ #

async def _main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m app.generate <match_id> [start_over]    # generate text")
        print("  python -m app.generate --audio <match_id> [lang]  # generate audio")
        sys.exit(1)

    # Audio generation mode
    if sys.argv[1] == "--audio":
        if len(sys.argv) < 3:
            print("Usage: python -m app.generate --audio <match_id> [language]")
            sys.exit(1)
        match_id = int(sys.argv[2])
        language = sys.argv[3] if len(sys.argv) > 3 else None
        await init_db()
        try:
            result = await generate_match_audio(match_id, language=language)
            print(f"Audio generation result: {result}")
        finally:
            await close_db()
        return

    # Text generation mode (default)
    match_id = int(sys.argv[1])
    start_over = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    await init_db()
    try:
        await generate_match(match_id, start_over)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(_main())
