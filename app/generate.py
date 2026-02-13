"""
Commentary generation engine.

Decoupled from the web server — reads balls from DB, generates commentary
+ TTS for each configured language, writes results to match_commentaries.

Usage:
    python -m app.generate <match_id> [start_over]

Examples:
    python -m app.generate 1          # generate from over 1
    python -m app.generate 1 7        # generate from over 7
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
    init_db, close_db, get_match, get_balls, update_match_status,
    delete_commentaries, insert_commentary,
)
from app.storage.audio import save_audio, clear_audio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Per-ball generation
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
    """Generate TTS + save audio + insert one commentary row for one language."""
    display_text = strip_audio_tags(text)

    audio_url = None
    try:
        audio_bytes = await synthesize_speech(text, branch, is_pivot, language=language)
        if audio_bytes:
            audio_url = save_audio(match_id, text, language, audio_bytes)
    except Exception as e:
        logger.error(f"TTS failed ({language}): {e}")

    await insert_commentary(
        match_id=match_id,
        ball_id=ball_id,
        seq=seq,
        event_type=event_type,
        language=language,
        text=display_text,
        audio_url=audio_url,
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
            text = f"{ball.batsman} — {ball.runs} run(s)."
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
#  Score update helper
# ------------------------------------------------------------------ #

def _build_score_data(state, ball: BallEvent, logic_result) -> dict:
    """Build the language-independent score update data."""
    return {
        "total_runs": state.total_runs,
        "wickets": state.wickets,
        "overs": state.overs_display,
        "crr": state.crr,
        "rrr": state.rrr,
        "runs_needed": state.runs_needed,
        "balls_remaining": state.balls_remaining,
        "batting_team": state.batting_team,
        "bowling_team": state.bowling_team,
        "target": state.target,
        "match_phase": state.match_phase,
        "batsman": ball.batsman,
        "bowler": ball.bowler,
        "ball_runs": ball.runs + ball.extras,
        "is_wicket": ball.is_wicket,
        "is_boundary": ball.is_boundary,
        "is_six": ball.is_six,
        "branch": logic_result.branch.value,
        "is_pivot": logic_result.is_pivot,
    }


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
    ball_rows = await get_balls(match_id, innings=2)
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
        ball_data = br["data"]
        all_balls.append((br["id"], BallEvent(**ball_data), br.get("context")))

    start_over_0 = max(start_over - 1, 0)
    warmup = [(bid, b, ctx) for bid, b, ctx in all_balls if b.over < start_over_0]
    live = [(bid, b, ctx) for bid, b, ctx in all_balls if b.over >= start_over_0]

    # Fast-forward: build state for warmup balls
    # If pre-computed, use the last warmup ball's state snapshot
    if warmup and has_precomputed:
        last_warmup_ctx = warmup[-1][2]
        if last_warmup_ctx:
            state = MatchState.model_validate(last_warmup_ctx["state"])
            # Also fast-forward state_mgr for potential fallback
            for _, ball, _ in warmup:
                state_mgr.update(ball)
            logger.info(f"Fast-forward (precomputed): {state.total_runs}/{state.wickets} after {state.overs_display}")
        else:
            # Fallback
            for _, ball, _ in warmup:
                state_mgr.update(ball)
            state = state_mgr.get_state()
            logger.info(f"Fast-forward: {state.total_runs}/{state.wickets} after {state.overs_display}")
    elif warmup:
        logger.info(f"Fast-forwarding {len(warmup)} balls to build state...")
        for _, ball, _ in warmup:
            state_mgr.update(ball)
        state = state_mgr.get_state()
        logger.info(f"Fast-forward: {state.total_runs}/{state.wickets} after {state.overs_display}")
    else:
        state = state_mgr.get_state()

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
    else:
        # Score sync event for mid-match start
        seq += 1
        await insert_commentary(
            match_id, None, seq, "score_update", None, None, None,
            _build_score_data(state, BallEvent(over=0, ball=0, batsman="", bowler=""),
                              type("LR", (), {"branch": NarrativeBranch.ROUTINE, "is_pivot": False})()),
        )

    # ============================================================ #
    #  Ball-by-ball loop
    # ============================================================ #
    for ball_db_id, ball, precomputed_ctx in live:

        # -- Resolve state + logic for this ball --
        if precomputed_ctx:
            # Use pre-computed context
            state = MatchState.model_validate(precomputed_ctx["state"])
            logic_result = LogicResult.model_validate(precomputed_ctx["logic"])
            score_data = precomputed_ctx["score_data"]
            match_over = precomputed_ctx["match_over"]
            narrative_triggers = precomputed_ctx.get("narratives", [])
        else:
            # Fallback: compute on the fly
            state = state_mgr.update(ball)
            logic_result = logic_engine.analyze(state, ball)
            score_data = _build_score_data(state, ball, logic_result)
            match_over = (
                state.runs_needed <= 0
                or state.wickets >= 10
                or state.balls_remaining <= 0
            )
            narrative_triggers = None  # handled inline below

        # Inject runtime commentary history into state
        state.commentary_history = list(commentary_history)

        # 2. Score update (language-independent)
        seq += 1
        await insert_commentary(
            match_id, ball_db_id, seq, "score_update", None, None, None,
            score_data,
        )

        # 3. Ball commentary (one row per language)
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
            f"[{state.overs_display}] {ball.batsman}: {ball.runs}"
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
    batsman_name = ball.batsman
    if not match_over and batsman_name in state.batsmen:
        batter = state.batsmen[batsman_name]
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
                batsman_name=batsman_name,
                batsman_runs=batter.runs,
                batsman_balls=batter.balls_faced,
                batsman_fours=batter.fours,
                batsman_sixes=batter.sixes,
                batsman_sr=batter.strike_rate,
                situation=f"Need {state.runs_needed} from {state.balls_remaining} balls",
            )
            if text:
                commentary_history.append(text)
                if len(commentary_history) > 6:
                    commentary_history.pop(0)

    # --- NEW BATSMAN ---
    if not match_over and ball.is_wicket and state.wickets < 10:
        dismissed = ball.dismissal_batsman or ball.batsman
        partnership_broken = ""
        last_fow = state.fall_of_wickets[-1] if state.fall_of_wickets else None
        if last_fow:
            partnership_broken = (
                f"{dismissed} out for {last_fow.batsman_runs} at "
                f"{last_fow.team_score}/{last_fow.wicket_number}"
            )
        situation = f"Need {state.runs_needed} from {state.balls_remaining} balls"
        if state.is_collapse:
            situation += " — collapse in progress!"
        seq += 1
        text = await _generate_narrative_all_langs(
            match_id, ball_db_id, seq, "new_batsman", state, languages,
            branch=NarrativeBranch.WICKET_DRAMA,
            new_batsman="",
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
        if state.batsmen:
            top_bat = max(state.batsmen.values(), key=lambda b: b.runs)
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


# ------------------------------------------------------------------ #
#  CLI entry point
# ------------------------------------------------------------------ #

async def _main():
    if len(sys.argv) < 2:
        print("Usage: python -m app.generate <match_id> [start_over]")
        sys.exit(1)

    match_id = int(sys.argv[1])
    start_over = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    await init_db()
    try:
        await generate_match(match_id, start_over)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(_main())
