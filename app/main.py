"""
FastAPI app — comprehensive API for AI Cricket Commentary Engine.

API surface:
  Matches:       POST /api/matches, GET list/detail, PATCH update
  Deliveries:    POST single/bulk (auto-computes context + innings summary)
  Commentaries:  GET list/detail, DELETE
  Generate text: POST whole match, POST single delivery
  Generate audio: POST whole match, POST single commentary
  Innings:       GET summary (batting/bowling stats from stored deliveries),
                 GET innings records, GET batters, GET bowlers, GET partnerships
  Languages:     GET list
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from app.models import SUPPORTED_LANGUAGES
from app.storage.database import (
    init_db, close_db,
    # Matches
    create_match, get_match, list_matches, update_match, delete_match,
    # Deliveries
    insert_delivery, insert_deliveries_bulk, get_deliveries, get_all_deliveries,
    get_delivery_by_id, row_to_delivery_event,
    # Commentaries
    get_commentaries_after, get_commentary_by_id,
    get_commentaries_pending_audio, delete_commentaries,
    # Innings stats
    get_innings_batters, get_innings_bowlers, get_fall_of_wickets,
    # Innings & partnerships
    get_innings, get_partnerships, upsert_innings,
    # Match players
    upsert_match_players_bulk, get_match_players, delete_match_players,
)
from app.generate import (
    generate_match, generate_ball_commentary,
    generate_match_audio, generate_commentary_audio,
)
from app.engine.precompute import precompute_match_context, precompute_ball_context
from app.engine.state_manager import StateManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ================================================================== #
#  App lifecycle
# ================================================================== #

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AI Cricket Commentary Engine starting up")
    await init_db()
    yield
    await close_db()
    logger.info("Shutting down")


app = FastAPI(
    title="AI Cricket Commentary Engine",
    description="Real-time AI-powered cricket commentary with TTS",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ================================================================== #
#  Page routes (serve frontend)
# ================================================================== #

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse("static/index.html")


@app.get("/match/{match_id}", response_class=HTMLResponse)
async def match_page(match_id: int):
    return FileResponse("static/index.html")


# ================================================================== #
#  API: Languages
# ================================================================== #

@app.get("/api/languages")
async def api_get_languages():
    """List all supported commentary languages."""
    return [
        {"code": code, "name": cfg["name"], "native_name": cfg["native_name"]}
        for code, cfg in SUPPORTED_LANGUAGES.items()
    ]


# ================================================================== #
#  API: Matches — CRUD
# ================================================================== #

class MatchCreateInput(BaseModel):
    title: str
    match_info: dict[str, Any] = {}
    languages: list[str] = ["hi"]
    status: str = "ready"
    venue: str | None = None
    format: str | None = None
    team1: str | None = None
    team2: str | None = None
    match_date: str | None = None
    players: list[dict[str, Any]] | None = None


class MatchUpdateInput(BaseModel):
    title: str | None = None
    status: str | None = None
    languages: list[str] | None = None
    match_info: dict[str, Any] | None = None
    venue: str | None = None
    format: str | None = None
    team1: str | None = None
    team2: str | None = None
    match_date: str | None = None


@app.post("/api/matches", status_code=201)
async def api_create_match(body: MatchCreateInput):
    """Create a new match. Optionally accepts a players list to upsert alongside."""
    match = await create_match(
        title=body.title,
        match_info=body.match_info,
        languages=body.languages,
        status=body.status,
        venue=body.venue,
        format=body.format,
        team1=body.team1,
        team2=body.team2,
        match_date=body.match_date,
    )
    match_id = match["match_id"]

    # If players provided, upsert them and include in response
    if body.players:
        await upsert_match_players_bulk(match_id, body.players)
        players = await get_match_players(match_id)
        match["players"] = players

    return match


@app.get("/api/matches")
async def api_list_matches(status: str | None = None):
    """List all matches, optionally filtered by status."""
    return await list_matches(status=status)


@app.get("/api/matches/{match_id}")
async def api_get_match(match_id: int):
    """Get a single match by ID."""
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


@app.patch("/api/matches/{match_id}")
async def api_update_match(match_id: int, body: MatchUpdateInput):
    """
    Update match fields (title, status, languages, match_info).
    Only provided fields are updated.
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    fields = body.model_dump(exclude_none=True)
    if not fields:
        return match

    updated = await update_match(match_id, **fields)
    return updated


@app.delete("/api/matches/{match_id}")
async def api_delete_match(match_id: int):
    """Delete a match and all related data (deliveries, commentaries)."""
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    result = await delete_match(match_id)
    return result


# ================================================================== #
#  API: Deliveries — CRUD
# ================================================================== #

class DeliveryInput(BaseModel):
    innings: int = 2
    ball_index: int = 0
    over: int
    ball: int
    batter: str
    bowler: str
    runs: int = 0
    extras: int = 0
    extras_type: str | None = None
    is_wicket: bool = False
    wicket_type: str | None = None
    dismissal_batter: str | None = None
    is_boundary: bool = False
    is_six: bool = False
    non_batter: str | None = None
    batter_id: int | None = None
    non_batter_id: int | None = None
    bowler_id: int | None = None


class BulkDeliveriesInput(BaseModel):
    innings: int
    deliveries: list[dict[str, Any]]


async def _update_innings_summary(match_id: int, innings: int) -> None:
    """
    Replay deliveries through StateManager and attach innings summary to match_info.

    For innings 1: stored as match_info.first_innings (used by LLM for chase context).
    For innings 2: stored as match_info.second_innings.
    """
    ball_rows = await get_deliveries(match_id, innings)
    if not ball_rows:
        return

    match = await get_match(match_id)
    if not match:
        return

    match_info = match.get("match_info", {})

    # Resolve team names
    innings_summaries = match_info.get("innings_summary", [])
    inn_meta = next(
        (s for s in innings_summaries if s.get("innings_number") == innings), {}
    )

    # Replay all deliveries through StateManager
    state_mgr = StateManager(
        batting_team=inn_meta.get("batting_team", ""),
        bowling_team=inn_meta.get("bowling_team", ""),
        target=match_info.get("target", 0) if innings == 2 else 0,
    )
    for br in ball_rows:
        state_mgr.update(row_to_delivery_event(br))

    summary = state_mgr.get_innings_summary()

    key = "first_innings" if innings == 1 else "second_innings"
    match_info[key] = summary
    await update_match(match_id, match_info=match_info)


@app.post("/api/matches/{match_id}/deliveries", status_code=201)
async def api_add_delivery(match_id: int, body: DeliveryInput):
    """
    Add a single ball delivery to a match.

    Automatically:
      - Computes the ball's context (state, logic, narratives)
      - Updates the innings summary in match_info
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    ball_data = body.model_dump(exclude_none=True)
    ball_id = await insert_delivery(
        match_id=match_id,
        innings=body.innings,
        ball_index=body.ball_index,
        over=body.over,
        ball=body.ball,
        batter=body.batter,
        bowler=body.bowler,
        data=ball_data,
        non_batter=body.non_batter,
        batter_id=body.batter_id,
        non_batter_id=body.non_batter_id,
        bowler_id=body.bowler_id,
        runs=body.runs,
        extras=body.extras,
        extras_type=body.extras_type,
        is_wicket=body.is_wicket,
        is_boundary=body.is_boundary,
        is_six=body.is_six,
    )

    # Compute context for this ball (replays all previous balls)
    ctx_result = await precompute_ball_context(ball_id)

    # Update innings summary
    await _update_innings_summary(match_id, body.innings)

    return {
        "ball_id": ball_id,
        "match_id": match_id,
        "context_computed": ctx_result.get("status") == "ok",
    }


@app.post("/api/matches/{match_id}/deliveries/bulk", status_code=201)
async def api_add_deliveries_bulk(match_id: int, body: BulkDeliveriesInput):
    """
    Bulk-insert all deliveries for an innings at once.
    Ideal for loading past/completed match data.

    Automatically:
      - Computes context for all inserted deliveries
      - Computes innings summary and stores in match_info
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    if not body.deliveries:
        raise HTTPException(status_code=400, detail="No deliveries provided")

    count = await insert_deliveries_bulk(match_id, body.innings, body.deliveries)

    # Compute context for all deliveries in the match
    ctx_count = await precompute_match_context(match_id)

    # Compute and store innings summary
    await _update_innings_summary(match_id, body.innings)

    return {
        "match_id": match_id,
        "innings": body.innings,
        "deliveries_inserted": count,
        "context_computed": ctx_count,
    }


@app.get("/api/matches/{match_id}/deliveries")
async def api_list_deliveries(match_id: int, innings: int | None = None):
    """
    List all deliveries for a match. Optionally filter by innings number.
    Returns deliveries ordered by innings, then ball_index.
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    if innings is not None:
        deliveries = await get_deliveries(match_id, innings)
    else:
        deliveries = await get_all_deliveries(match_id)

    return {
        "match_id": match_id,
        "innings": innings,
        "total": len(deliveries),
        "deliveries": deliveries,
    }


@app.get("/api/deliveries/{delivery_id}")
async def api_get_delivery(delivery_id: int):
    """Get a single delivery by its ID (includes pre-computed context if available)."""
    delivery = await get_delivery_by_id(delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return delivery


# ================================================================== #
#  API: Innings summary (computed from stored deliveries)
# ================================================================== #

@app.get("/api/matches/{match_id}/innings/{innings}/summary")
async def api_innings_summary(match_id: int, innings: int):
    """
    Compute batting and bowling summary for an innings from stored deliveries.

    Returns top scorers, top bowlers, totals, and per-player breakdowns.
    Derived entirely from the deliveries in the database — no external data needed.
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    ball_rows = await get_deliveries(match_id, innings)
    if not ball_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No deliveries found for match {match_id} innings {innings}",
        )

    # Resolve team names from match_info.innings_summary
    match_info = match.get("match_info", {})
    innings_summaries = match_info.get("innings_summary", [])
    inn_meta = next(
        (s for s in innings_summaries if s.get("innings_number") == innings), {}
    )

    # Replay deliveries through StateManager
    state_mgr = StateManager(
        batting_team=inn_meta.get("batting_team", ""),
        bowling_team=inn_meta.get("bowling_team", ""),
        target=match_info.get("target", 0) if innings == 2 else 0,
    )
    for br in ball_rows:
        state_mgr.update(row_to_delivery_event(br))

    summary = state_mgr.get_innings_summary()
    summary["match_id"] = match_id
    summary["innings"] = innings
    return summary


# ================================================================== #
#  API: Innings stats (from dedicated tables)
# ================================================================== #

@app.get("/api/matches/{match_id}/innings/{innings}/batters")
async def api_innings_batters(match_id: int, innings: int):
    """Get all batter stats for an innings from the innings_batters table."""
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    rows = await get_innings_batters(match_id, innings)
    return {"match_id": match_id, "innings": innings, "batters": rows}


@app.get("/api/matches/{match_id}/innings/{innings}/bowlers")
async def api_innings_bowlers(match_id: int, innings: int):
    """Get all bowler stats for an innings from the innings_bowlers table."""
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    rows = await get_innings_bowlers(match_id, innings)
    return {"match_id": match_id, "innings": innings, "bowlers": rows}


@app.get("/api/matches/{match_id}/innings/{innings}/fall-of-wickets")
async def api_fall_of_wickets(match_id: int, innings: int):
    """Get fall of wickets for an innings from the fall_of_wickets table."""
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    rows = await get_fall_of_wickets(match_id, innings)
    return {"match_id": match_id, "innings": innings, "fall_of_wickets": rows}


@app.get("/api/matches/{match_id}/innings/{innings}/partnerships")
async def api_partnerships(match_id: int, innings: int):
    """Get partnerships for an innings."""
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    rows = await get_partnerships(match_id, innings)
    return {"match_id": match_id, "innings": innings, "partnerships": rows}


@app.get("/api/matches/{match_id}/innings")
async def api_match_innings(match_id: int):
    """Get innings records for a match."""
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    rows = await get_innings(match_id)
    return {"match_id": match_id, "innings": rows}


# ================================================================== #
#  API: Match Players (Squad / Playing XI)
# ================================================================== #

class MatchPlayersInput(BaseModel):
    players: list[dict[str, Any]]


@app.post("/api/matches/{match_id}/players", status_code=201)
async def api_upsert_match_players(match_id: int, body: MatchPlayersInput):
    """
    Bulk upsert match players (squad / playing XI).

    Each player dict should have: player_name, team.
    Optional: player_id, is_captain, is_keeper,
    player_status ('Playing XI', 'Substitute', 'Impact Player').
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    if not body.players:
        raise HTTPException(status_code=400, detail="No players provided")

    count = await upsert_match_players_bulk(match_id, body.players)
    return {"match_id": match_id, "players_upserted": count}


@app.get("/api/matches/{match_id}/players")
async def api_get_match_players(match_id: int, team: str | None = None):
    """Get match players, optionally filtered by team."""
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    players = await get_match_players(match_id, team=team)
    return {"match_id": match_id, "players": players}


@app.delete("/api/matches/{match_id}/players")
async def api_delete_match_players(match_id: int):
    """Delete all players for a match."""
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    count = await delete_match_players(match_id)
    return {"match_id": match_id, "deleted": count}


# ================================================================== #
#  API: Match timeline (frontend compat — grouped by innings)
# ================================================================== #

@app.get("/api/matches/{match_id}/timeline")
async def api_match_timeline(match_id: int):
    """
    Return all deliveries for both innings grouped by innings with team info.
    Used by the frontend progress bar. Lightweight: no context or commentary.
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    all_deliveries = await get_all_deliveries(match_id)

    innings_map: dict[int, list] = {}
    for b in all_deliveries:
        inn = b["innings"]
        if inn not in innings_map:
            innings_map[inn] = []
        data = b.get("data") or {}
        innings_map[inn].append({
            "ball_id": b["id"],
            "over": b["over"],
            "ball": b["ball"],
            "batter": b["batter"],
            "bowler": b["bowler"],
            "runs": b["runs"],
            "extras": b["extras"],
            "extras_type": b["extras_type"],
            "is_wicket": b["is_wicket"],
            "wicket_type": data.get("wicket_type"),
            "is_boundary": b["is_boundary"],
            "is_six": b["is_six"],
        })

    match_info = match.get("match_info", {})
    innings_summary = match_info.get("innings_summary", [])
    innings_list = []
    for inn_num in sorted(innings_map.keys()):
        summary = next((s for s in innings_summary if s.get("innings_number") == inn_num), {})
        innings_list.append({
            "innings_number": inn_num,
            "batting_team": summary.get("batting_team", ""),
            "bowling_team": summary.get("bowling_team", ""),
            "deliveries": innings_map[inn_num],
        })

    return {
        "match_id": match_id,
        "status": match["status"],
        "innings": innings_list,
    }


@app.get("/api/matches/{match_id}/full")
async def api_get_match_full(match_id: int):
    """
    Return **everything** about a match in a single call.

    Response structure::

        {
            "match": { ... },
            "innings": [
                {
                    ...innings record...,
                    "batters": [...],
                    "bowlers": [...],
                    "fall_of_wickets": [...],
                    "partnerships": [...]
                }
            ],
            "deliveries": [ ... ],          # all deliveries across all innings
            "commentaries": [ ... ],        # all commentaries (all languages)
            "summary": {
                "total_deliveries": N,
                "total_commentaries": N,
                "innings_summary": [...],
                "first_innings": {...},
                "second_innings": {...},
                "target": N
            }
        }
    """
    import asyncio

    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    # Fetch independent data in parallel
    innings_records, all_deliveries, all_commentaries, players = await asyncio.gather(
        get_innings(match_id),
        get_all_deliveries(match_id),
        get_commentaries_after(match_id, -1, language=None),
        get_match_players(match_id),
    )

    # Enrich each innings record with batters, bowlers, FOW, partnerships
    enriched_innings = []
    for inn in innings_records:
        inn_num = inn["innings_number"]
        batters, bowlers, fow, partnerships = await asyncio.gather(
            get_innings_batters(match_id, inn_num),
            get_innings_bowlers(match_id, inn_num),
            get_fall_of_wickets(match_id, inn_num),
            get_partnerships(match_id, inn_num),
        )
        enriched_innings.append({
            **inn,
            "batters": batters,
            "bowlers": bowlers,
            "fall_of_wickets": fow,
            "partnerships": partnerships,
        })

    match_info = match.get("match_info", {})
    summary = {
        "total_deliveries": len(all_deliveries),
        "total_commentaries": len(all_commentaries),
        "innings_summary": match_info.get("innings_summary", []),
        "first_innings": match_info.get("first_innings"),
        "second_innings": match_info.get("second_innings"),
        "target": match_info.get("target"),
    }

    return {
        "match": match,
        "players": players,
        "innings": enriched_innings,
        "deliveries": all_deliveries,
        "commentaries": all_commentaries,
        "summary": summary,
    }


# ================================================================== #
#  API: Commentaries — read / delete
# ================================================================== #

@app.get("/api/matches/{match_id}/commentaries")
async def api_get_commentaries(match_id: int, after_seq: int = 0, language: str | None = "hi"):
    """
    Poll for commentaries. Returns events with seq > after_seq.
    Filters by language (returns requested language + language-independent events).
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    commentaries = await get_commentaries_after(match_id, after_seq, language=language)
    return {
        "match": match,
        "commentaries": commentaries,
    }


@app.get("/api/commentaries/{commentary_id}")
async def api_get_commentary(commentary_id: int):
    """Get a single commentary row by ID (includes joined ball data)."""
    row = await get_commentary_by_id(commentary_id)
    if not row:
        raise HTTPException(status_code=404, detail="Commentary not found")
    return row


@app.delete("/api/matches/{match_id}/commentaries")
async def api_delete_commentaries(match_id: int):
    """Delete all commentaries for a match (useful before re-generation)."""
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    count = await delete_commentaries(match_id)
    return {"match_id": match_id, "deleted": count}


# ================================================================== #
#  API: Commentary text generation (LLM)
# ================================================================== #

class GenerateMatchRequest(BaseModel):
    start_over: int = 1


@app.post("/api/matches/{match_id}/commentaries/generate")
async def api_generate_match_commentary(
    match_id: int,
    body: GenerateMatchRequest,
    background_tasks: BackgroundTasks,
):
    """
    Generate LLM commentary text for an entire match (no audio).

    Processes all deliveries from start_over, generates score updates,
    ball commentary, and narrative moments for all configured languages.

    Runs in the background — returns immediately with status.
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    if match["status"] == "generating":
        raise HTTPException(status_code=409, detail="Generation already in progress")

    background_tasks.add_task(generate_match, match_id, body.start_over)

    return {
        "match_id": match_id,
        "status": "started",
        "start_over": body.start_over,
        "message": "Commentary text generation started in background",
    }


class GenerateBallRequest(BaseModel):
    languages: list[str] | None = None


@app.post("/api/deliveries/{delivery_id}/commentaries/generate")
async def api_generate_delivery_commentary(delivery_id: int, body: GenerateBallRequest):
    """
    Generate LLM commentary for a single delivery (no audio).

    The delivery must have pre-computed context (run /precompute first).
    Generates score update + ball commentary + any narrative triggers.

    Runs synchronously — returns the generated commentary details.
    """
    delivery = await get_delivery_by_id(delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")

    result = await generate_ball_commentary(
        match_id=delivery["match_id"],
        ball_id=delivery_id,
        languages=body.languages,
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return result


# ================================================================== #
#  API: Audio generation (TTS) — separate from text
# ================================================================== #

class GenerateAudioRequest(BaseModel):
    language: str | None = None


@app.post("/api/matches/{match_id}/commentaries/generate-audio")
async def api_generate_match_audio(
    match_id: int,
    body: GenerateAudioRequest,
    background_tasks: BackgroundTasks,
):
    """
    Generate TTS audio for all commentaries in a match that don't have audio yet.

    Optionally filter by language. Runs in the background.
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    pending = await get_commentaries_pending_audio(match_id, language=body.language)

    if not pending:
        return {
            "match_id": match_id,
            "language": body.language,
            "status": "nothing_to_do",
            "pending": 0,
            "message": "No commentaries pending audio generation",
        }

    background_tasks.add_task(generate_match_audio, match_id, body.language)

    return {
        "match_id": match_id,
        "language": body.language,
        "status": "started",
        "pending": len(pending),
        "message": f"Audio generation started for {len(pending)} commentaries",
    }


@app.post("/api/commentaries/{commentary_id}/generate-audio")
async def api_generate_single_audio(commentary_id: int):
    """
    Generate TTS audio for a single commentary row.

    Useful for retrying failed audio or generating audio on demand.
    Runs synchronously — returns the result directly.
    """
    result = await generate_commentary_audio(commentary_id)

    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Commentary not found")

    return result
