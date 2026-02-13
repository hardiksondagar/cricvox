"""
FastAPI app — slim read-heavy API.

Endpoints:
  - Match CRUD (list, detail)
  - Ball addition (POST /balls for future live feed)
  - Commentary polling (GET /commentaries?after_seq=N&language=hi)
  - Supported languages

Generation is handled separately by app/generate.py.
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from app.models import SUPPORTED_LANGUAGES
from app.storage.database import (
    init_db, close_db, get_match, list_matches,
    insert_ball, get_commentaries_after, seed_matches,
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
    await seed_matches()
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
#  Page routes
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
async def get_languages():
    return [
        {"code": code, "name": cfg["name"], "native_name": cfg["native_name"]}
        for code, cfg in SUPPORTED_LANGUAGES.items()
    ]


# ================================================================== #
#  API: Matches
# ================================================================== #

@app.get("/api/matches")
async def api_list_matches(status: str | None = None):
    return await list_matches(status=status)


@app.get("/api/matches/{match_id}")
async def api_get_match(match_id: int):
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


# ================================================================== #
#  API: Ball addition (for future live feed)
# ================================================================== #

class BallInput(BaseModel):
    innings: int = 2
    ball_index: int = 0
    over: int
    ball: int
    batsman: str
    bowler: str
    runs: int = 0
    extras: int = 0
    extras_type: str | None = None
    is_wicket: bool = False
    wicket_type: str | None = None
    dismissal_batsman: str | None = None
    is_boundary: bool = False
    is_six: bool = False
    non_striker: str | None = None


@app.post("/api/matches/{match_id}/balls")
async def add_ball(match_id: int, ball_input: BallInput):
    """Add a ball delivery to a match. Returns the ball ID."""
    match = await get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    ball_data = ball_input.model_dump(exclude_none=True)
    ball_id = await insert_ball(
        match_id=match_id,
        innings=ball_input.innings,
        ball_index=ball_input.ball_index,
        over=ball_input.over,
        ball=ball_input.ball,
        batsman=ball_input.batsman,
        bowler=ball_input.bowler,
        data=ball_data,
    )
    return {"ball_id": ball_id, "match_id": match_id}


# ================================================================== #
#  API: Commentary polling
# ================================================================== #

@app.get("/api/matches/{match_id}/commentaries")
async def get_commentaries(match_id: int, after_seq: int = 0, language: str | None = "hi"):
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
