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
from app.commentary.prompts import strip_audio_tags
from app.audio.tts import synthesize_speech
from app.storage.database import (
    init_db, close_db, get_match, get_deliveries, update_match_status,
    delete_commentaries, insert_commentary,
    get_commentaries_pending_audio, get_commentary_by_id,
    update_commentary_audio, get_delivery_by_id, get_max_seq,
    get_recent_commentary_texts, row_to_delivery_event,
    get_deliveries_by_overs, get_commentaries_pending_audio_by_ball_ids,
    delete_commentaries_by_ball_ids,
)
from app.storage.audio import save_audio, clear_audio

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
):
    """Generate commentary text and insert one DB row (no audio)."""
    display_text = strip_audio_tags(text)

    await insert_commentary(
        match_id=match_id,
        ball_id=ball_id,
        seq=seq,
        event_type=event_type,
        language=language,
        text=display_text,
        audio_url=None,
        data=extra_data,
    )
    return display_text


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

    async def gen_lang(lang: str):
        try:
            text = await generate_commentary(state, ball, logic_result, language=lang)
        except Exception as e:
            logger.error(f"Commentary generation failed ({lang}): {e}")
            text = f"{ball.batter} — {ball.runs} run(s)."
        return await _generate_one_lang(
            match_id, ball_id, seq, "commentary", text, branch, is_pivot, lang, data
        )

    results = await asyncio.gather(*[gen_lang(lang) for lang in languages])
    return results[0] if results else ""  # return first lang text for commentary history


async def _generate_narrative_all_langs(
    match_id: int,
    ball_id: int | None,
    seq: int,
    moment_type: str,
    state,
    languages: list[str],
    branch: NarrativeBranch = NarrativeBranch.OVER_TRANSITION,
    **kwargs,
):
    """Generate a narrative moment for all languages in parallel."""
    data = {
        "branch": branch.value,
        "is_pivot": False,
        "is_narrative": True,
        "narrative_type": moment_type,
    }

    async def gen_lang(lang: str):
        try:
            text = await generate_narrative(moment_type, state, language=lang, **kwargs)
        except Exception as e:
            logger.error(f"Narrative generation failed ({moment_type}, {lang}): {e}")
            return None
        if not text:
            return None
        display = await _generate_one_lang(
            match_id, ball_id, seq, "commentary", text, branch, False, lang, data
        )
        return display

    results = await asyncio.gather(*[gen_lang(lang) for lang in languages])
    # Return first non-None for commentary history
    for r in results:
        if r:
            return r
    return ""


# ------------------------------------------------------------------ #
#  Main generation runner
# ------------------------------------------------------------------ #

async def generate_match(match_id: int, start_over: int = 1):
    """
    Generate all commentary for a match. Reads balls from DB, writes commentaries.

    If balls have pre-computed context (match_balls.context), uses that directly.
    Otherwise falls back to computing state/logic on the fly.

    Args:
        match_id:    Integer match ID (must exist in DB with balls loaded).
        start_over:  1-indexed over to start commentary from.
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

    # Clear old commentaries + audio
    await delete_commentaries(match_id)
    clear_audio(match_id)

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
    seq = 0

    # ============================================================ #
    #  match_start event
    # ============================================================ #
    seq += 1
    await insert_commentary(
        match_id, None, seq, "match_start", None, None, None, match_info,
    )

    # ============================================================ #
    #  Pre-match narratives
    # ============================================================ #
    if start_over <= 1:
        seq += 1
        await _generate_narrative_all_langs(
            match_id, None, seq, "first_innings_start", None, languages,
            match_title=match_info.get("title", ""),
            venue=match_info.get("venue", ""),
            match_format=match_info.get("format", "T20"),
            batting_team=first_innings.get("batting_team", match_info.get("bowling_team", "")),
            bowling_team=first_innings.get("bowling_team", match_info.get("batting_team", "")),
        )

        if first_innings:
            seq += 1
            await _generate_narrative_all_langs(
                match_id, None, seq, "first_innings_end", None, languages,
                first_batting_team=first_innings.get("batting_team", ""),
                first_innings_runs=first_innings.get("total_runs", 0),
                first_innings_wickets=first_innings.get("total_wickets", 0),
                top_scorers=first_innings.get("top_scorers", "N/A"),
                top_bowlers=first_innings.get("top_bowlers", "N/A"),
                first_innings_fours=first_innings.get("total_fours", 0),
                first_innings_sixes=first_innings.get("total_sixes", 0),
                first_innings_extras=first_innings.get("total_extras", 0),
            )

        seq += 1
        await _generate_narrative_all_langs(
            match_id, None, seq, "second_innings_start", state, languages,
            first_batting_team=first_innings.get("batting_team", ""),
            first_innings_runs=first_innings.get("total_runs", 0),
            first_innings_wickets=first_innings.get("total_wickets", 0),
            venue=match_info.get("venue", ""),
            match_title=match_info.get("title", ""),
        )
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
        )

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

                if ntype == "match_result":
                    seq += 1
                    await _generate_narrative_all_langs(
                        match_id, None, seq, "match_result", state, languages,
                        branch=nbranch, **nkwargs,
                    )
                    seq += 1
                    await insert_commentary(
                        match_id, None, seq, "match_end", None, None, None,
                        {
                            "result": "won" if state.runs_needed <= 0 else "lost",
                            "final_score": f"{state.total_runs}/{state.wickets}",
                            "overs": state.overs_display,
                        },
                    )
                else:
                    seq += 1
                    text = await _generate_narrative_all_langs(
                        match_id, ball_db_id, seq, ntype, state, languages,
                        branch=nbranch, **nkwargs,
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
            match_id, None, seq, "match_result", state, languages,
            branch=NarrativeBranch.WICKET_DRAMA,
            result_text=result_text,
            match_highlights="\n".join(highlights) if highlights else "",
        )

        seq += 1
        await insert_commentary(
            match_id, None, seq, "match_end", None, None, None,
            {
                "result": "won" if state.runs_needed <= 0 else "lost",
                "final_score": f"{state.total_runs}/{state.wickets}",
                "overs": state.overs_display,
            },
        )

    return {"seq": seq, "commentary_history": commentary_history}


# ================================================================== #
#  Single-ball generation
# ================================================================== #

async def generate_ball_commentary(
    match_id: int,
    ball_id: int,
    languages: list[str] | None = None,
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
    narrative_triggers = ctx.get("narratives", [])

    # Get commentary history from DB (last 6 texts in the first language)
    history = await get_recent_commentary_texts(match_id, languages[0], limit=6)
    state.commentary_history = history

    # Delete existing commentaries for this ball (idempotent re-generation)
    deleted = await delete_commentaries_by_ball_ids(match_id, [ball_id])
    if deleted:
        logger.info(f"Deleted {deleted} existing commentaries for ball {ball_id}")

    seq = await get_max_seq(match_id)
    start_seq = seq + 1

    # ── Match intro: generate if this is the first ball and no intro exists yet ──
    is_first_ball = all_balls[0]["id"] == ball_id
    if is_first_ball and seq == 0:
        first_innings = match_info.get("first_innings", {})

        # match_start event (language-independent metadata)
        seq += 1
        await insert_commentary(
            match_id, None, seq, "match_start", None, None, None, match_info,
        )

        # first_innings_start narrative
        seq += 1
        await _generate_narrative_all_langs(
            match_id, None, seq, "first_innings_start", None, languages,
            match_title=match_info.get("title", ""),
            venue=match_info.get("venue", ""),
            match_format=match_info.get("format", "T20"),
            batting_team=first_innings.get("batting_team", match_info.get("bowling_team", "")),
            bowling_team=first_innings.get("bowling_team", match_info.get("batting_team", "")),
        )

        # first_innings_end narrative (if first innings data exists)
        if first_innings:
            seq += 1
            await _generate_narrative_all_langs(
                match_id, None, seq, "first_innings_end", None, languages,
                first_batting_team=first_innings.get("batting_team", ""),
                first_innings_runs=first_innings.get("total_runs", 0),
                first_innings_wickets=first_innings.get("total_wickets", 0),
                top_scorers=first_innings.get("top_scorers", "N/A"),
                top_bowlers=first_innings.get("top_bowlers", "N/A"),
                first_innings_fours=first_innings.get("total_fours", 0),
                first_innings_sixes=first_innings.get("total_sixes", 0),
                first_innings_extras=first_innings.get("total_extras", 0),
            )

        # second_innings_start narrative
        seq += 1
        await _generate_narrative_all_langs(
            match_id, None, seq, "second_innings_start", state, languages,
            first_batting_team=first_innings.get("batting_team", ""),
            first_innings_runs=first_innings.get("total_runs", 0),
            first_innings_wickets=first_innings.get("total_wickets", 0),
            venue=match_info.get("venue", ""),
            match_title=match_info.get("title", ""),
        )

        logger.info(f"Generated match intro narratives for match {match_id}")

    # 1. Ball commentary (one row per language)
    seq += 1
    display_text = await _generate_commentary_all_langs(
        match_id, ball_id, seq, state, ball, logic_result, languages,
    )

    # 3. Post-ball narratives
    for narr in narrative_triggers:
        ntype = narr["type"]
        nbranch = NarrativeBranch(narr.get("branch", "over_transition"))
        nkwargs = narr.get("kwargs", {})

        if ntype == "match_result":
            seq += 1
            await _generate_narrative_all_langs(
                match_id, None, seq, "match_result", state, languages,
                branch=nbranch, **nkwargs,
            )
            seq += 1
            await insert_commentary(
                match_id, None, seq, "match_end", None, None, None,
                {
                    "result": "won" if state.runs_needed <= 0 else "lost",
                    "final_score": f"{state.total_runs}/{state.wickets}",
                    "overs": state.overs_display,
                },
            )
        else:
            seq += 1
            await _generate_narrative_all_langs(
                match_id, ball_id, seq, ntype, state, languages,
                branch=nbranch, **nkwargs,
            )

    return {
        "status": "ok",
        "match_id": match_id,
        "ball_id": ball_id,
        "commentary_text": display_text,
        "seq_start": start_seq,
        "seq_end": seq,
        "match_over": match_over,
        "narratives_generated": len(narrative_triggers),
    }


# ================================================================== #
#  Overs-based generation
# ================================================================== #

async def generate_overs_commentary(
    match_id: int,
    innings: int,
    overs_0indexed: list[int],
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

async def generate_commentary_audio(commentary_id: int) -> dict:
    """
    Generate TTS audio for a single commentary row.

    Reads the commentary from DB, generates audio via TTS, saves the file,
    and updates the DB row with the audio_url.

    Returns a dict with commentary_id, audio_url (or None on failure),
    and status ("generated", "skipped", or "failed").
    """
    row = await get_commentary_by_id(commentary_id)
    if not row:
        return {"commentary_id": commentary_id, "status": "not_found", "audio_url": None}

    if row["audio_url"]:
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
) -> dict:
    """
    Generate TTS audio for all commentaries in a match that don't have audio yet.

    Args:
        match_id:  Integer match ID.
        language:  Optional language filter. If None, processes all languages.

    Returns a summary dict with total, generated, skipped, failed counts.
    """
    match = await get_match(match_id)
    if not match:
        return {"match_id": match_id, "error": "Match not found"}

    pending = await get_commentaries_pending_audio(match_id, language=language)

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

    generated = 0
    failed = 0
    skipped = 0

    for row in pending:
        result = await generate_commentary_audio(row["id"])
        if result["status"] == "generated":
            generated += 1
        elif result["status"] == "failed":
            failed += 1
        else:
            skipped += 1

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
) -> dict:
    """
    Generate TTS audio for commentaries belonging to deliveries in specific overs.

    Only processes commentaries that have text but no audio yet.

    Args:
        match_id:       Integer match ID.
        innings:        Innings number (1 or 2).
        overs_0indexed: List of 0-indexed over numbers.
        language:       Optional language filter.

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
        match_id, ball_ids, language=language
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

    generated = 0
    failed = 0
    skipped = 0

    for row in pending:
        result = await generate_commentary_audio(row["id"])
        if result["status"] == "generated":
            generated += 1
        elif result["status"] == "failed":
            failed += 1
        else:
            skipped += 1

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
) -> dict:
    """
    Generate TTS audio for all pending commentaries of a specific delivery.

    Returns summary dict with ball_id, total, generated, failed counts.
    """
    pending = await get_commentaries_pending_audio_by_ball_ids(match_id, [ball_id])

    if not pending:
        return {
            "ball_id": ball_id,
            "total": 0,
            "generated": 0,
            "failed": 0,
            "message": "No commentaries pending audio for this delivery",
        }

    generated = 0
    failed = 0

    for row in pending:
        result = await generate_commentary_audio(row["id"])
        if result["status"] == "generated":
            generated += 1
        else:
            failed += 1

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
