"""
Pre-compute ball-by-ball context for LLM commentary generation.

Runs StateManager + LogicEngine over every ball in a match and stores
the computed state snapshot, logic result, score data, event description,
and narrative triggers in the match_balls.context column.

This separates deterministic state computation from non-deterministic
LLM generation — everything except commentary_history (which depends on
actual LLM outputs) is pre-computed here.

Usage:
    python -m app.precompute <match_id>
    python -m app.precompute --all
"""

import asyncio
import json
import logging
import sys

from app.models import BallEvent, NarrativeBranch
from app.engine.state_manager import StateManager
from app.engine.logic_engine import LogicEngine
from app.commentary.prompts import build_event_description
from app.storage.database import (
    init_db, close_db, get_match, get_balls,
    update_balls_context_bulk, list_matches,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Score data builder (same logic as generate.py)
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
#  Serialize MatchState for storage
# ------------------------------------------------------------------ #

def _serialize_state(state) -> dict:
    """
    Serialize MatchState to a dict suitable for JSON storage.
    Excludes commentary_history and last_commentary (runtime-only).
    """
    d = state.model_dump()
    # These fields are populated at generation time with actual LLM outputs
    d.pop("commentary_history", None)
    d.pop("last_commentary", None)
    return d


# ------------------------------------------------------------------ #
#  Narrative trigger detection
# ------------------------------------------------------------------ #

def _detect_narratives(
    state,
    ball: BallEvent,
    match_over: bool,
    previous_phase: str,
    previous_overs_completed: int,
    first_innings: dict,
) -> list[dict]:
    """
    Detect which narrative moments should fire after this ball.
    Returns a list of narrative trigger dicts with type, branch, and kwargs.
    """
    narratives = []

    # --- MILESTONE ---
    if not match_over and ball.batsman in state.batsmen:
        batter = state.batsmen[ball.batsman]
        milestone_type = None
        if batter.just_reached_fifty and batter.runs < 100:
            milestone_type = "FIFTY"
        elif batter.just_reached_hundred:
            milestone_type = "HUNDRED"
        if milestone_type:
            narratives.append({
                "type": "milestone",
                "branch": NarrativeBranch.BOUNDARY_MOMENTUM.value,
                "kwargs": {
                    "milestone_type": milestone_type,
                    "batsman_name": ball.batsman,
                    "batsman_runs": batter.runs,
                    "batsman_balls": batter.balls_faced,
                    "batsman_fours": batter.fours,
                    "batsman_sixes": batter.sixes,
                    "batsman_sr": batter.strike_rate,
                    "situation": f"Need {state.runs_needed} from {state.balls_remaining} balls",
                },
            })

    # --- NEW BATSMAN (after wicket) ---
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
        narratives.append({
            "type": "new_batsman",
            "branch": NarrativeBranch.WICKET_DRAMA.value,
            "kwargs": {
                "new_batsman": "",
                "position": state.wickets + 1,
                "partnership_broken": partnership_broken,
                "situation": situation,
            },
        })

    # --- END OF OVER / PHASE CHANGE ---
    if not match_over and state.overs_completed > previous_overs_completed:
        bowler_name = ball.bowler
        bowler_figures = ""
        if bowler_name in state.bowlers:
            bowler_figures = state.bowlers[bowler_name].figures_str
        over_runs = state.over_runs_history[-1] if state.over_runs_history else 0

        current_phase = state.match_phase
        phase_changed = current_phase != previous_phase

        if phase_changed:
            phase_summary = ""
            if previous_phase == "powerplay":
                phase_summary = f"Powerplay done: {state.powerplay_runs} runs, {state.wickets} wickets"
            elif previous_phase == "middle":
                phase_summary = f"Middle overs done: {state.middle_overs_runs} runs scored"
            narratives.append({
                "type": "phase_change",
                "branch": NarrativeBranch.OVER_TRANSITION.value,
                "kwargs": {
                    "new_phase": current_phase.title() + " Overs",
                    "phase_summary": phase_summary,
                },
            })
        else:
            completed_over_num = state.overs_completed - 1
            over_wickets = sum(
                1 for f in state.fall_of_wickets
                if f.overs.startswith(f"{completed_over_num}.")
            )
            narratives.append({
                "type": "end_of_over",
                "branch": NarrativeBranch.OVER_TRANSITION.value,
                "kwargs": {
                    "over_runs": over_runs,
                    "over_wickets": over_wickets,
                    "bowler": bowler_name,
                    "bowler_figures": bowler_figures,
                    "phase_info": f"Phase: {state.match_phase}",
                },
            })

    # --- MATCH RESULT ---
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

        narratives.append({
            "type": "match_result",
            "branch": NarrativeBranch.WICKET_DRAMA.value,
            "kwargs": {
                "result_text": result_text,
                "match_highlights": "\n".join(highlights) if highlights else "",
            },
        })

    return narratives


# ------------------------------------------------------------------ #
#  Main pre-computation
# ------------------------------------------------------------------ #

async def precompute_match_context(match_id: int) -> int:
    """
    Pre-compute state + logic context for every ball in a match.

    Processes all innings-2 balls through StateManager and LogicEngine,
    storing the computed context in match_balls.context.

    Returns the number of balls processed.
    """
    match = await get_match(match_id)
    if not match:
        logger.error(f"Match {match_id} not found")
        return 0

    match_info = match["match_info"]
    first_innings = match_info.get("first_innings", {})

    # Load balls from DB
    ball_rows = await get_balls(match_id, innings=2)
    if not ball_rows:
        logger.error(f"No balls found for match {match_id} innings 2")
        return 0

    # Build state manager + logic engine
    state_mgr = StateManager(
        batting_team=match_info.get("batting_team", ""),
        bowling_team=match_info.get("bowling_team", ""),
        target=match_info.get("target", 0),
    )
    logic_engine = LogicEngine()

    previous_phase = "powerplay"
    previous_overs_completed = 0
    updates: list[tuple[int, dict]] = []

    for br in ball_rows:
        ball = BallEvent(**br["data"])

        # 1. Update state
        state = state_mgr.update(ball)
        logic_result = logic_engine.analyze(state, ball)

        # 2. Check match over
        match_over = (
            state.runs_needed <= 0
            or state.wickets >= 10
            or state.balls_remaining <= 0
        )

        # 3. Detect narrative triggers
        narrs = _detect_narratives(
            state, ball, match_over,
            previous_phase, previous_overs_completed,
            first_innings,
        )

        # Track phase/over changes for next iteration
        if state.overs_completed > previous_overs_completed:
            if state.match_phase != previous_phase:
                previous_phase = state.match_phase
            previous_overs_completed = state.overs_completed

        # 4. Build context blob
        context = {
            "state": _serialize_state(state),
            "logic": logic_result.model_dump(),
            "event_description": build_event_description(ball),
            "score_data": _build_score_data(state, ball, logic_result),
            "match_over": match_over,
            "narratives": narrs,
        }

        updates.append((br["id"], context))

        if match_over:
            break

    # Bulk write to DB
    count = await update_balls_context_bulk(updates)
    logger.info(f"Pre-computed context for {count} balls (match {match_id})")
    return count


# ------------------------------------------------------------------ #
#  CLI entry point
# ------------------------------------------------------------------ #

async def _main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m app.precompute <match_id>   # one match")
        print("  python -m app.precompute --all         # all matches")
        sys.exit(1)

    await init_db()
    try:
        if sys.argv[1] == "--all":
            matches = await list_matches()
            for m in matches:
                await precompute_match_context(m["match_id"])
        else:
            match_id = int(sys.argv[1])
            await precompute_match_context(match_id)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(_main())
