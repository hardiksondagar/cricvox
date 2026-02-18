"""
Pre-compute ball-by-ball context for LLM commentary generation.

Runs StateManager + LogicEngine over every ball in a match and stores
the computed state snapshot, logic result, score data, event description,
and narrative triggers in the match_balls.context column.

This separates deterministic state computation from non-deterministic
LLM generation — everything except commentary_history (which depends on
actual LLM outputs) is pre-computed here.
"""

import logging

from app.models import BallEvent, NarrativeBranch
from app.engine.state_manager import StateManager
from app.engine.logic_engine import LogicEngine
from app.commentary.prompts import build_event_description
from app.storage.database import (
    get_match, get_deliveries,
    update_deliveries_context_bulk, update_delivery_context,
    update_delivery_snapshot, update_delivery_snapshot_bulk,
    upsert_innings_batters_bulk, upsert_innings_bowlers_bulk,
    insert_fall_of_wickets_bulk, delete_innings_stats,
    get_delivery_by_id, row_to_delivery_event,
    upsert_partnerships_bulk, upsert_innings,
    get_match_players,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _build_player_lookup(match_id: int) -> dict[str, int]:
    """
    Build a player_name -> match_players.id lookup for a match.

    Returns an empty dict if no players are registered.
    """
    players = await get_match_players(match_id)
    lookup: dict[str, int] = {}
    for p in players:
        # Use player_name as key (first-wins in case of duplicates)
        name = p["player_name"]
        if name not in lookup:
            lookup[name] = p["id"]
    return lookup


# ------------------------------------------------------------------ #
#  Build per-ball match snapshot (for match_balls columns)
# ------------------------------------------------------------------ #

def _build_snapshot(state, ball=None, player_lookup=None) -> dict:
    """
    Extract the per-ball match snapshot from StateManager state.

    If ball and player_lookup are provided, also resolves non_batter,
    batter_id, non_batter_id, and bowler_id from the match_players table.
    """
    snap = {
        "total_runs": state.total_runs,
        "total_wickets": state.wickets,
        "overs_completed": state.overs_completed,
        "balls_in_over": state.balls_in_current_over,
        "crr": state.crr,
        "rrr": state.rrr,
        "runs_needed": state.runs_needed,
        "balls_remaining": state.balls_remaining,
        "match_phase": state.match_phase,
    }

    # Resolve player fields
    if ball is not None:
        snap["non_batter"] = state.non_batter
        if player_lookup:
            snap["batter_id"] = player_lookup.get(ball.batter)
            snap["bowler_id"] = player_lookup.get(ball.bowler)
            snap["non_batter_id"] = player_lookup.get(state.non_batter) if state.non_batter else None

    return snap


# ------------------------------------------------------------------ #
#  Serialize tracking fields for context JSON (slimmed)
# ------------------------------------------------------------------ #

def _serialize_tracking(state) -> dict:
    """
    Extract runtime tracking fields that the LLM prompt builder needs
    but are not stored in tables/columns.
    """
    batter = state.batters.get(state.current_batter) if state.current_batter else None
    non_batter = state.batters.get(state.non_batter) if state.non_batter else None
    bowler = state.bowlers.get(state.current_bowler) if state.current_bowler else None

    result = {
        "over_runs_history": list(state.over_runs_history),
        "last_6_balls": list(state.last_6_balls),
        "consecutive_dots": state.consecutive_dots,
        "balls_since_last_boundary": state.balls_since_last_boundary,
        "balls_since_last_wicket": state.balls_since_last_wicket,
        "previous_over_summary": state.previous_over_summary,
        "partnership_runs": state.partnership_runs,
        "partnership_balls": state.partnership_balls,
        "partnership_number": state.partnership_number,
        "current_batter": state.current_batter,
        "current_bowler": state.current_bowler,
        "non_batter": state.non_batter,
        "batting_order": list(state.batting_order),
        "is_new_bowler": state.is_new_bowler,
        "is_new_over": state.is_new_over,
        "is_strike_change": state.is_strike_change,
        "is_new_batter": state.is_new_batter,
        "new_batter_name": state.new_batter_name,
        "current_over_runs": state.current_over_runs,
        "current_over_wickets": state.current_over_wickets,
        "total_extras": state.total_extras,
        "total_wides": state.total_wides,
        "total_noballs": state.total_noballs,
        "total_fours": state.total_fours,
        "total_sixes": state.total_sixes,
    }

    if batter:
        result["batter_stats"] = {
            "runs": batter.runs, "balls": batter.balls_faced,
            "fours": batter.fours, "sixes": batter.sixes,
            "sr": round(batter.strike_rate, 2),
        }
    if non_batter:
        result["non_batter_stats"] = {
            "runs": non_batter.runs, "balls": non_batter.balls_faced,
            "fours": non_batter.fours, "sixes": non_batter.sixes,
            "sr": round(non_batter.strike_rate, 2),
        }
    if bowler:
        result["bowler_stats"] = {
            "wickets": bowler.wickets, "runs": bowler.runs_conceded,
            "overs": round(bowler.balls_bowled / 6, 1),
            "economy": round(bowler.economy, 2),
        }

    return result


# ------------------------------------------------------------------ #
#  Extract batters / bowler dicts from StateManager for DB upsert
# ------------------------------------------------------------------ #

def _extract_batters(state) -> list[dict]:
    """Extract all batter stats from state as a list of dicts."""
    return [
        {
            "name": b.name,
            "position": b.position,
            "runs": b.runs,
            "balls_faced": b.balls_faced,
            "fours": b.fours,
            "sixes": b.sixes,
            "dots": b.dots,
            "is_out": b.is_out,
            "strike_rate": round(b.strike_rate, 2),
            "out_status": "Out" if b.is_out else "Not Out",
            "dismissal_info": None,
        }
        for b in state.batters.values()
    ]


def _extract_bowlers(state) -> list[dict]:
    """Extract all bowler stats from state as a list of dicts."""
    return [
        {
            "name": bw.name,
            "balls_bowled": bw.balls_bowled,
            "runs_conceded": bw.runs_conceded,
            "wickets": bw.wickets,
            "maidens": bw.maidens,
            "dots": bw.dots,
            "fours_conceded": bw.fours_conceded,
            "sixes_conceded": bw.sixes_conceded,
            "wides": bw.wides,
            "noballs": bw.noballs,
            "economy": round(bw.economy, 2),
            "overs_bowled": bw.balls_bowled / 6,
        }
        for bw in state.bowlers.values()
    ]


def _extract_fall_of_wickets(state) -> list[dict]:
    """Extract fall of wickets from state as a list of dicts."""
    return [
        {
            "wicket_number": f.wicket_number,
            "batter": f.batter,
            "batter_runs": f.batter_runs,
            "team_score": f.team_score,
            "overs": f.overs,
            "bowler": f.bowler,
            "how": f.how,
        }
        for f in state.fall_of_wickets
    ]


def _resolve_non_batters(balls: list[BallEvent]) -> None:
    """
    Pre-pass that sets non_batter and infers dismissal_batter using lookahead.

    After a wicket, the new batter may not face a ball immediately.
    This scans forward to find the replacement batter and fills in
    non_batter for all deliveries in between, so StateManager always
    knows both batters at the crease.

    When dismissal_batter is missing (common in scraped data), it infers
    who was dismissed by checking which of the two batters at the crease
    appears in subsequent deliveries (the survivor). The other one was out.
    """
    if not balls:
        return

    active: set[str] = set()

    # Seed the opening pair: first batter + first different batter
    active.add(balls[0].batter)
    for b in balls[1:]:
        if b.batter not in active:
            active.add(b.batter)
            break

    for i, ball in enumerate(balls):
        active.add(ball.batter)

        # Set non_batter as the other active batter
        if len(active) >= 2:
            others = [b for b in active if b != ball.batter]
            ball.non_batter = others[0] if others else None

        if ball.is_wicket:
            crease_before = set(active)

            if ball.dismissal_batter:
                dismissed = ball.dismissal_batter
            else:
                # Infer: look ahead to find which batter survived
                survivor = None
                for j in range(i + 1, len(balls)):
                    if balls[j].batter in crease_before:
                        survivor = balls[j].batter
                        break

                if survivor and len(crease_before) == 2:
                    dismissed = (crease_before - {survivor}).pop()
                else:
                    dismissed = ball.batter  # fallback: assume striker

                ball.dismissal_batter = dismissed

            active.discard(dismissed)

            # Lookahead: find the replacement batter
            for j in range(i + 1, len(balls)):
                if balls[j].batter not in active:
                    active.add(balls[j].batter)
                    break


def _extract_partnerships(state) -> list[dict]:
    """Extract partnerships from fall of wickets + final state."""
    partnerships = []
    prev_score = 0

    for fow in state.fall_of_wickets:
        partnership_runs = fow.team_score - prev_score
        partnerships.append({
            "wicket_number": fow.wicket_number,
            "batter1": fow.batter,  # dismissed batter
            "batter2": fow.partner or "",
            "runs": partnership_runs,
            "balls": 0,  # we don't track per-partnership balls in FOW
        })
        prev_score = fow.team_score

    # Add unbroken partnership (current)
    if state.total_runs > prev_score:
        partnerships.append({
            "wicket_number": len(state.fall_of_wickets) + 1,
            "batter1": state.current_batter or "",
            "batter2": state.non_batter or "",
            "runs": state.total_runs - prev_score,
            "balls": 0,
        })

    return partnerships


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
    if not match_over and ball.batter in state.batters:
        batter = state.batters[ball.batter]
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
                    "batter_name": ball.batter,
                    "batter_runs": batter.runs,
                    "batter_balls": batter.balls_faced,
                    "batter_fours": batter.fours,
                    "batter_sixes": batter.sixes,
                    "batter_sr": batter.strike_rate,
                    "situation": f"Need {state.runs_needed} from {state.balls_remaining} balls",
                },
            })

    # --- NEW BATTER (after wicket) ---
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
        narratives.append({
            "type": "new_batter",
            "branch": NarrativeBranch.WICKET_DRAMA.value,
            "kwargs": {
                "new_batter": "",
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

        narratives.append({
            "type": "second_innings_end",
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

    Replays all innings through StateManager and LogicEngine, then:
    - Upserts batter / bowler stats into innings_batters / innings_bowlers
    - Inserts fall of wickets into fall_of_wickets
    - Writes partnerships and innings summary
    - Updates match_balls snapshot columns (total_runs, wickets, crr, rrr, …)
    - Stores slimmed context JSON (logic, narratives, event_description,
      tracking fields — NO batters/bowlers/score_data)

    Returns the total number of balls processed across all innings.
    """
    match = await get_match(match_id)
    if not match:
        logger.error(f"Match {match_id} not found")
        return 0

    match_info = match["match_info"]
    innings_summaries = match_info.get("innings_summary", [])

    # Build player name -> ID lookup (once per match)
    player_lookup = await _build_player_lookup(match_id)

    total_count = 0

    for innings_num in (1, 2):
        ball_rows = await get_deliveries(match_id, innings=innings_num)
        if not ball_rows:
            continue

        # Clean up previous stats for this innings (re-precompute safe)
        await delete_innings_stats(match_id, innings_num)

        # Resolve per-innings team names
        inn_meta = next(
            (s for s in innings_summaries if s.get("innings_number") == innings_num),
            {},
        )
        batting_team = inn_meta.get("batting_team", match_info.get("batting_team", ""))
        bowling_team = inn_meta.get("bowling_team", match_info.get("bowling_team", ""))

        # Innings 1 has no chase target; innings 2 uses match target
        target = match_info.get("target", 0) if innings_num == 2 else 0

        # First innings context is available for innings 2 narratives
        first_innings = match_info.get("first_innings", {}) if innings_num == 2 else {}

        state_mgr = StateManager(
            batting_team=batting_team,
            bowling_team=bowling_team,
            target=target,
        )
        logic_engine = LogicEngine()

        # Convert rows to BallEvents and resolve non_batter via lookahead
        ball_events = [row_to_delivery_event(br) for br in ball_rows]
        _resolve_non_batters(ball_events)

        previous_phase = "powerplay"
        previous_overs_completed = 0
        context_updates: list[tuple[int, dict]] = []
        snapshot_updates: list[tuple[int, dict]] = []

        for br, ball in zip(ball_rows, ball_events):
            state = state_mgr.update(ball)
            logic_result = logic_engine.analyze(state, ball)

            # Innings over conditions
            if innings_num == 2:
                match_over = (
                    state.runs_needed <= 0
                    or state.wickets >= 10
                    or state.balls_remaining <= 0
                )
            else:
                match_over = (
                    state.wickets >= 10
                    or state.total_balls_bowled >= 120
                )

            narrs = _detect_narratives(
                state, ball, match_over,
                previous_phase, previous_overs_completed,
                first_innings,
            )

            if state.overs_completed > previous_overs_completed:
                if state.match_phase != previous_phase:
                    previous_phase = state.match_phase
                previous_overs_completed = state.overs_completed

            # Slimmed context: only LLM-relevant + tracking fields
            context = {
                "logic": logic_result.model_dump(),
                "event_description": build_event_description(ball),
                "match_over": match_over,
                "narratives": narrs,
                "tracking": _serialize_tracking(state),
            }

            context_updates.append((br["id"], context))
            snapshot_updates.append((br["id"], _build_snapshot(state, ball, player_lookup)))

            if match_over:
                break

        # Bulk-write context JSON + snapshot columns (incl. player IDs)
        count = await update_deliveries_context_bulk(context_updates)
        await update_delivery_snapshot_bulk(snapshot_updates)

        # Write final batter / bowler stats to tables
        await upsert_innings_batters_bulk(
            match_id, innings_num, _extract_batters(state),
        )
        await upsert_innings_bowlers_bulk(
            match_id, innings_num, _extract_bowlers(state),
        )

        # Write fall of wickets
        fow = _extract_fall_of_wickets(state)
        if fow:
            await insert_fall_of_wickets_bulk(match_id, innings_num, fow)

        # Write partnerships
        partnerships = _extract_partnerships(state)
        if partnerships:
            await upsert_partnerships_bulk(match_id, innings_num, partnerships)

        # Write innings summary
        await upsert_innings(
            match_id, innings_num,
            batting_team=batting_team,
            bowling_team=bowling_team,
            total_runs=state.total_runs,
            total_wickets=state.wickets,
            total_overs=state.total_balls_bowled / 6,
            extras_total=state.total_extras,
        )

        logger.info(
            f"Pre-computed context for {count} balls "
            f"(match {match_id}, innings {innings_num})"
        )
        total_count += count

    return total_count


async def precompute_ball_context(ball_id: int) -> dict:
    """
    Pre-compute context for a single ball.

    Replays all balls up to and including the target ball through
    StateManager to build accumulated state, then stores:
    - slimmed context JSON
    - snapshot columns on match_balls
    - upserts batter / bowler stats
    - inserts any new fall of wickets

    Returns the computed context dict, or an error dict.
    """
    ball_row = await get_delivery_by_id(ball_id)
    if not ball_row:
        return {"status": "error", "message": f"Ball {ball_id} not found"}

    match_id = ball_row["match_id"]
    innings_num = ball_row["innings"]
    match = await get_match(match_id)
    if not match:
        return {"status": "error", "message": "Match not found"}

    match_info = match["match_info"]
    innings_summaries = match_info.get("innings_summary", [])
    first_innings = match_info.get("first_innings", {}) if innings_num == 2 else {}

    # Build player name -> ID lookup
    player_lookup = await _build_player_lookup(match_id)

    # Resolve per-innings team names
    inn_meta = next(
        (s for s in innings_summaries if s.get("innings_number") == innings_num),
        {},
    )
    batting_team = inn_meta.get("batting_team", match_info.get("batting_team", ""))
    bowling_team = inn_meta.get("bowling_team", match_info.get("bowling_team", ""))
    target = match_info.get("target", 0) if innings_num == 2 else 0

    # Load all balls for the innings (need to replay from the start)
    ball_rows = await get_deliveries(match_id, innings=innings_num)
    if not ball_rows:
        return {"status": "error", "message": "No balls found"}

    state_mgr = StateManager(
        batting_team=batting_team,
        bowling_team=bowling_team,
        target=target,
    )
    logic_engine = LogicEngine()

    previous_phase = "powerplay"
    previous_overs_completed = 0
    target_context = None

    for br in ball_rows:
        ball = row_to_delivery_event(br)
        state = state_mgr.update(ball)
        logic_result = logic_engine.analyze(state, ball)

        if innings_num == 2:
            match_over = (
                state.runs_needed <= 0
                or state.wickets >= 10
                or state.balls_remaining <= 0
            )
        else:
            match_over = (
                state.wickets >= 10
                or state.total_balls_bowled >= 120
            )

        narrs = _detect_narratives(
            state, ball, match_over,
            previous_phase, previous_overs_completed,
            first_innings,
        )

        if state.overs_completed > previous_overs_completed:
            if state.match_phase != previous_phase:
                previous_phase = state.match_phase
            previous_overs_completed = state.overs_completed

        context = {
            "logic": logic_result.model_dump(),
            "event_description": build_event_description(ball),
            "match_over": match_over,
            "narratives": narrs,
            "tracking": _serialize_tracking(state),
        }

        # If this is the target ball, save everything and stop
        if br["id"] == ball_id:
            await update_delivery_context(ball_id, context)

            # Snapshot columns + player IDs
            snap = _build_snapshot(state, ball, player_lookup)
            await update_delivery_snapshot(
                ball_id,
                total_runs=snap["total_runs"],
                total_wickets=snap["total_wickets"],
                overs_completed=snap["overs_completed"],
                balls_in_over=snap["balls_in_over"],
                crr=snap.get("crr"),
                rrr=snap.get("rrr"),
                runs_needed=snap.get("runs_needed"),
                balls_remaining=snap.get("balls_remaining"),
                match_phase=snap.get("match_phase"),
                non_batter=snap.get("non_batter"),
                batter_id=snap.get("batter_id"),
                non_batter_id=snap.get("non_batter_id"),
                bowler_id=snap.get("bowler_id"),
            )

            # Delete previous stats then re-insert (clean slate for this innings)
            await delete_innings_stats(match_id, innings_num)

            # Upsert batter / bowler stats (accumulated up to this ball)
            await upsert_innings_batters_bulk(
                match_id, innings_num, _extract_batters(state),
            )
            await upsert_innings_bowlers_bulk(
                match_id, innings_num, _extract_bowlers(state),
            )
            # Fall of wickets
            fow = _extract_fall_of_wickets(state)
            if fow:
                await insert_fall_of_wickets_bulk(match_id, innings_num, fow)

            # Partnerships
            partnerships = _extract_partnerships(state)
            if partnerships:
                await upsert_partnerships_bulk(match_id, innings_num, partnerships)

            # Innings summary
            await upsert_innings(
                match_id, innings_num,
                batting_team=batting_team,
                bowling_team=bowling_team,
                total_runs=state.total_runs,
                total_wickets=state.wickets,
                total_overs=state.total_balls_bowled / 6,
                extras_total=state.total_extras,
            )

            target_context = context
            logger.info(f"Pre-computed context for ball {ball_id} (match {match_id})")
            break

        if match_over:
            break

    if target_context is None:
        return {"status": "error", "message": "Ball not reached during replay"}

    return {
        "status": "ok",
        "ball_id": ball_id,
        "match_id": match_id,
        "context": target_context,
    }
