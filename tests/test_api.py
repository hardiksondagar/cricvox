"""
Integration tests for FastAPI API endpoints.

Uses conftest fixtures:
  - _init_test_db: autouse, fresh SQLite DB per test
  - client: httpx.AsyncClient wired to app via ASGITransport
  - seeded_match: match with deliveries for innings 1 and 2
"""

import pytest

from app.engine.precompute import precompute_match_context
from app.storage.database import insert_commentary

# --------------------------------------------------------------------------- #
#  Sample data
# --------------------------------------------------------------------------- #

BULK_DELIVERIES_INNINGS_1 = {
    "innings": 1,
    "deliveries": [
        {"over": 0, "ball": 1, "batter": "Batter A", "bowler": "Bowler X",
         "runs": 1, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False},
        {"over": 0, "ball": 2, "batter": "Batter B", "bowler": "Bowler X",
         "runs": 4, "extras": 0, "is_wicket": False, "is_boundary": True, "is_six": False},
        {"over": 0, "ball": 3, "batter": "Batter B", "bowler": "Bowler X",
         "runs": 0, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False},
        {"over": 0, "ball": 4, "batter": "Batter A", "bowler": "Bowler X",
         "runs": 6, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": True},
        {"over": 0, "ball": 5, "batter": "Batter A", "bowler": "Bowler X",
         "runs": 0, "extras": 0, "is_wicket": True, "wicket_type": "bowled",
         "is_boundary": False, "is_six": False},
        {"over": 0, "ball": 6, "batter": "Batter C", "bowler": "Bowler X",
         "runs": 2, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False},
    ],
}

BULK_DELIVERIES_INNINGS_2 = {
    "innings": 2,
    "deliveries": [
        {"over": 0, "ball": 1, "batter": "Batter D", "bowler": "Bowler Z",
         "runs": 2, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False},
        {"over": 0, "ball": 2, "batter": "Batter E", "bowler": "Bowler Z",
         "runs": 0, "extras": 0, "is_wicket": False, "is_boundary": False, "is_six": False},
    ],
}


# --------------------------------------------------------------------------- #
#  Tests
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_match_lifecycle(client):
    """POST create match, GET list (verify in list), GET detail, PATCH update title, DELETE, GET returns 404."""
    # Create
    r = await client.post("/api/matches", json={
        "title": "Lifecycle Test",
        "match_info": {"venue": "Test Ground"},
        "languages": ["hi"],
    })
    assert r.status_code == 201
    created = r.json()
    match_id = created["match_id"]

    # List
    r = await client.get("/api/matches")
    assert r.status_code == 200
    matches = r.json()
    found = next((m for m in matches if m["match_id"] == match_id), None)
    assert found is not None
    assert found["title"] == "Lifecycle Test"

    # Detail
    r = await client.get(f"/api/matches/{match_id}")
    assert r.status_code == 200
    assert r.json()["title"] == "Lifecycle Test"

    # PATCH
    r = await client.patch(f"/api/matches/{match_id}", json={"title": "Updated Title"})
    assert r.status_code == 200
    assert r.json()["title"] == "Updated Title"

    # DELETE
    r = await client.delete(f"/api/matches/{match_id}")
    assert r.status_code == 200
    body = r.json()
    assert "commentaries_deleted" in body
    assert "deliveries_deleted" in body
    assert body["match_deleted"] == 1

    # 404
    r = await client.get(f"/api/matches/{match_id}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delivery_single(client):
    """Create match, POST single delivery, verify 201, GET delivery by id."""
    r = await client.post("/api/matches", json={
        "title": "Single Delivery Test",
        "match_info": {"innings_summary": [
            {"innings_number": 1, "batting_team": "Team A", "bowling_team": "Team B"},
        ]},
    })
    assert r.status_code == 201
    match_id = r.json()["match_id"]

    r = await client.post(
        f"/api/matches/{match_id}/deliveries",
        json={
            "innings": 1,
            "ball_index": 0,
            "over": 0,
            "ball": 1,
            "batter": "Batter A",
            "bowler": "Bowler X",
            "runs": 4,
            "extras": 0,
            "is_wicket": False,
            "is_boundary": True,
            "is_six": False,
        },
    )
    assert r.status_code == 201
    body = r.json()
    ball_id = body["ball_id"]
    assert "context_computed" in body

    r = await client.get(f"/api/deliveries/{ball_id}")
    assert r.status_code == 200
    d = r.json()
    assert d["batter"] == "Batter A"
    assert d["bowler"] == "Bowler X"
    assert d["runs"] == 4
    assert d["is_boundary"] is True


@pytest.mark.asyncio
async def test_delivery_bulk(client):
    """Create match, POST bulk deliveries (6 deliveries for one over), verify count, GET deliveries list."""
    r = await client.post("/api/matches", json={
        "title": "Bulk Delivery Test",
        "match_info": {"innings_summary": [
            {"innings_number": 1, "batting_team": "Team A", "bowling_team": "Team B"},
        ]},
    })
    assert r.status_code == 201
    match_id = r.json()["match_id"]

    r = await client.post(
        f"/api/matches/{match_id}/deliveries/bulk",
        json=BULK_DELIVERIES_INNINGS_1,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["deliveries_inserted"] == 6
    assert "context_computed" in body

    r = await client.get(f"/api/matches/{match_id}/deliveries")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 6
    assert len(data["deliveries"]) == 6


@pytest.mark.asyncio
async def test_get_deliveries_filter_by_innings(client):
    """Create match, POST bulk for innings 1 AND innings 2, GET with innings filter."""
    r = await client.post("/api/matches", json={
        "title": "Filter Test",
        "match_info": {"innings_summary": [
            {"innings_number": 1, "batting_team": "Team A", "bowling_team": "Team B"},
            {"innings_number": 2, "batting_team": "Team B", "bowling_team": "Team A"},
        ]},
    })
    assert r.status_code == 201
    match_id = r.json()["match_id"]

    await client.post(f"/api/matches/{match_id}/deliveries/bulk", json=BULK_DELIVERIES_INNINGS_1)
    await client.post(f"/api/matches/{match_id}/deliveries/bulk", json=BULK_DELIVERIES_INNINGS_2)

    r = await client.get(f"/api/matches/{match_id}/deliveries?innings=1")
    assert r.status_code == 200
    assert r.json()["total"] == 6
    assert all(d["innings"] == 1 for d in r.json()["deliveries"])

    r = await client.get(f"/api/matches/{match_id}/deliveries?innings=2")
    assert r.status_code == 200
    assert r.json()["total"] == 2
    assert all(d["innings"] == 2 for d in r.json()["deliveries"])


@pytest.mark.asyncio
async def test_innings_summary(client):
    """Create match, POST bulk deliveries, GET innings summary, verify batting/bowling data."""
    r = await client.post("/api/matches", json={
        "title": "Summary Test",
        "match_info": {"innings_summary": [
            {"innings_number": 1, "batting_team": "Team A", "bowling_team": "Team B"},
        ]},
    })
    assert r.status_code == 201
    match_id = r.json()["match_id"]

    await client.post(f"/api/matches/{match_id}/deliveries/bulk", json=BULK_DELIVERIES_INNINGS_1)

    r = await client.get(f"/api/matches/{match_id}/innings/1/summary")
    assert r.status_code == 200
    s = r.json()
    assert "batting_team" in s or "batters" in s or "bowlers" in s or "total_runs" in s


@pytest.mark.asyncio
async def test_innings_stats_endpoints(client, seeded_match):
    """Use seeded_match, call precompute, then verify batters, bowlers, fall-of-wickets, partnerships, innings."""
    match_id = seeded_match["match_id"]

    await precompute_match_context(match_id)

    r = await client.get(f"/api/matches/{match_id}/innings/1/batters")
    assert r.status_code == 200
    assert "batters" in r.json()
    assert len(r.json()["batters"]) > 0

    r = await client.get(f"/api/matches/{match_id}/innings/1/bowlers")
    assert r.status_code == 200
    assert "bowlers" in r.json()
    assert len(r.json()["bowlers"]) > 0

    r = await client.get(f"/api/matches/{match_id}/innings/1/fall-of-wickets")
    assert r.status_code == 200
    assert "fall_of_wickets" in r.json()

    r = await client.get(f"/api/matches/{match_id}/innings/1/partnerships")
    assert r.status_code == 200
    assert "partnerships" in r.json()

    r = await client.get(f"/api/matches/{match_id}/innings")
    assert r.status_code == 200
    data = r.json()
    assert "innings" in data
    assert len(data["innings"]) >= 1


@pytest.mark.asyncio
async def test_timeline(client):
    """Create match with bulk deliveries, GET timeline, verify structure has innings with deliveries."""
    r = await client.post("/api/matches", json={
        "title": "Timeline Test",
        "match_info": {"innings_summary": [
            {"innings_number": 1, "batting_team": "Team A", "bowling_team": "Team B"},
            {"innings_number": 2, "batting_team": "Team B", "bowling_team": "Team A"},
        ]},
    })
    assert r.status_code == 201
    match_id = r.json()["match_id"]

    await client.post(f"/api/matches/{match_id}/deliveries/bulk", json=BULK_DELIVERIES_INNINGS_1)
    await client.post(f"/api/matches/{match_id}/deliveries/bulk", json=BULK_DELIVERIES_INNINGS_2)

    r = await client.get(f"/api/matches/{match_id}/timeline")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "innings_summary" in data
    # Should have items: structural events + balls
    items = data["items"]
    assert len(items) > 0
    # Check that ball items have ball_info
    ball_items = [i for i in items if i["type"] == "ball"]
    assert len(ball_items) >= 7  # 6 from inn1 + 1 from inn2
    for bi in ball_items:
        assert bi["ball_info"] is not None
    # Check structural events exist
    event_items = [i for i in items if i["type"] == "event"]
    assert len(event_items) > 0


@pytest.mark.asyncio
async def test_commentary_crud(client):
    """Create match, create delivery, insert commentary via DB, GET commentaries, GET single, DELETE."""
    r = await client.post("/api/matches", json={
        "title": "Commentary Test",
        "match_info": {"innings_summary": [
            {"innings_number": 1, "batting_team": "Team A", "bowling_team": "Team B"},
        ]},
    })
    assert r.status_code == 201
    match_id = r.json()["match_id"]

    r = await client.post(
        f"/api/matches/{match_id}/deliveries",
        json={
            "innings": 1,
            "over": 0,
            "ball": 1,
            "batter": "Batter A",
            "bowler": "Bowler X",
            "runs": 4,
            "is_wicket": False,
            "is_boundary": True,
            "is_six": False,
        },
    )
    assert r.status_code == 201
    ball_id = r.json()["ball_id"]

    await insert_commentary(
        match_id=match_id,
        ball_id=ball_id,
        seq=2,
        event_type="delivery",
        language="hi",
        text="That's a boundary!",
        audio_url=None,
        data={},
    )

    r = await client.get(f"/api/matches/{match_id}/commentaries?after_seq=0&language=hi")
    assert r.status_code == 200
    data = r.json()
    assert "commentaries" in data
    assert len(data["commentaries"]) >= 1
    # Find the delivery commentary we inserted (skeletons also have text now)
    commentary_with_text = [c for c in data["commentaries"] if c.get("text") == "That's a boundary!"]
    assert len(commentary_with_text) >= 1
    c = commentary_with_text[0]
    commentary_id = c["id"]

    r = await client.get(f"/api/commentaries/{commentary_id}")
    assert r.status_code == 200
    assert r.json()["text"] == "That's a boundary!"

    r = await client.delete(f"/api/matches/{match_id}/commentaries")
    assert r.status_code == 200
    assert r.json()["deleted"] >= 1


@pytest.mark.asyncio
async def test_languages(client):
    """GET /api/languages, verify it returns a list."""
    r = await client.get("/api/languages")
    assert r.status_code == 200
    langs = r.json()
    assert isinstance(langs, list)
    assert len(langs) > 0
    for item in langs:
        assert "code" in item
        assert "name" in item


@pytest.mark.asyncio
async def test_404_match(client):
    """GET /api/matches/99999, verify 404."""
    r = await client.get("/api/matches/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_404_delivery(client):
    """GET /api/deliveries/99999, verify 404."""
    r = await client.get("/api/deliveries/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_404_commentary(client):
    """GET /api/commentaries/99999, verify 404."""
    r = await client.get("/api/commentaries/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_full_match(client):
    """Create match, insert deliveries (innings 1+2), precompute, insert commentary, GET full, verify sections."""
    r = await client.post("/api/matches", json={
        "title": "Full Match Test",
        "match_info": {
            "innings_summary": [
                {"innings_number": 1, "batting_team": "Team A", "bowling_team": "Team B"},
                {"innings_number": 2, "batting_team": "Team B", "bowling_team": "Team A"},
            ],
            "target": 20,
        },
    })
    assert r.status_code == 201
    match_id = r.json()["match_id"]

    await client.post(f"/api/matches/{match_id}/deliveries/bulk", json=BULK_DELIVERIES_INNINGS_1)
    await client.post(f"/api/matches/{match_id}/deliveries/bulk", json=BULK_DELIVERIES_INNINGS_2)

    await precompute_match_context(match_id)

    # Insert a commentary
    deliveries = await client.get(f"/api/matches/{match_id}/deliveries")
    ball_id = deliveries.json()["deliveries"][0]["id"]
    await insert_commentary(
        match_id=match_id,
        ball_id=ball_id,
        seq=1,
        event_type="delivery",
        language="hi",
        text="First ball commentary",
        audio_url=None,
        data={},
    )

    r = await client.get(f"/api/matches/{match_id}/full")
    assert r.status_code == 200
    data = r.json()

    # Top-level keys
    assert "match" in data
    assert "innings" in data
    assert "deliveries" in data
    assert "commentaries" in data
    assert "summary" in data

    # Match
    assert data["match"]["match_id"] == match_id

    # Deliveries
    assert len(data["deliveries"]) == 8  # 6 + 2

    # Commentaries
    assert len(data["commentaries"]) >= 1

    # Summary
    assert data["summary"]["total_deliveries"] == 8
    assert data["summary"]["total_commentaries"] >= 1
    assert "innings_summary" in data["summary"]

    # Innings enriched with stats
    for inn in data["innings"]:
        assert "batters" in inn
        assert "bowlers" in inn
        assert "fall_of_wickets" in inn
        assert "partnerships" in inn

    # Players
    assert "players" in data


# --------------------------------------------------------------------------- #
#  Match Players
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_match_players_lifecycle(client):
    """POST players, GET (all + filter by team), DELETE."""
    r = await client.post("/api/matches", json={
        "title": "Players Test",
        "match_info": {},
    })
    assert r.status_code == 201
    match_id = r.json()["match_id"]

    # Add players
    r = await client.post(f"/api/matches/{match_id}/players", json={
        "players": [
            {"player_name": "Rohit Sharma", "team": "India", "is_captain": True, "is_keeper": False},
            {"player_name": "Virat Kohli", "team": "India"},
            {"player_name": "Rishabh Pant", "team": "India", "is_keeper": True},
            {"player_name": "Aiden Markram", "team": "South Africa", "is_captain": True},
            {"player_name": "David Miller", "team": "South Africa"},
        ],
    })
    assert r.status_code == 201
    assert r.json()["players_upserted"] == 5

    # Get all
    r = await client.get(f"/api/matches/{match_id}/players")
    assert r.status_code == 200
    assert len(r.json()["players"]) == 5

    # Filter by team
    r = await client.get(f"/api/matches/{match_id}/players?team=India")
    assert r.status_code == 200
    india = r.json()["players"]
    assert len(india) == 3
    assert any(p["is_captain"] for p in india)

    r = await client.get(f"/api/matches/{match_id}/players?team=South Africa")
    assert r.status_code == 200
    assert len(r.json()["players"]) == 2

    # Delete
    r = await client.delete(f"/api/matches/{match_id}/players")
    assert r.status_code == 200
    assert r.json()["deleted"] == 5

    # Verify empty
    r = await client.get(f"/api/matches/{match_id}/players")
    assert r.status_code == 200
    assert len(r.json()["players"]) == 0


@pytest.mark.asyncio
async def test_delivery_with_player_ids(client):
    """POST delivery with batter_id, non_batter_id, bowler_id; verify in GET."""
    r = await client.post("/api/matches", json={
        "title": "ID Delivery Test",
        "match_info": {"innings_summary": [
            {"innings_number": 1, "batting_team": "Team A", "bowling_team": "Team B"},
        ]},
    })
    match_id = r.json()["match_id"]

    r = await client.post(f"/api/matches/{match_id}/deliveries", json={
        "innings": 1,
        "over": 0,
        "ball": 1,
        "batter": "Batter A",
        "bowler": "Bowler X",
        "runs": 4,
        "is_boundary": True,
        "is_wicket": False,
        "is_six": False,
        "batter_id": 10,
        "non_batter_id": 20,
        "bowler_id": 30,
    })
    assert r.status_code == 201
    ball_id = r.json()["ball_id"]

    r = await client.get(f"/api/deliveries/{ball_id}")
    assert r.status_code == 200
    d = r.json()
    assert d["batter_id"] == 10
    assert d["non_batter_id"] == 20
    assert d["bowler_id"] == 30


@pytest.mark.asyncio
async def test_create_match_with_players(client):
    """POST /api/matches with players list, verify players are created and returned."""
    r = await client.post("/api/matches", json={
        "title": "Match With Players",
        "match_info": {},
        "players": [
            {"player_name": "Rohit", "team": "India"},
            {"player_name": "Kohli", "team": "India"},
            {"player_name": "de Kock", "team": "South Africa"},
        ],
    })
    assert r.status_code == 201
    body = r.json()
    match_id = body["match_id"]

    # Players should be returned in the creation response
    assert "players" in body
    assert len(body["players"]) == 3

    # Verify via GET
    r = await client.get(f"/api/matches/{match_id}/players")
    assert r.status_code == 200
    players = r.json()["players"]
    assert len(players) == 3
    names = {p["player_name"] for p in players}
    assert names == {"Rohit", "Kohli", "de Kock"}


@pytest.mark.asyncio
async def test_non_batter_populated_by_precompute(client):
    """Insert deliveries with non_batter, precompute, verify non_batter is on delivery rows."""
    r = await client.post("/api/matches", json={
        "title": "Non-batter Test",
        "match_info": {
            "innings_summary": [
                {"innings_number": 1, "batting_team": "Team A", "bowling_team": "Team B"},
            ],
        },
        "players": [
            {"player_name": "Batter A", "team": "Team A"},
            {"player_name": "Batter B", "team": "Team A"},
            {"player_name": "Bowler X", "team": "Team B"},
        ],
    })
    assert r.status_code == 201
    match_id = r.json()["match_id"]

    # Insert with non_batter
    r = await client.post(f"/api/matches/{match_id}/deliveries/bulk", json={
        "innings": 1,
        "deliveries": [
            {"over": 0, "ball": 1, "batter": "Batter A", "bowler": "Bowler X",
             "runs": 1, "non_batter": "Batter B"},
            {"over": 0, "ball": 2, "batter": "Batter B", "bowler": "Bowler X",
             "runs": 4, "is_boundary": True, "non_batter": "Batter A"},
        ],
    })
    assert r.status_code == 201

    # After precompute (runs automatically), non_batter should be populated
    r = await client.get(f"/api/matches/{match_id}/deliveries?innings=1")
    assert r.status_code == 200
    deliveries = r.json()["deliveries"]
    assert len(deliveries) == 2

    # non_batter should be populated (either from insert or precompute)
    d0 = deliveries[0]
    assert d0["non_batter"] == "Batter B"

    d1 = deliveries[1]
    assert d1["non_batter"] == "Batter A"

    # Player IDs should be resolved by precompute
    assert d0["batter_id"] is not None
    assert d0["bowler_id"] is not None
