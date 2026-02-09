import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.feed.mock_feed import load_match_data
from app.engine.state_manager import StateManager
from app.engine.logic_engine import LogicEngine
from app.commentary.generator import generate_commentary, generate_narrative
from app.audio.tts import synthesize_speech
from app.models import NarrativeBranch, SUPPORTED_LANGUAGES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global state for the match broadcast
match_running = False
subscribers: list[asyncio.Queue] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App startup/shutdown lifecycle."""
    logger.info("AI Cricket Commentary Engine starting up")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="AI Cricket Commentary Engine",
    description="Real-time AI-powered cricket commentary with TTS",
    lifespan=lifespan,
)

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main dashboard."""
    return FileResponse("static/index.html")


@app.get("/api/languages")
async def get_languages():
    """Return list of supported commentary languages."""
    return [
        {"code": code, "name": cfg["name"], "native_name": cfg["native_name"]}
        for code, cfg in SUPPORTED_LANGUAGES.items()
    ]


@app.get("/api/match-info")
async def get_match_info(innings: int = 2):
    """Return the match metadata."""
    match_info, balls = load_match_data(innings=innings)
    return {
        "match_info": match_info,
        "total_balls": len(balls),
    }


@app.get("/api/stream")
async def stream(request: Request):
    """SSE endpoint that streams live match events to the dashboard."""
    queue: asyncio.Queue = asyncio.Queue()
    subscribers.append(queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield event
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield {"event": "ping", "data": "{}"}
        finally:
            subscribers.remove(queue)

    return EventSourceResponse(event_generator())


async def broadcast(event_type: str, data: dict):
    """Push an SSE event to all connected subscribers."""
    event = {
        "event": event_type,
        "data": json.dumps(data, default=str),
    }
    for queue in subscribers:
        await queue.put(event)


@app.post("/api/start")
async def start_match(start_over: int = 1, language: str = "en"):
    """
    Start the match replay.

    Args:
        start_over: Over number to start generating commentary from (1-indexed).
                    e.g. start_over=15 skips to the 15th over.
                    All earlier balls are fast-forwarded (state built silently).
                    Default: 1 (start from the beginning).
        language:   Language code for commentary (e.g. 'en', 'hi', 'ta', 'te').
                    Default: 'en' (English).
    """
    global match_running
    if match_running:
        return {"status": "already_running"}

    # Validate language code
    if language not in SUPPORTED_LANGUAGES:
        language = "en"

    match_running = True
    asyncio.create_task(_run_match(start_over=start_over, language=language))
    return {"status": "started", "start_over": start_over, "language": language}


@app.post("/api/stop")
async def stop_match():
    """Stop the match replay."""
    global match_running
    match_running = False
    return {"status": "stopped"}


@app.get("/api/status")
async def match_status():
    """Return whether the match is currently running."""
    return {"running": match_running}


async def _broadcast_narrative(
    moment_type: str,
    state=None,
    branch: NarrativeBranch = NarrativeBranch.OVER_TRANSITION,
    language: str = "en",
    **kwargs,
):
    """Generate a narrative moment, synthesize audio, broadcast, and log."""
    try:
        text = await generate_narrative(moment_type, state, language=language, **kwargs)
    except Exception as e:
        logger.error(f"Narrative generation failed ({moment_type}): {e}")
        return

    if not text:
        return

    # TTS for narrative moments
    try:
        audio_b64 = await synthesize_speech(text, branch, is_pivot=False, language=language)
    except Exception as e:
        logger.error(f"Narrative TTS failed ({moment_type}): {e}")
        audio_b64 = None

    await broadcast("commentary", {
        "text": text,
        "audio_base64": audio_b64,
        "branch": branch.value,
        "is_pivot": False,
        "is_narrative": True,
        "narrative_type": moment_type,
    })

    # Add to commentary history so ball commentary knows what was just said
    if state is not None:
        state.commentary_history.append(text)
        if len(state.commentary_history) > 6:
            state.commentary_history.pop(0)

    logger.info(f"[NARRATIVE:{moment_type}] {text}")


async def _run_match(start_over: int = 1, language: str = "en"):
    """
    Main match loop: process balls, generate commentary, broadcast via SSE.

    Args:
        start_over: 1-indexed over to start commentary from.
                    Balls before this over are fast-forwarded (state built, no LLM/TTS).
        language:   Language code for commentary generation and TTS.
    """
    global match_running

    # Convert to 0-indexed for internal use
    start_over_0 = max(start_over - 1, 0)

    match_info, all_balls = load_match_data()
    state_mgr = StateManager(
        batting_team=match_info["batting_team"],
        bowling_team=match_info["bowling_team"],
        target=match_info["target"],
    )
    logic_engine = LogicEngine()

    # Extract first innings data (available when replaying innings 2)
    first_innings = match_info.get("first_innings", {})

    lang_name = SUPPORTED_LANGUAGES.get(language, {}).get("name", language)
    logger.info(
        f"Match started: {match_info['batting_team']} vs {match_info['bowling_team']}, "
        f"target {match_info['target']}, start_over={start_over}, language={lang_name}"
    )

    # ============================================================ #
    #  FAST-FORWARD: build state silently for all balls before start_over
    # ============================================================ #
    warmup_balls = [b for b in all_balls if b.over < start_over_0]
    live_balls = [b for b in all_balls if b.over >= start_over_0]

    if warmup_balls:
        logger.info(
            f"Fast-forwarding {len(warmup_balls)} balls "
            f"(overs 1-{start_over - 1}) to build state..."
        )
        for ball in warmup_balls:
            state_mgr.update(ball)
        state = state_mgr.get_state()
        logger.info(
            f"Fast-forward complete: {state.total_runs}/{state.wickets} "
            f"after {state.overs_display} overs"
        )
    else:
        state = state_mgr.get_state()

    # Broadcast match start
    await broadcast("match_start", match_info)

    # ============================================================ #
    #  PRE-MATCH NARRATIVES
    # ============================================================ #
    if start_over <= 1:
        # Full pre-match sequence for starting from the beginning
        # 1. FIRST INNINGS START
        await _broadcast_narrative(
            "first_innings_start",
            state=None,
            language=language,
            match_title=match_info.get("title", ""),
            venue=match_info.get("venue", ""),
            match_format=match_info.get("format", "T20"),
            batting_team=first_innings.get("batting_team", match_info.get("bowling_team", "")),
            bowling_team=first_innings.get("bowling_team", match_info.get("batting_team", "")),
        )
        await asyncio.sleep(4)

        # 2. FIRST INNINGS END
        if first_innings:
            await _broadcast_narrative(
                "first_innings_end",
                state=None,
                language=language,
                first_batting_team=first_innings.get("batting_team", ""),
                first_innings_runs=first_innings.get("total_runs", 0),
                first_innings_wickets=first_innings.get("total_wickets", 0),
                top_scorers=first_innings.get("top_scorers", "N/A"),
                top_bowlers=first_innings.get("top_bowlers", "N/A"),
                first_innings_fours=first_innings.get("total_fours", 0),
                first_innings_sixes=first_innings.get("total_sixes", 0),
                first_innings_extras=first_innings.get("total_extras", 0),
            )
            await asyncio.sleep(4)

        # 3. SECOND INNINGS START
        await _broadcast_narrative(
            "second_innings_start",
            state,
            language=language,
            first_batting_team=first_innings.get("batting_team", ""),
            first_innings_runs=first_innings.get("total_runs", 0),
            first_innings_wickets=first_innings.get("total_wickets", 0),
            venue=match_info.get("venue", ""),
            match_title=match_info.get("title", ""),
        )
        await asyncio.sleep(3)
    else:
        # Skipping ahead — just sync the frontend with current state
        await broadcast("score_update", {
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
            "batsman": state.current_batsman or "",
            "bowler": state.current_bowler or "",
            "ball_runs": 0,
            "is_wicket": False,
            "is_boundary": False,
            "is_six": False,
            "branch": "routine",
            "is_pivot": False,
        })

    # Track phase for detecting transitions
    previous_phase = state.match_phase
    previous_overs_completed = state.overs_completed

    # ============================================================ #
    #  LIVE BALL-BY-BALL LOOP
    # ============================================================ #
    delay = settings.ball_delay_seconds

    for ball in live_balls:
        if not match_running:
            break

        # 1. Update state
        state = state_mgr.update(ball)

        # 2. Logic engine analysis
        logic_result = logic_engine.analyze(state, ball)

        # 3. Broadcast score update immediately
        score_data = {
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
        await broadcast("score_update", score_data)

        # 4. Generate ball commentary
        try:
            commentary_text = await generate_commentary(state, ball, logic_result, language=language)
        except Exception as e:
            logger.error(f"Commentary generation failed: {e}")
            commentary_text = f"{ball.batsman} — {ball.runs} run(s)."

        # 5. Synthesize audio
        try:
            audio_b64 = await synthesize_speech(
                commentary_text,
                logic_result.branch,
                logic_result.is_pivot,
                language=language,
            )
        except Exception as e:
            logger.error(f"TTS failed: {e}")
            audio_b64 = None

        # 6. Broadcast ball commentary
        commentary_data = {
            "text": commentary_text,
            "audio_base64": audio_b64,
            "branch": logic_result.branch.value,
            "is_pivot": logic_result.is_pivot,
            "equation_shift": logic_result.equation_shift,
            "over": ball.over,
            "ball": ball.ball,
            "batsman": ball.batsman,
            "bowler": ball.bowler,
        }
        await broadcast("commentary", commentary_data)

        # 7. Update commentary history
        state.last_commentary = commentary_text
        state.commentary_history.append(commentary_text)
        if len(state.commentary_history) > 6:
            state.commentary_history.pop(0)

        logger.info(
            f"[{state.overs_display}] {ball.batsman}: {ball.runs}{'W' if ball.is_wicket else ''} "
            f"| {state.total_runs}/{state.wickets} | {logic_result.branch.value} "
            f"| {commentary_text}"
        )

        # ============================================================ #
        #  CHECK: is the match over? If so, skip to result.
        # ============================================================ #
        match_over = (
            state.runs_needed <= 0
            or state.wickets >= 10
            or state.balls_remaining <= 0
        )

        # ============================================================ #
        #  NARRATIVE MOMENTS — only if match is still alive
        # ============================================================ #

        # --- MILESTONE: batsman reaches 50 or 100 ---
        batsman_name = ball.batsman
        if not match_over and batsman_name in state.batsmen:
            batter = state.batsmen[batsman_name]
            milestone_type = None
            if batter.just_reached_fifty and batter.runs < 100:
                milestone_type = "FIFTY"
            elif batter.just_reached_hundred:
                milestone_type = "HUNDRED"
            if milestone_type:
                await asyncio.sleep(1.5)
                await _broadcast_narrative(
                    "milestone", state,
                    branch=NarrativeBranch.BOUNDARY_MOMENTUM,
                    language=language,
                    milestone_type=milestone_type,
                    batsman_name=batsman_name,
                    batsman_runs=batter.runs,
                    batsman_balls=batter.balls_faced,
                    batsman_fours=batter.fours,
                    batsman_sixes=batter.sixes,
                    batsman_sr=batter.strike_rate,
                    situation=f"Need {state.runs_needed} from {state.balls_remaining} balls",
                )

        # --- NEW BATSMAN: after a wicket (before next delivery) ---
        if not match_over and ball.is_wicket and state.wickets < 10:
            await asyncio.sleep(2)
            # Figure out who the new batsman will be
            dismissed = ball.dismissal_batsman or ball.batsman
            new_batsman = ""
            # The new batsman will appear on the NEXT ball, but we can preview
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
            await _broadcast_narrative(
                "new_batsman", state,
                branch=NarrativeBranch.WICKET_DRAMA,
                language=language,
                new_batsman=new_batsman,
                position=state.wickets + 1,
                partnership_broken=partnership_broken,
                situation=situation,
            )

        # --- END OF OVER: over just completed (skip if match is over) ---
        if not match_over and state.overs_completed > previous_overs_completed:
            await asyncio.sleep(1.5)

            # Get the bowler's figures for this over
            bowler_name = ball.bowler
            bowler_figures = ""
            if bowler_name in state.bowlers:
                bowler_figures = state.bowlers[bowler_name].figures_str

            over_runs = state.over_runs_history[-1] if state.over_runs_history else 0

            # Check for phase change
            current_phase = state.match_phase
            phase_changed = current_phase != previous_phase

            if phase_changed:
                # Combined end-of-over + phase change
                phase_summary = ""
                if previous_phase == "powerplay":
                    phase_summary = f"Powerplay done: {state.powerplay_runs} runs, {state.wickets} wickets"
                elif previous_phase == "middle":
                    phase_summary = f"Middle overs done: {state.middle_overs_runs} runs scored"

                await _broadcast_narrative(
                    "phase_change", state,
                    language=language,
                    new_phase=current_phase.title() + " Overs",
                    phase_summary=phase_summary,
                )
                previous_phase = current_phase
            else:
                # Count wickets in the just-completed over from FOW log
                completed_over_num = state.overs_completed - 1
                over_wickets = sum(
                    1 for f in state.fall_of_wickets
                    if f.overs.startswith(f"{completed_over_num}.")
                )
                # Regular end-of-over narrative
                await _broadcast_narrative(
                    "end_of_over", state,
                    language=language,
                    over_runs=over_runs,
                    over_wickets=over_wickets,
                    bowler=bowler_name,
                    bowler_figures=bowler_figures,
                    phase_info=f"Phase: {state.match_phase}",
                )

            previous_overs_completed = state.overs_completed

        # --- Wait before next ball ---
        await asyncio.sleep(delay)

        # --- Match result ---
        if match_over:
            await asyncio.sleep(2)

            # Determine result text
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

            # Build match highlights for the result narrative
            highlights = []
            # Top scorer of the chase
            if state.batsmen:
                top_bat = max(state.batsmen.values(), key=lambda b: b.runs)
                if top_bat.runs >= 15:
                    highlights.append(
                        f"Top scorer in chase: {top_bat.name} "
                        f"{top_bat.runs}({top_bat.balls_faced})"
                    )
            # Best bowler of the chase
            if state.bowlers:
                top_bowl = max(state.bowlers.values(), key=lambda b: b.wickets)
                if top_bowl.wickets > 0:
                    highlights.append(
                        f"Best bowler: {top_bowl.name} {top_bowl.figures_str}"
                    )
            # First innings context
            if first_innings:
                highlights.append(
                    f"First innings: {first_innings.get('batting_team', '')} "
                    f"{first_innings.get('total_runs', 0)}/"
                    f"{first_innings.get('total_wickets', 0)}"
                )
            match_highlights = "\n".join(highlights) if highlights else ""

            # --- NARRATIVE: MATCH RESULT ---
            await _broadcast_narrative(
                "match_result", state,
                branch=NarrativeBranch.WICKET_DRAMA,
                language=language,
                result_text=result_text,
                match_highlights=match_highlights,
            )

            await broadcast("match_end", {
                "result": "won" if state.runs_needed <= 0 else "lost",
                "final_score": f"{state.total_runs}/{state.wickets}",
                "overs": state.overs_display,
            })
            break

    match_running = False
    logger.info("Match finished")
