"""
FastAPI app — comprehensive API for AI Cricket Commentary Engine.

API surface:
  Matches:        POST /api/matches, GET list/detail, PATCH update
  Deliveries:     POST single/bulk (auto-computes context + innings summary)
  Commentaries:   GET list/detail, DELETE
  Generate text:  POST /api/matches/{id}/generate_commentaries
                  (all / by overs / by delivery_id, optional audio)
  Generate audio: POST /api/matches/{id}/generate_commentaries_audio
                  (all / by overs / by commentary_id)
  Innings:        GET summary, innings records, batters, bowlers, partnerships
  Languages:      GET list
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from app.models import SUPPORTED_LANGUAGES
from app.commentary.prompts import strip_audio_tags
from app.storage.database import (
    init_db, close_db,
    # Matches
    create_match, get_match, list_matches, update_match, delete_match,
    # Deliveries
    insert_delivery, insert_deliveries_bulk, get_deliveries, get_all_deliveries,
    get_delivery_by_id, row_to_delivery_event, get_max_seq,
    # Commentaries
    get_commentaries_after, get_commentary_by_id,
    get_commentaries_pending_audio, delete_commentaries,
    insert_commentary, get_timeline_items,
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
    generate_overs_commentary, generate_overs_audio,
    generate_ball_audio,
)
from app.engine.precompute import precompute_match_context, precompute_ball_context
from app.engine.state_manager import StateManager
from app.commentary.precomputed_text import (
    precomputed_delivery_text,
    precomputed_first_innings_start_text,
    precomputed_first_innings_end_text,
    precomputed_second_innings_start_text,
    precomputed_end_of_over_text,
    precomputed_phase_change_text,
    precomputed_second_innings_end_text,
)

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


def _match_languages(match: dict) -> list[str]:
    """Get match languages, filtered to supported ones. Defaults to ['hi']."""
    langs = match.get("languages") or ["hi"]
    if isinstance(langs, str):
        langs = [langs]
    return [l for l in langs if l in SUPPORTED_LANGUAGES] or ["hi"]


async def _insert_delivery_skeleton(
    match_id: int, ball_id: int, seq: int, delivery: dict, languages: list[str],
) -> int:
    """Insert delivery skeleton rows (one per language) with precomputed text."""
    d = delivery.get("data") or {}
    oc = delivery.get('overs_completed', delivery['over'])
    bio = delivery.get('balls_in_over', delivery['ball'])
    overs_display = f"{oc}.{bio}"
    data = {
        "over": delivery["over"],
        "ball": delivery["ball"],
        "innings": delivery["innings"],
        "batter": delivery["batter"],
        "bowler": delivery["bowler"],
        "non_batter": delivery.get("non_batter"),
        "runs": delivery["runs"],
        "extras": delivery["extras"],
        "extras_type": delivery["extras_type"],
        "is_wicket": delivery["is_wicket"],
        "wicket_type": d.get("wicket_type"),
        "is_boundary": delivery["is_boundary"],
        "is_six": delivery["is_six"],
        "total_runs": delivery.get("total_runs"),
        "total_wickets": delivery.get("total_wickets"),
        "overs": overs_display,
        "crr": delivery.get("crr"),
        "rrr": delivery.get("rrr"),
        "runs_needed": delivery.get("runs_needed"),
        "balls_remaining": delivery.get("balls_remaining"),
        "match_phase": delivery.get("match_phase"),
    }
    text = precomputed_delivery_text({**delivery, **data})
    for lang in languages:
        await insert_commentary(
            match_id=match_id, ball_id=ball_id, seq=seq,
            event_type="delivery", language=lang, text=text, audio_url=None,
            data=data, is_generated=False,
        )
    return len(languages)


async def _create_structural_skeletons_for_ball(
    match_id: int, ball_id: int, delivery: dict, match: dict,
) -> int:
    """
    Create structural event skeletons for a single ball add.
    All skeletons are tied to ball_id for delivery-based generation.
    Returns count of skeletons inserted.
    """
    ctx = delivery.get("context") or {}

    match_info = match.get("match_info", {})
    innings_summaries = match_info.get("innings_summary", [])
    first_innings = match_info.get("first_innings", {})
    innings_num = delivery["innings"]
    ball_index = delivery.get("ball_index", 0)

    inn_meta = next(
        (s for s in innings_summaries if s.get("innings_number") == innings_num), {}
    )
    batting_team = inn_meta.get("batting_team", match_info.get("batting_team", ""))
    bowling_team = inn_meta.get("bowling_team", match_info.get("bowling_team", ""))
    languages = _match_languages(match)

    seq = await get_max_seq(match_id)
    inserted = 0

    # --- Pre-delivery: first_innings_start (first ball of match) ---
    if innings_num == 1 and ball_index == 0:
        seq += 1
        first_inn = first_innings or {"batting_team": batting_team, "bowling_team": bowling_team}
        text = precomputed_first_innings_start_text(match_info, first_inn)
        for lang in languages:
            await insert_commentary(
                match_id, ball_id, seq, "first_innings_start", lang, text, None,
                {**match_info, "first_innings": first_inn}, is_generated=False,
            )
            inserted += 1

    # --- Pre-delivery: first_innings_end + second_innings_start (first ball of inn 2) ---
    if innings_num == 2 and ball_index == 0:
        inn1_deliveries = await get_deliveries(match_id, innings=1)
        last_inn1_id = inn1_deliveries[-1]["id"] if inn1_deliveries else ball_id

        seq += 1
        text = precomputed_first_innings_end_text(first_innings)
        for lang in languages:
            await insert_commentary(
                match_id, last_inn1_id, seq, "first_innings_end", lang, text, None,
                {"innings": 1, **first_innings}, is_generated=False,
            )
            inserted += 1

        seq += 1
        text = precomputed_second_innings_start_text(match_info, first_innings)
        for lang in languages:
            await insert_commentary(
                match_id, ball_id, seq, "second_innings_start", lang, text, None,
                {"innings": 2, "target": match_info.get("target", 0)}, is_generated=False,
            )
            inserted += 1

    # --- Delivery skeleton ---
    seq += 1
    inserted += await _insert_delivery_skeleton(match_id, ball_id, seq, delivery, languages)

    # --- Post-delivery: from context narratives ---
    narratives = ctx.get("narratives", [])
    match_over = ctx.get("match_over", False)

    for narr in narratives:
        ntype = narr.get("type")
        nkwargs = narr.get("kwargs", {})

        if ntype == "end_of_over":
            seq += 1
            text = precomputed_end_of_over_text(nkwargs)
            oc = delivery.get("overs_completed", delivery.get("over", 0))
            for lang in languages:
                await insert_commentary(
                    match_id, ball_id, seq, "end_of_over", lang, text, None,
                    {"innings": innings_num, "over": oc - 1, **nkwargs}, is_generated=False,
                )
                inserted += 1
        elif ntype == "phase_change":
            seq += 1
            text = precomputed_phase_change_text(nkwargs)
            for lang in languages:
                await insert_commentary(
                    match_id, ball_id, seq, "phase_change", lang, text, None,
                    {"innings": innings_num, **nkwargs}, is_generated=False,
                )
                inserted += 1
        elif ntype == "second_innings_end":
            seq += 1
            result = "won" if delivery.get("runs_needed", 1) <= 0 else "lost"
            text = precomputed_second_innings_end_text({
                "result": result,
                "final_score": f"{delivery.get('total_runs', 0)}/{delivery.get('total_wickets', 0)}",
                "overs": f"{delivery.get('overs_completed', 0)}.{delivery.get('balls_in_over', 0)}",
            })
            for lang in languages:
                await insert_commentary(
                    match_id, ball_id, seq, "second_innings_end", lang, text, None,
                    {**nkwargs, "result": result}, is_generated=False,
                )
                inserted += 1

    # --- first_innings_end (last ball of innings 1, when innings complete) ---
    if innings_num == 1 and match_over:
        seq += 1
        text = precomputed_first_innings_end_text(
            first_innings or {
                "batting_team": batting_team,
                "total_runs": delivery.get("total_runs", 0),
                "total_wickets": delivery.get("total_wickets", 0),
            }
        )
        for lang in languages:
            await insert_commentary(
                match_id, ball_id, seq, "first_innings_end", lang, text, None,
                {"innings": 1, "batting_team": batting_team, "bowling_team": bowling_team,
                 "total_runs": delivery.get("total_runs"), "total_wickets": delivery.get("total_wickets")},
                is_generated=False,
            )
            inserted += 1

    return inserted


async def _create_bulk_commentary_skeletons(match_id: int, innings: int) -> int:
    """
    After bulk-inserting deliveries, create commentary skeleton rows.
    All skeletons tied to ball_id. Includes precomputed text.
    """
    deliveries = await get_deliveries(match_id, innings)
    if not deliveries:
        return 0

    match = await get_match(match_id)
    if not match:
        return 0

    match_info = match.get("match_info", {})
    innings_summaries = match_info.get("innings_summary", [])
    first_innings = match_info.get("first_innings", {})

    inn_meta = next(
        (s for s in innings_summaries if s.get("innings_number") == innings), {}
    )
    batting_team = inn_meta.get("batting_team", match_info.get("batting_team", ""))
    bowling_team = inn_meta.get("bowling_team", match_info.get("bowling_team", ""))

    languages = _match_languages(match)
    seq = await get_max_seq(match_id)
    inserted = 0

    # Innings 1: first_innings_start before first ball
    if innings == 1:
        first_id = deliveries[0]["id"]
        seq += 1
        first_inn = first_innings or {"batting_team": batting_team, "bowling_team": bowling_team}
        text = precomputed_first_innings_start_text(match_info, first_inn)
        for lang in languages:
            await insert_commentary(
                match_id, first_id, seq, "first_innings_start", lang, text, None,
                {**match_info, "first_innings": first_inn}, is_generated=False,
            )
            inserted += 1

    # Innings 2: first_innings_end (ball_id=last inn 1), second_innings_start (ball_id=first inn 2)
    if innings == 2:
        inn1 = await get_deliveries(match_id, innings=1)
        last_inn1_id = inn1[-1]["id"] if inn1 else deliveries[0]["id"]

        seq += 1
        text = precomputed_first_innings_end_text(first_innings)
        for lang in languages:
            await insert_commentary(
                match_id, last_inn1_id, seq, "first_innings_end", lang, text, None,
                {"innings": 1, **first_innings}, is_generated=False,
            )
            inserted += 1

        seq += 1
        text = precomputed_second_innings_start_text(match_info, first_innings)
        for lang in languages:
            await insert_commentary(
                match_id, deliveries[0]["id"], seq, "second_innings_start", lang, text, None,
                {"innings": 2, "target": match_info.get("target", 0)}, is_generated=False,
            )
            inserted += 1

    prev_over = None
    last_ball_id = None
    for d in deliveries:
        curr_over = d["over"]
        ctx = d.get("context") or {}

        # end_of_over / phase_change when over changes (tied to last ball of previous over)
        if prev_over is not None and curr_over != prev_over and last_ball_id:
            # Get narratives from the last ball of the completed over
            last_ball = next((x for x in deliveries if x["id"] == last_ball_id), None)
            narratives = (last_ball or {}).get("context") or {}
            narratives = narratives.get("narratives", []) if isinstance(narratives, dict) else []
            phase_narr = next((n for n in narratives if n.get("type") == "phase_change"), None)
            over_narr = next((n for n in narratives if n.get("type") == "end_of_over"), None)

            if phase_narr:
                seq += 1
                text = precomputed_phase_change_text(phase_narr.get("kwargs", {}))
                for lang in languages:
                    await insert_commentary(
                        match_id, last_ball_id, seq, "phase_change", lang, text, None,
                        {"innings": innings, **phase_narr.get("kwargs", {})}, is_generated=False,
                    )
                    inserted += 1
            elif over_narr:
                seq += 1
                text = precomputed_end_of_over_text(over_narr.get("kwargs", {}))
                for lang in languages:
                    await insert_commentary(
                        match_id, last_ball_id, seq, "end_of_over", lang, text, None,
                        {"innings": innings, "over": prev_over, **over_narr.get("kwargs", {})}, is_generated=False,
                    )
                    inserted += 1
            else:
                seq += 1
                text = precomputed_end_of_over_text({"over": prev_over, "over_runs": 0, "bowler": ""})
                for lang in languages:
                    await insert_commentary(
                        match_id, last_ball_id, seq, "end_of_over", lang, text, None,
                        {"innings": innings, "over": prev_over}, is_generated=False,
                    )
                    inserted += 1

        # Delivery skeleton
        seq += 1
        inserted += await _insert_delivery_skeleton(match_id, d["id"], seq, d, languages)
        prev_over = curr_over
        last_ball_id = d["id"]

    # first_innings_end is created when processing innings 2 (above), not here — avoids duplicate

    # second_innings_end after last ball of innings 2 when match over
    if innings == 2:
        last_d = deliveries[-1]
        ctx = last_d.get("context") or {}
        if ctx.get("match_over"):
            for narr in ctx.get("narratives", []):
                if narr.get("type") == "second_innings_end":
                    seq += 1
                    nkwargs = narr.get("kwargs", {})
                    result = "won" if last_d.get("runs_needed", 1) <= 0 else "lost"
                    text = precomputed_second_innings_end_text({
                        "result": result,
                        "final_score": f"{last_d.get('total_runs', 0)}/{last_d.get('total_wickets', 0)}",
                        "overs": f"{last_d.get('overs_completed', 0)}.{last_d.get('balls_in_over', 0)}",
                    })
                    for lang in languages:
                        await insert_commentary(
                            match_id, last_d["id"], seq, "second_innings_end", lang, text, None,
                            {**nkwargs, "result": result}, is_generated=False,
                        )
                        inserted += 1
                    break

    return inserted


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

    # Auto-create structural + delivery skeletons (all tied to ball_id)
    delivery = await get_delivery_by_id(ball_id)
    if delivery:
        await _create_structural_skeletons_for_ball(match_id, ball_id, delivery, match)

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

    # Auto-create commentary skeleton rows for all deliveries + structural events
    await _create_bulk_commentary_skeletons(match_id, body.innings)

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
    Return a flat list of timeline items for the frontend progress bar.

    Each item is either a 'ball' (with delivery snapshot) or an 'event'
    (structural: first_innings_start, innings_break, end_of_over, etc.).

    Items are ordered by seq. The frontend renders each item as a badge
    and uses ball_info (when present) for scoreboard snapshots on scrub.
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    items = await get_timeline_items(match_id)
    # Strip audio tags for display (DB stores raw text for TTS)
    for item in items:
        if item.get("text"):
            item["text"] = strip_audio_tags(item["text"])

    # Also provide innings metadata for team names
    match_info = match.get("match_info", {})
    innings_summary = match_info.get("innings_summary", [])

    return {
        "match_id": match_id,
        "status": match["status"],
        "items": items,
        "innings_summary": innings_summary,
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

    # Strip audio tags for display (DB stores raw text for TTS)
    for c in all_commentaries:
        if c.get("text"):
            c["text"] = strip_audio_tags(c["text"])

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
    # Strip audio tags for display (DB stores raw text for TTS)
    for c in commentaries:
        if c.get("text"):
            c["text"] = strip_audio_tags(c["text"])
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
    if row.get("text"):
        row["text"] = strip_audio_tags(row["text"])
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
#  API: Commentary text generation (LLM) — unified endpoint
# ================================================================== #

@app.post("/api/matches/{match_id}/generate_commentaries")
async def api_generate_commentaries(
    match_id: int,
    background_tasks: BackgroundTasks,
    innings: int | None = None,
    overs: str | None = None,
    delivery_id: int | None = None,
    generate_audio: bool = False,
    force_regenerate: bool = False,
):
    """
    Unified commentary generation endpoint.

    Generates LLM commentary text (and optionally TTS audio) for a match.

    Behaviour depends on query parameters:
      - **No params**: generate for ALL deliveries in the match (background).
      - **overs** (comma-separated, 1-indexed) + **innings**: generate for those overs (background).
        innings is required when overs is provided.
      - **delivery_id** (no overs): generate for that single delivery (sync).
      - **generate_audio** (default false): if true, also generate TTS audio after text.
      - **force_regenerate** (default false): if true, re-generate even when commentary already exists.
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    # ── Case 1: Single delivery (synchronous) ──────────────────────
    if delivery_id is not None and overs is None:
        delivery = await get_delivery_by_id(delivery_id)
        if not delivery:
            raise HTTPException(status_code=404, detail="Delivery not found")
        if delivery["match_id"] != match_id:
            raise HTTPException(
                status_code=400,
                detail="Delivery does not belong to this match",
            )

        result = await generate_ball_commentary(
            match_id=match_id,
            ball_id=delivery_id,
            force_regenerate=force_regenerate,
        )
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["message"])

        # Optionally generate audio for this delivery's commentaries
        if generate_audio:
            audio_result = await generate_ball_audio(match_id, delivery_id)
            result["audio"] = audio_result

        return result

    # ── Case 2: Specific overs (background) ───────────────────────
    if overs is not None:
        if innings is None:
            raise HTTPException(
                status_code=400,
                detail="innings is required when overs is provided",
            )
        try:
            overs_list = [int(o.strip()) for o in overs.split(",") if o.strip()]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid overs format. Use comma-separated numbers (e.g. 1,2,3)",
            )
        if not overs_list:
            raise HTTPException(status_code=400, detail="No valid overs provided")

        overs_0indexed = [o - 1 for o in overs_list]

        async def _bg_overs(mid: int, inn: int, overs_0: list[int], audio: bool, force: bool):
            await generate_overs_commentary(mid, inn, overs_0, force_regenerate=force)
            if audio:
                await generate_overs_audio(mid, inn, overs_0)

        background_tasks.add_task(
            _bg_overs, match_id, innings, overs_0indexed, generate_audio, force_regenerate
        )

        return {
            "match_id": match_id,
            "status": "started",
            "innings": innings,
            "overs": overs_list,
            "generate_audio": generate_audio,
            "force_regenerate": force_regenerate,
            "message": f"Commentary generation started for innings {innings} overs {overs_list}",
        }

    # ── Case 3: Entire match (background) ─────────────────────────
    if match["status"] == "generating":
        raise HTTPException(status_code=409, detail="Generation already in progress")

    async def _bg_match(mid: int, audio: bool, force: bool):
        await generate_match(mid, force_regenerate=force)
        if audio:
            await generate_match_audio(mid)

    background_tasks.add_task(_bg_match, match_id, generate_audio, force_regenerate)

    return {
        "match_id": match_id,
        "status": "started",
        "generate_audio": generate_audio,
        "force_regenerate": force_regenerate,
        "message": "Commentary generation started for all deliveries",
    }


# ================================================================== #
#  API: Audio generation (TTS) — unified endpoint
# ================================================================== #

@app.post("/api/matches/{match_id}/generate_commentaries_audio")
async def api_generate_commentaries_audio(
    match_id: int,
    background_tasks: BackgroundTasks,
    innings: int | None = None,
    overs: str | None = None,
    commentary_id: int | None = None,
    language: str | None = None,
):
    """
    Unified audio generation endpoint.

    Generates TTS audio for existing commentary text.
    Commentary text must exist before audio can be generated.

    Behaviour depends on query parameters:
      - **No params**: generate audio for ALL pending commentaries (background).
      - **overs** (comma-separated, 1-indexed) + **innings**: generate audio for those overs (background).
        innings is required when overs is provided.
      - **commentary_id** (no overs): generate audio for that single commentary (sync).
      - **language**: optional filter — only generate audio for this language.
    """
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    # ── Case 1: Single commentary (synchronous) ──────────────────
    if commentary_id is not None and overs is None:
        row = await get_commentary_by_id(commentary_id)
        if not row:
            raise HTTPException(status_code=404, detail="Commentary not found")
        if row["match_id"] != match_id:
            raise HTTPException(
                status_code=400,
                detail="Commentary does not belong to this match",
            )
        if not row.get("text"):
            raise HTTPException(
                status_code=400,
                detail="Commentary has no text. Generate commentary text first.",
            )

        result = await generate_commentary_audio(commentary_id)
        if result["status"] == "not_found":
            raise HTTPException(status_code=404, detail="Commentary not found")
        return result

    # ── Case 2: Specific overs (background) ──────────────────────
    if overs is not None:
        if innings is None:
            raise HTTPException(
                status_code=400,
                detail="innings is required when overs is provided",
            )
        try:
            overs_list = [int(o.strip()) for o in overs.split(",") if o.strip()]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid overs format. Use comma-separated numbers (e.g. 1,2,3)",
            )
        if not overs_list:
            raise HTTPException(status_code=400, detail="No valid overs provided")

        overs_0indexed = [o - 1 for o in overs_list]

        background_tasks.add_task(
            generate_overs_audio, match_id, innings, overs_0indexed, language
        )

        return {
            "match_id": match_id,
            "status": "started",
            "innings": innings,
            "overs": overs_list,
            "language": language,
            "message": f"Audio generation started for innings {innings} overs {overs_list}",
        }

    # ── Case 3: All pending commentaries (background) ────────────
    pending = await get_commentaries_pending_audio(match_id, language=language)

    if not pending:
        return {
            "match_id": match_id,
            "language": language,
            "status": "nothing_to_do",
            "pending": 0,
            "message": "No commentaries pending audio generation",
        }

    background_tasks.add_task(generate_match_audio, match_id, language)

    return {
        "match_id": match_id,
        "language": language,
        "status": "started",
        "pending": len(pending),
        "message": f"Audio generation started for {len(pending)} commentaries",
    }
