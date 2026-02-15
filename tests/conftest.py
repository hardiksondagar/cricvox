"""
Shared fixtures for the test suite.

Key design decisions:
  - Uses an **in-memory SQLite** DB so tests are fast and isolated.
  - Overrides the database module's global `_db` / `DB_PATH` before each test.
  - Provides an `httpx.AsyncClient` wired to the FastAPI app via ASGITransport.
  - Supplies small, realistic cricket delivery fixtures for seeding.
"""

import asyncio
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.storage.database as db_mod
from app.main import app


# --------------------------------------------------------------------------- #
#  Event loop — use a single loop for all async tests in a session
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# --------------------------------------------------------------------------- #
#  In-memory database — fresh for every test function
# --------------------------------------------------------------------------- #

@pytest_asyncio.fixture(autouse=True)
async def _init_test_db(tmp_path: Path):
    """
    Before each test:
      1. Point the DB module to a temp file (in-memory doesn't work with
         multiple connections that aiosqlite might open).
      2. Run init_db() to create all tables.
    After the test:
      3. Close the connection.
    """
    test_db = tmp_path / "test.db"
    db_mod.DB_DIR = tmp_path
    db_mod.DB_PATH = test_db

    await db_mod.init_db()
    yield
    await db_mod.close_db()


# --------------------------------------------------------------------------- #
#  HTTP client — talks to FastAPI app without a real server
# --------------------------------------------------------------------------- #

@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --------------------------------------------------------------------------- #
#  Small realistic delivery data for seeding
# --------------------------------------------------------------------------- #

SAMPLE_MATCH_INFO = {
    "title": "Test Match",
    "batting_team": "Team B",
    "bowling_team": "Team A",
    "target": 151,
    "innings_summary": [
        {
            "innings_number": 1,
            "batting_team": "Team A",
            "bowling_team": "Team B",
            "total_runs": 150,
            "total_wickets": 6,
            "total_balls": 12,
        },
        {
            "innings_number": 2,
            "batting_team": "Team B",
            "bowling_team": "Team A",
            "total_runs": 0,
            "total_wickets": 0,
            "total_balls": 12,
        },
    ],
}


def _make_deliveries(innings: int = 2) -> list[dict]:
    """
    12 deliveries (2 overs) with a mix of events:
    Over 0: dot, single, four, dot, wide, six, wicket (bowled)
    Over 1: single, two, dot, single, four, single
    """
    return [
        # Over 0
        {"over": 0, "ball": 1, "batter": "Batter A", "bowler": "Bowler X",
         "runs": 0, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False,
         "non_batter": "Batter B"},
        {"over": 0, "ball": 2, "batter": "Batter A", "bowler": "Bowler X",
         "runs": 1, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False,
         "non_batter": "Batter B"},
        {"over": 0, "ball": 3, "batter": "Batter B", "bowler": "Bowler X",
         "runs": 4, "extras": 0, "is_wicket": False, "is_boundary": True, "is_six": False,
         "non_batter": "Batter A"},
        {"over": 0, "ball": 4, "batter": "Batter B", "bowler": "Bowler X",
         "runs": 0, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False,
         "non_batter": "Batter A"},
        {"over": 0, "ball": 4, "batter": "Batter B", "bowler": "Bowler X",
         "runs": 0, "extras": 1, "extras_type": "wide", "is_wicket": False, "is_boundary": False, "is_six": False,
         "non_batter": "Batter A"},
        {"over": 0, "ball": 5, "batter": "Batter B", "bowler": "Bowler X",
         "runs": 6, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": True,
         "non_batter": "Batter A"},
        {"over": 0, "ball": 6, "batter": "Batter B", "bowler": "Bowler X",
         "runs": 0, "extras": 0, "is_wicket": True, "wicket_type": "bowled", "is_boundary": False, "is_six": False,
         "non_batter": "Batter A"},
        # Over 1
        {"over": 1, "ball": 1, "batter": "Batter A", "bowler": "Bowler Y",
         "runs": 1, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False,
         "non_batter": "Batter C"},
        {"over": 1, "ball": 2, "batter": "Batter C", "bowler": "Bowler Y",
         "runs": 2, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False,
         "non_batter": "Batter A"},
        {"over": 1, "ball": 3, "batter": "Batter C", "bowler": "Bowler Y",
         "runs": 0, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False,
         "non_batter": "Batter A"},
        {"over": 1, "ball": 4, "batter": "Batter C", "bowler": "Bowler Y",
         "runs": 1, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False,
         "non_batter": "Batter A"},
        {"over": 1, "ball": 5, "batter": "Batter A", "bowler": "Bowler Y",
         "runs": 4, "extras": 0, "is_wicket": False, "is_boundary": True, "is_six": False,
         "non_batter": "Batter C"},
        {"over": 1, "ball": 6, "batter": "Batter A", "bowler": "Bowler Y",
         "runs": 1, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False,
         "non_batter": "Batter C"},
    ]


@pytest_asyncio.fixture
async def seeded_match() -> dict:
    """
    Create a match and bulk-insert deliveries for innings 1 and 2.
    Returns {"match_id": int, "match": dict}.
    """
    from app.storage.database import create_match, insert_deliveries_bulk

    match = await create_match(
        title="Test Match",
        match_info=SAMPLE_MATCH_INFO,
        languages=["hi"],
    )
    mid = match["match_id"]

    await insert_deliveries_bulk(mid, 1, _make_deliveries(innings=1))
    await insert_deliveries_bulk(mid, 2, _make_deliveries(innings=2))

    return {"match_id": mid, "match": match}
