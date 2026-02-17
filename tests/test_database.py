"""
Comprehensive unit tests for the database layer.

Uses pytest and pytest_asyncio. The conftest provides _init_test_db (autouse)
that creates a fresh SQLite DB for each test. Tests create data directly
except test_delete_match_cascades which uses seeded_match.
"""

import pytest
import pytest_asyncio

from app.storage import database as db


# --------------------------------------------------------------------------- #
#  Matches
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_and_get_match():
    """Create match with all fields (venue, format, team1, team2, match_date), retrieve and verify."""
    match_info = {"target": 150, "batting_team": "India", "bowling_team": "South Africa"}
    created = await db.create_match(
        title="India vs South Africa Final",
        match_info=match_info,
        languages=["hi", "en"],
        status="ready",
        venue="Kensington Oval",
        format="T20",
        team1="India",
        team2="South Africa",
        match_date="2024-06-29",
    )
    assert "match_id" in created
    assert created["title"] == "India vs South Africa Final"
    assert created["match_info"] == match_info
    assert created["languages"] == ["hi", "en"]
    assert created["status"] == "ready"
    assert created["venue"] == "Kensington Oval"
    assert created["format"] == "T20"
    assert created["team1"] == "India"
    assert created["team2"] == "South Africa"
    assert created["match_date"] == "2024-06-29"
    assert "created_at" in created

    retrieved = await db.get_match(created["match_id"])
    assert retrieved is not None
    assert retrieved["match_id"] == created["match_id"]
    assert retrieved["title"] == created["title"]
    assert retrieved["venue"] == created["venue"]
    assert retrieved["format"] == created["format"]
    assert retrieved["team1"] == created["team1"]
    assert retrieved["team2"] == created["team2"]
    assert retrieved["match_date"] == created["match_date"]


@pytest.mark.asyncio
async def test_list_matches_by_status():
    """Create 2 matches with different statuses, filter by status."""
    await db.create_match("Match A", {"target": 150}, status="ready")
    await db.create_match("Match B", {"target": 180}, status="running")
    await db.create_match("Match C", {"target": 200}, status="ready")

    all_matches = await db.list_matches()
    assert len(all_matches) >= 3

    ready = await db.list_matches(status="ready")
    assert len(ready) >= 2
    titles = [m["title"] for m in ready]
    assert "Match A" in titles
    assert "Match C" in titles
    assert "Match B" not in titles

    running = await db.list_matches(status="running")
    assert len(running) >= 1
    assert running[0]["title"] == "Match B"


@pytest.mark.asyncio
async def test_update_match():
    """Update title and status, verify."""
    created = await db.create_match("Original Title", {"target": 150}, status="ready")
    mid = created["match_id"]

    updated = await db.update_match(mid, title="Updated Title", status="running")
    assert updated is not None
    assert updated["title"] == "Updated Title"
    assert updated["status"] == "running"

    retrieved = await db.get_match(mid)
    assert retrieved["title"] == "Updated Title"
    assert retrieved["status"] == "running"


# --------------------------------------------------------------------------- #
#  Deliveries
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_insert_and_get_deliveries():
    """Insert single delivery, bulk deliveries, verify get_deliveries, get_all_deliveries, get_delivery_by_id, count_deliveries."""
    match = await db.create_match("Delivery Test", {"target": 150})
    mid = match["match_id"]

    # Single insert
    ball_id = await db.insert_delivery(
        mid, 1, 0, 0, 1, "Batter A", "Bowler X", {}, runs=4
    )
    assert ball_id > 0

    # Bulk insert
    balls = [
        {"over": 0, "ball": 2, "batter": "Batter A", "bowler": "Bowler X", "runs": 1},
        {"over": 0, "ball": 3, "batter": "Batter B", "bowler": "Bowler X", "runs": 4},
    ]
    count = await db.insert_deliveries_bulk(mid, 1, balls)
    assert count == 2

    # get_deliveries for innings 1
    deliveries_1 = await db.get_deliveries(mid, 1)
    assert len(deliveries_1) == 3
    assert deliveries_1[0]["ball"] == 1
    assert deliveries_1[0]["batter"] == "Batter A"
    assert deliveries_1[0]["runs"] == 4

    # Add innings 2
    await db.insert_deliveries_bulk(mid, 2, [{"over": 0, "ball": 1, "batter": "Chaser", "bowler": "Bowler Y", "runs": 0}])

    # get_all_deliveries
    all_del = await db.get_all_deliveries(mid)
    assert len(all_del) == 4
    innings_order = [d["innings"] for d in all_del]
    assert innings_order == [1, 1, 1, 2]

    # get_delivery_by_id
    by_id = await db.get_delivery_by_id(ball_id)
    assert by_id is not None
    assert by_id["id"] == ball_id
    assert by_id["match_id"] == mid
    assert by_id["innings"] == 1

    # count_deliveries
    assert await db.count_deliveries(mid) == 4
    assert await db.count_deliveries(mid, innings=1) == 3
    assert await db.count_deliveries(mid, innings=2) == 1


@pytest.mark.asyncio
async def test_delivery_context_update():
    """Insert delivery, update context, verify it persists."""
    match = await db.create_match("Context Test", {"target": 150})
    mid = match["match_id"]
    ball_id = await db.insert_delivery(
        mid, 1, 0, 0, 1, "Batter A", "Bowler X", {}
    )

    context = {"pressure": "high", "rrr": 12.5, "notes": ["death overs"]}
    await db.update_delivery_context(ball_id, context)

    delivery = await db.get_delivery_by_id(ball_id)
    assert delivery["context"] is not None
    assert delivery["context"]["pressure"] == "high"
    assert delivery["context"]["rrr"] == 12.5
    assert delivery["context"]["notes"] == ["death overs"]


@pytest.mark.asyncio
async def test_delivery_snapshot_update():
    """Insert delivery, update snapshot columns, verify."""
    match = await db.create_match("Snapshot Test", {"target": 150})
    mid = match["match_id"]
    ball_id = await db.insert_delivery(
        mid, 1, 0, 0, 1, "Batter A", "Bowler X", {}
    )

    await db.update_delivery_snapshot(
        ball_id,
        total_runs=45,
        total_wickets=2,
        overs_completed=5,
        balls_in_over=3,
        crr=9.0,
        rrr=10.5,
        runs_needed=106,
        balls_remaining=87,
        match_phase="middle",
    )

    delivery = await db.get_delivery_by_id(ball_id)
    assert delivery["total_runs"] == 45
    assert delivery["total_wickets"] == 2
    assert delivery["overs_completed"] == 5
    assert delivery["balls_in_over"] == 3
    assert delivery["crr"] == 9.0
    assert delivery["rrr"] == 10.5
    assert delivery["runs_needed"] == 106
    assert delivery["balls_remaining"] == 87
    assert delivery["match_phase"] == "middle"


@pytest.mark.asyncio
async def test_delivery_snapshot_with_player_fields():
    """Update snapshot with non_batter + player IDs, verify COALESCE behavior."""
    match = await db.create_match("Snapshot Player Test", {"target": 150})
    mid = match["match_id"]
    ball_id = await db.insert_delivery(
        mid, 1, 0, 0, 1, "Batter A", "Bowler X", {},
        batter_id=10, bowler_id=30,
    )

    # Update with non_batter and non_batter_id
    await db.update_delivery_snapshot(
        ball_id,
        total_runs=5, total_wickets=0, overs_completed=0, balls_in_over=1,
        crr=6.0, rrr=0.0, runs_needed=0, balls_remaining=119,
        match_phase="powerplay",
        non_batter="Batter B",
        non_batter_id=20,
    )

    delivery = await db.get_delivery_by_id(ball_id)
    assert delivery["non_batter"] == "Batter B"
    assert delivery["non_batter_id"] == 20
    # batter_id/bowler_id should remain (COALESCE)
    assert delivery["batter_id"] == 10
    assert delivery["bowler_id"] == 30


# --------------------------------------------------------------------------- #
#  Innings Batters
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_innings_batters_crud():
    """Upsert single + bulk, verify with get, check new columns (strike_rate, out_status, dismissal_info)."""
    match = await db.create_match("Batters Test", {"target": 150})
    mid = match["match_id"]

    # Single upsert
    await db.upsert_innings_batter(
        mid, 1, "Rohit Sharma",
        position=1, runs=50, balls_faced=30, fours=6, sixes=2, dots=8,
        is_out=True, strike_rate=166.67, out_status="c Kohli b Bumrah",
        dismissal_info="Caught at mid-off",
    )

    # Bulk upsert
    batsmen = [
        {"name": "Virat Kohli", "position": 2, "runs": 76, "balls_faced": 59, "fours": 8, "sixes": 2, "dots": 15, "is_out": False, "strike_rate": 128.81},
        {"name": "Suryakumar Yadav", "position": 3, "runs": 31, "balls_faced": 16, "fours": 3, "sixes": 1, "dots": 4, "is_out": True, "out_status": "b Starc", "dismissal_info": "Bowled"},
    ]
    count = await db.upsert_innings_batters_bulk(mid, 1, batsmen)
    assert count == 2

    batters = await db.get_innings_batters(mid, 1)
    assert len(batters) == 3

    rohit = next(b for b in batters if b["name"] == "Rohit Sharma")
    assert rohit["runs"] == 50
    assert rohit["balls_faced"] == 30
    assert rohit["strike_rate"] == 166.67
    assert rohit["out_status"] == "c Kohli b Bumrah"
    assert rohit["dismissal_info"] == "Caught at mid-off"
    assert rohit["is_out"] == 1  # SQLite stores as int

    virat = next(b for b in batters if b["name"] == "Virat Kohli")
    assert virat["strike_rate"] == 128.81
    assert virat["is_out"] == 0


# --------------------------------------------------------------------------- #
#  Innings Bowlers
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_innings_bowlers_crud():
    """Bulk upsert + get, verify economy, overs_bowled columns."""
    match = await db.create_match("Bowlers Test", {"target": 150})
    mid = match["match_id"]

    bowlers = [
        {"name": "Bumrah", "balls_bowled": 24, "runs_conceded": 18, "wickets": 2, "maidens": 0, "dots": 12, "economy": 4.5, "overs_bowled": 4.0},
        {"name": "Arshdeep", "balls_bowled": 24, "runs_conceded": 20, "wickets": 1, "maidens": 0, "dots": 8, "economy": 5.0, "overs_bowled": 4.0},
    ]
    count = await db.upsert_innings_bowlers_bulk(mid, 1, bowlers)
    assert count == 2

    result = await db.get_innings_bowlers(mid, 1)
    assert len(result) == 2
    bumrah = next(b for b in result if b["name"] == "Bumrah")
    assert bumrah["economy"] == 4.5
    assert bumrah["overs_bowled"] == 4.0
    assert bumrah["wickets"] == 2


# --------------------------------------------------------------------------- #
#  Fall of Wickets
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fall_of_wickets_crud():
    """Bulk insert + get, verify batter/batter_runs fields."""
    match = await db.create_match("FOW Test", {"target": 150})
    mid = match["match_id"]

    wickets = [
        {"wicket_number": 1, "batter": "Rohit Sharma", "batter_runs": 9, "team_score": 23, "overs": "2.3", "bowler": "Starc", "how": "c Warner b Starc"},
        {"wicket_number": 2, "batter": "Virat Kohli", "batter_runs": 76, "team_score": 151, "overs": "19.2", "bowler": "Cummins", "how": "b Cummins"},
    ]
    count = await db.insert_fall_of_wickets_bulk(mid, 1, wickets)
    assert count == 2

    fow = await db.get_fall_of_wickets(mid, 1)
    assert len(fow) == 2
    assert fow[0]["batter"] == "Rohit Sharma"
    assert fow[0]["batter_runs"] == 9
    assert fow[0]["team_score"] == 23
    assert fow[1]["batter"] == "Virat Kohli"
    assert fow[1]["batter_runs"] == 76


# --------------------------------------------------------------------------- #
#  Innings
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_innings_crud():
    """Upsert + get innings records (single and all)."""
    match = await db.create_match("Innings Test", {"target": 150})
    mid = match["match_id"]

    await db.upsert_innings(
        mid, 1, "India", "South Africa",
        total_runs=176, total_wickets=7, total_overs=20.0, extras_total=12,
    )
    await db.upsert_innings(
        mid, 2, "South Africa", "India",
        total_runs=0, total_wickets=0, total_overs=None, extras_total=0,
    )

    single = await db.get_innings(mid, 1)
    assert single is not None
    assert single["innings_number"] == 1
    assert single["batting_team"] == "India"
    assert single["bowling_team"] == "South Africa"
    assert single["total_runs"] == 176
    assert single["total_wickets"] == 7
    assert single["total_overs"] == 20.0
    assert single["extras_total"] == 12

    all_innings = await db.get_innings(mid)
    assert len(all_innings) == 2
    assert all_innings[0]["innings_number"] == 1
    assert all_innings[1]["innings_number"] == 2


# --------------------------------------------------------------------------- #
#  Partnerships
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_partnerships_crud():
    """Bulk upsert + get partnerships."""
    match = await db.create_match("Partnerships Test", {"target": 150})
    mid = match["match_id"]

    partnerships = [
        {"wicket_number": 1, "batter1": "Rohit", "batter2": "Kohli", "runs": 92, "balls": 62},
        {"wicket_number": 2, "batter1": "Kohli", "batter2": "SKY", "runs": 59, "balls": 35},
    ]
    count = await db.upsert_partnerships_bulk(mid, 1, partnerships)
    assert count == 2

    result = await db.get_partnerships(mid, 1)
    assert len(result) == 2
    assert result[0]["batter1"] == "Rohit"
    assert result[0]["batter2"] == "Kohli"
    assert result[0]["runs"] == 92
    assert result[0]["balls"] == 62


# --------------------------------------------------------------------------- #
#  Commentaries
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_commentary_crud():
    """Insert, get_after, get_by_id, pending_audio, update_audio, recent_texts, delete."""
    match = await db.create_match("Commentary Test", {"target": 150})
    mid = match["match_id"]
    ball_id = await db.insert_delivery(mid, 1, 0, 0, 1, "Batter A", "Bowler X", {})

    cid1 = await db.insert_commentary(
        mid, ball_id, 1, "delivery", "hi", "चौका! चार रन.", None, {}
    )
    cid2 = await db.insert_commentary(
        mid, ball_id, 2, "delivery", "hi", "एक और रन.", None, {}
    )
    cid3 = await db.insert_commentary(
        mid, None, 3, "phase_change", "hi", "पावरप्ले खत्म.", None, {"type": "phase_change"}
    )

    # get_commentaries_after
    after_0 = await db.get_commentaries_after(mid, 0)
    assert len(after_0) == 3
    after_2 = await db.get_commentaries_after(mid, 2)
    assert len(after_2) == 1
    after_2_lang = await db.get_commentaries_after(mid, 2, language="hi")
    assert len(after_2_lang) == 1

    # get_commentary_by_id
    comm = await db.get_commentary_by_id(cid1)
    assert comm is not None
    assert comm["text"] == "चौका! चार रन."
    assert comm["event_type"] == "delivery"
    assert comm["ball_id"] == ball_id

    # get_commentaries_pending_audio (all have no audio_url)
    pending = await db.get_commentaries_pending_audio(mid)
    assert len(pending) >= 2  # commentaries with language and text
    pending_hi = await db.get_commentaries_pending_audio(mid, language="hi")
    assert len(pending_hi) >= 2

    # update_commentary_audio
    await db.update_commentary_audio(cid1, "https://example.com/audio1.mp3")
    comm_updated = await db.get_commentary_by_id(cid1)
    assert comm_updated["audio_url"] == "https://example.com/audio1.mp3"

    # get_recent_commentary_texts (event_type='delivery' only)
    recent = await db.get_recent_commentary_texts(mid, "hi", limit=6)
    assert "चौका! चार रन." in recent
    assert "एक और रन." in recent
    assert "पावरप्ले खत्म." not in recent  # phase_change, not delivery

    # delete_commentaries
    deleted = await db.delete_commentaries(mid)
    assert deleted == 3
    assert await db.get_commentary_by_id(cid1) is None


# --------------------------------------------------------------------------- #
#  Delete Match Cascades
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_delete_match_cascades(seeded_match):
    """Create match with deliveries, commentaries, batters, bowlers, FOW, innings, partnerships; delete; verify all gone."""
    mid = seeded_match["match_id"]

    # Add the rest: batters, bowlers, FOW, innings, partnerships, commentaries
    await db.upsert_innings_batter(mid, 1, "Batter A", runs=20, balls_faced=12)
    await db.upsert_innings_bowlers_bulk(mid, 1, [{"name": "Bowler X", "balls_bowled": 12, "runs_conceded": 15}])
    await db.insert_fall_of_wickets_bulk(mid, 1, [{"wicket_number": 1, "batter": "Batter B", "batter_runs": 10, "team_score": 50, "overs": "5.2", "bowler": "Bowler X", "how": "bowled"}])
    await db.upsert_innings(mid, 1, "Team A", "Team B", total_runs=150, total_wickets=6)
    await db.upsert_innings(mid, 2, "Team B", "Team A", total_runs=0, total_wickets=0)
    await db.upsert_partnerships_bulk(mid, 1, [{"wicket_number": 1, "batter1": "A", "batter2": "B", "runs": 50, "balls": 30}])

    deliveries = await db.get_deliveries(mid, 1)
    ball_id = deliveries[0]["id"] if deliveries else None
    if ball_id:
        await db.insert_commentary(mid, ball_id, 1, "commentary", "hi", "Test text", None, {})

    result = await db.delete_match(mid)
    assert result["match_deleted"] == 1
    assert result["deliveries_deleted"] > 0
    assert result["commentaries_deleted"] >= (1 if ball_id else 0)

    # Verify all gone
    assert await db.get_match(mid) is None
    assert await db.get_deliveries(mid, 1) == []
    assert await db.get_all_deliveries(mid) == []
    assert await db.get_innings_batters(mid, 1) == []
    assert await db.get_innings_bowlers(mid, 1) == []
    assert await db.get_fall_of_wickets(mid, 1) == []
    assert await db.get_innings(mid, 1) is None
    assert await db.get_innings(mid) == []
    assert await db.get_partnerships(mid, 1) == []
    assert await db.get_commentaries_after(mid, 0) == []


# --------------------------------------------------------------------------- #
#  Max Seq
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_max_seq():
    """Insert commentaries, verify max_seq returns correct value."""
    match = await db.create_match("Max Seq Test", {"target": 150})
    mid = match["match_id"]
    ball_id = await db.insert_delivery(mid, 1, 0, 0, 1, "Batter A", "Bowler X", {})

    assert await db.get_max_seq(mid) == 0

    await db.insert_commentary(mid, ball_id, 10, "commentary", "hi", "Text 1", None, {})
    assert await db.get_max_seq(mid) == 10

    await db.insert_commentary(mid, ball_id, 25, "commentary", "hi", "Text 2", None, {})
    assert await db.get_max_seq(mid) == 25

    await db.insert_commentary(mid, ball_id, 7, "commentary", "hi", "Text 3", None, {})
    assert await db.get_max_seq(mid) == 25  # max is still 25


# --------------------------------------------------------------------------- #
#  Match Players
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_match_players_crud():
    """Bulk upsert + get + delete match players."""
    match = await db.create_match("Players Test", {"target": 150})
    mid = match["match_id"]

    players = [
        {"player_name": "Rohit Sharma", "team": "India", "is_captain": True, "is_keeper": False, "player_status": "Playing XI"},
        {"player_name": "Virat Kohli", "team": "India", "is_captain": False, "is_keeper": False, "player_status": "Playing XI"},
        {"player_name": "Rishabh Pant", "team": "India", "is_captain": False, "is_keeper": True, "player_status": "Playing XI"},
        {"player_name": "Aiden Markram", "team": "South Africa", "is_captain": True, "player_status": "Playing XI"},
        {"player_name": "David Miller", "team": "South Africa", "player_status": "Playing XI"},
    ]
    count = await db.upsert_match_players_bulk(mid, players)
    assert count == 5

    # Get all players
    all_players = await db.get_match_players(mid)
    assert len(all_players) == 5

    # Filter by team
    india = await db.get_match_players(mid, team="India")
    assert len(india) == 3
    rohit = next(p for p in india if p["player_name"] == "Rohit Sharma")
    assert rohit["is_captain"] is True
    assert rohit["is_keeper"] is False
    pant = next(p for p in india if p["player_name"] == "Rishabh Pant")
    assert pant["is_keeper"] is True

    sa = await db.get_match_players(mid, team="South Africa")
    assert len(sa) == 2

    # Upsert update (change captain)
    await db.upsert_match_players_bulk(mid, [
        {"player_name": "Rohit Sharma", "team": "India", "is_captain": False},
    ])
    india2 = await db.get_match_players(mid, team="India")
    rohit2 = next(p for p in india2 if p["player_name"] == "Rohit Sharma")
    assert rohit2["is_captain"] is False

    # Delete
    deleted = await db.delete_match_players(mid)
    assert deleted == 5
    assert await db.get_match_players(mid) == []


@pytest.mark.asyncio
async def test_match_players_with_player_id():
    """Verify player_id (future FK) is stored and returned."""
    match = await db.create_match("Player ID Test", {"target": 150})
    mid = match["match_id"]

    await db.upsert_match_players_bulk(mid, [
        {"player_name": "Kohli", "team": "India", "player_id": 42},
        {"player_name": "Miller", "team": "SA", "player_id": 99},
    ])
    players = await db.get_match_players(mid)
    kohli = next(p for p in players if p["player_name"] == "Kohli")
    assert kohli["player_id"] == 42
    miller = next(p for p in players if p["player_name"] == "Miller")
    assert miller["player_id"] == 99


# --------------------------------------------------------------------------- #
#  Delivery ID columns (batter_id, non_batter_id, bowler_id)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_delivery_player_ids():
    """Insert delivery with batter_id, non_batter_id, bowler_id and verify they persist."""
    match = await db.create_match("Delivery IDs Test", {"target": 150})
    mid = match["match_id"]

    ball_id = await db.insert_delivery(
        mid, 1, 0, 0, 1, "Batter A", "Bowler X", {},
        batter_id=10, non_batter_id=20, bowler_id=30,
    )
    delivery = await db.get_delivery_by_id(ball_id)
    assert delivery["batter_id"] == 10
    assert delivery["non_batter_id"] == 20
    assert delivery["bowler_id"] == 30


@pytest.mark.asyncio
async def test_delivery_player_ids_bulk():
    """Bulk insert deliveries with player IDs and verify."""
    match = await db.create_match("Bulk IDs Test", {"target": 150})
    mid = match["match_id"]

    balls = [
        {"over": 0, "ball": 1, "batter": "A", "bowler": "X", "runs": 0,
         "batter_id": 1, "non_batter_id": 2, "bowler_id": 3},
        {"over": 0, "ball": 2, "batter": "B", "bowler": "X", "runs": 4,
         "batter_id": 2, "non_batter_id": 1, "bowler_id": 3},
    ]
    count = await db.insert_deliveries_bulk(mid, 1, balls)
    assert count == 2

    deliveries = await db.get_deliveries(mid, 1)
    assert deliveries[0]["batter_id"] == 1
    assert deliveries[0]["non_batter_id"] == 2
    assert deliveries[0]["bowler_id"] == 3
    assert deliveries[1]["batter_id"] == 2
    assert deliveries[1]["non_batter_id"] == 1


@pytest.mark.asyncio
async def test_delivery_player_ids_nullable():
    """IDs are nullable — deliveries without IDs should work fine."""
    match = await db.create_match("Nullable IDs Test", {"target": 150})
    mid = match["match_id"]

    ball_id = await db.insert_delivery(
        mid, 1, 0, 0, 1, "Batter A", "Bowler X", {},
    )
    delivery = await db.get_delivery_by_id(ball_id)
    assert delivery["batter_id"] is None
    assert delivery["non_batter_id"] is None
    assert delivery["bowler_id"] is None
