"""
Integration test using real match data (IND vs SA T20 WC 2024 Final).

Loads the actual JSON file through the same code path as load_match.py,
then checks first few rows of every table to ensure correctness.
"""

import json
from pathlib import Path

import pytest

from app.storage import database as db
from app.engine.precompute import precompute_match_context
from scripts.load_match import build_match_info, extract_players

MATCH_JSON = Path(__file__).resolve().parent.parent / "data" / "sample" / "ind_vs_sa_final.json"


@pytest.fixture
def raw_data() -> dict:
    with open(MATCH_JSON) as f:
        return json.load(f)


@pytest.fixture
async def loaded_match(raw_data) -> dict:
    """
    Seed the real match into the test DB via the same logic as load_match.py.
    Returns {"match_id": int, "raw": raw_data}.
    """
    innings_data = raw_data["innings"]
    match_info = build_match_info(raw_data)
    title = match_info.get("title", "IND vs SA Final")

    match = await db.create_match(
        title=title,
        match_info=match_info,
        languages=["hi"],
        venue=match_info.get("venue"),
        format=match_info.get("format"),
        team1=match_info.get("teams", ["", ""])[0] if "teams" in match_info else None,
        team2=match_info.get("teams", ["", ""])[1] if "teams" in match_info else None,
    )
    mid = match["match_id"]

    # Extract and register players (same as updated load_match.py)
    player_list = extract_players(innings_data)
    if player_list:
        await db.upsert_match_players_bulk(mid, player_list)

    # Build player name → ID lookup
    player_rows = await db.get_match_players(mid)
    player_lookup = {p["player_name"]: p["id"] for p in player_rows}

    for inn in innings_data:
        innings_num = inn.get("innings_number", 1)
        batting_team = inn.get("batting_team", "")
        bowling_team = inn.get("bowling_team", "")
        raw_balls = inn["balls"]

        # Map batsman→batter, resolve IDs (same as load_match.py)
        deliveries = []
        for b in raw_balls:
            d = dict(b)
            if "batsman" in d:
                d["batter"] = d.pop("batsman")
            if "dismissal_batsman" in d:
                d["dismissal_batter"] = d.pop("dismissal_batsman")
            # Resolve player IDs
            d["batter_id"] = player_lookup.get(d.get("batter", ""))
            d["bowler_id"] = player_lookup.get(d.get("bowler", ""))
            deliveries.append(d)

        await db.insert_deliveries_bulk(mid, innings_num, deliveries)

    # Run precompute — populates context, snapshot, batters, bowlers, FOW, innings, partnerships, non_batter + IDs
    await precompute_match_context(mid)

    return {"match_id": mid, "raw": raw_data}


# =========================================================================== #
#  matches table
# =========================================================================== #

async def test_matches_table(loaded_match):
    """Verify match record has correct metadata."""
    mid = loaded_match["match_id"]
    match = await db.get_match(mid)

    assert match is not None
    assert match["title"] == "ICC T20 World Cup 2024 Final"
    assert match["venue"] == "Kensington Oval, Barbados"
    assert match["format"] == "T20"
    assert match["team1"] == "India"
    assert match["team2"] == "South Africa"
    assert match["status"] == "ready"

    # match_info should have innings_summary with both innings
    mi = match["match_info"]
    assert mi["batting_team"] == "South Africa"   # innings 2 team
    assert mi["bowling_team"] == "India"
    assert mi["target"] == 177                     # IND scored 176 + 1

    inn_summary = mi["innings_summary"]
    assert len(inn_summary) == 2
    assert inn_summary[0]["batting_team"] == "India"
    assert inn_summary[0]["total_runs"] == 176
    assert inn_summary[0]["total_wickets"] == 7
    assert inn_summary[1]["batting_team"] == "South Africa"
    assert inn_summary[1]["total_runs"] == 168
    assert inn_summary[1]["total_wickets"] == 8


# =========================================================================== #
#  deliveries table
# =========================================================================== #

async def test_deliveries_table(loaded_match, raw_data):
    """Verify deliveries are correct — count, first few, last, key events."""
    mid = loaded_match["match_id"]

    inn1_raw = raw_data["innings"][0]["balls"]
    inn2_raw = raw_data["innings"][1]["balls"]

    # Correct counts
    inn1 = await db.get_deliveries(mid, 1)
    inn2 = await db.get_deliveries(mid, 2)
    assert len(inn1) == len(inn1_raw), f"Innings 1: expected {len(inn1_raw)}, got {len(inn1)}"
    assert len(inn2) == len(inn2_raw), f"Innings 2: expected {len(inn2_raw)}, got {len(inn2)}"

    total = await db.count_deliveries(mid)
    assert total == len(inn1_raw) + len(inn2_raw)

    # First delivery of innings 1: Rohit 1 run off Marco Jansen, over 0 ball 1
    d0 = inn1[0]
    assert d0["over"] == 0
    assert d0["ball"] == 1
    assert d0["batter"] == "Rohit"
    assert d0["bowler"] == "Marco Jansen"
    assert d0["runs"] == 1
    assert d0["is_wicket"] is False
    assert d0["is_boundary"] is False

    # Second delivery: Kohli FOUR off Jansen
    d1 = inn1[1]
    assert d1["batter"] == "Kohli"
    assert d1["bowler"] == "Marco Jansen"
    assert d1["runs"] == 4
    assert d1["is_boundary"] is True

    # First wicket in innings 1 — find it
    first_wicket = next(d for d in inn1 if d["is_wicket"])
    assert first_wicket["over"] == 1
    assert first_wicket["ball"] == 4
    raw_wk = inn1_raw[inn1.index(first_wicket)]
    assert raw_wk["wicket_type"] == "caught"
    # wicket_type is in data JSON
    assert first_wicket["data"].get("wicket_type") == "caught"

    # First delivery of innings 2 (SA chase)
    d2_0 = inn2[0]
    assert d2_0["innings"] == 2
    assert d2_0["over"] == 0
    assert d2_0["ball"] == 1

    # Snapshot columns should be populated (from precompute)
    # Check a delivery mid-innings has reasonable snapshot values
    mid_delivery = inn1[len(inn1) // 2]
    assert mid_delivery["total_runs"] > 0 or mid_delivery["overs_completed"] > 0
    assert mid_delivery["match_phase"] in ("powerplay", "middle", "death")

    # Context should be populated
    assert mid_delivery["context"] is not None
    assert "logic" in mid_delivery["context"]
    assert "branch" in mid_delivery["context"]["logic"]


# =========================================================================== #
#  innings_batters table
# =========================================================================== #

async def test_innings_batters_table(loaded_match):
    """Verify batter stats for innings 1 (India batting)."""
    mid = loaded_match["match_id"]

    batters = await db.get_innings_batters(mid, 1)
    assert len(batters) > 0, "No batters found for innings 1"

    names = [b["name"] for b in batters]
    # India's top order
    assert "Rohit" in names, f"Rohit not in batters: {names}"
    assert "Kohli" in names, f"Kohli not in batters: {names}"

    # Kohli scored 76 in the real match — he should have the most runs
    kohli = next(b for b in batters if b["name"] == "Kohli")
    assert kohli["runs"] == 76, f"Kohli runs: {kohli['runs']}, expected 76"
    assert kohli["balls_faced"] > 0
    assert kohli["fours"] > 0
    assert kohli["strike_rate"] is not None
    assert kohli["strike_rate"] > 0

    # Rohit scored 9 — got out early
    rohit = next(b for b in batters if b["name"] == "Rohit")
    assert rohit["runs"] == 9, f"Rohit runs: {rohit['runs']}, expected 9"
    assert rohit["is_out"] == 1

    # Innings 2 — SA batting
    sa_batters = await db.get_innings_batters(mid, 2)
    assert len(sa_batters) > 0
    sa_names = [b["name"] for b in sa_batters]
    assert "de Kock" in sa_names or "Quinton de Kock" in sa_names or any("Kock" in n for n in sa_names), \
        f"de Kock not found in SA batters: {sa_names}"


# =========================================================================== #
#  innings_bowlers table
# =========================================================================== #

async def test_innings_bowlers_table(loaded_match):
    """Verify bowler stats for innings 1 (SA bowling)."""
    mid = loaded_match["match_id"]

    bowlers = await db.get_innings_bowlers(mid, 1)
    assert len(bowlers) > 0, "No bowlers found for innings 1"

    names = [b["name"] for b in bowlers]
    assert "Marco Jansen" in names, f"Jansen not in bowlers: {names}"

    jansen = next(b for b in bowlers if b["name"] == "Marco Jansen")
    assert jansen["balls_bowled"] > 0
    assert jansen["runs_conceded"] > 0
    assert jansen["economy"] is not None
    assert jansen["economy"] > 0

    # Innings 2 — India bowling (Bumrah, Arshdeep etc.)
    ind_bowlers = await db.get_innings_bowlers(mid, 2)
    assert len(ind_bowlers) > 0
    ind_names = [b["name"] for b in ind_bowlers]
    # Bumrah or Arshdeep should be there
    assert any("Bumrah" in n or "Arshdeep" in n for n in ind_names), \
        f"No India bowlers found: {ind_names}"


# =========================================================================== #
#  fall_of_wickets table
# =========================================================================== #

async def test_fall_of_wickets_table(loaded_match):
    """Verify FOW for innings 1 — India lost 7 wickets."""
    mid = loaded_match["match_id"]

    fow = await db.get_fall_of_wickets(mid, 1)
    assert len(fow) == 7, f"Expected 7 FOW in innings 1, got {len(fow)}"

    # Wickets should be numbered 1-7
    wicket_nums = [w["wicket_number"] for w in fow]
    assert wicket_nums == list(range(1, 8))

    # First wicket: Rohit caught (over 1.4, team score around 23)
    w1 = fow[0]
    assert w1["wicket_number"] == 1
    assert w1["batter"] == "Rohit"
    assert w1["batter_runs"] == 9
    assert w1["how"] == "caught"
    assert w1["team_score"] > 0

    # SA innings 2 — lost 8 wickets
    fow2 = await db.get_fall_of_wickets(mid, 2)
    assert len(fow2) == 8, f"Expected 8 FOW in innings 2, got {len(fow2)}"


# =========================================================================== #
#  innings table
# =========================================================================== #

async def test_innings_table(loaded_match):
    """Verify innings summary records."""
    mid = loaded_match["match_id"]

    all_innings = await db.get_innings(mid)
    assert len(all_innings) == 2, f"Expected 2 innings records, got {len(all_innings)}"

    inn1 = await db.get_innings(mid, 1)
    assert inn1 is not None
    assert inn1["batting_team"] == "India"
    assert inn1["bowling_team"] == "South Africa"
    assert inn1["total_runs"] == 176
    assert inn1["total_wickets"] == 7

    inn2 = await db.get_innings(mid, 2)
    assert inn2 is not None
    assert inn2["batting_team"] == "South Africa"
    assert inn2["bowling_team"] == "India"
    assert inn2["total_runs"] == 168
    assert inn2["total_wickets"] == 8


# =========================================================================== #
#  partnerships table
# =========================================================================== #

async def test_partnerships_table(loaded_match):
    """Verify partnerships for innings 1."""
    mid = loaded_match["match_id"]

    partnerships = await db.get_partnerships(mid, 1)
    assert len(partnerships) > 0, "No partnerships found for innings 1"

    # First partnership (opening stand between Rohit and Kohli)
    p1 = partnerships[0]
    assert p1["wicket_number"] == 1
    # One of the batters should be Rohit or Kohli
    pair = {p1["batter1"], p1["batter2"]}
    assert "Rohit" in pair or "Kohli" in pair, f"Opening pair: {pair}"
    assert p1["runs"] > 0

    # SA partnerships
    sa_partnerships = await db.get_partnerships(mid, 2)
    assert len(sa_partnerships) > 0


# =========================================================================== #
#  deliveries context column
# =========================================================================== #

async def test_delivery_context_structure(loaded_match):
    """Verify the context JSON has expected shape on a few deliveries."""
    mid = loaded_match["match_id"]

    deliveries = await db.get_deliveries(mid, 2)
    assert len(deliveries) > 0

    # Check first delivery context
    d = deliveries[0]
    ctx = d["context"]
    assert ctx is not None, "First delivery of innings 2 should have context"

    # Context should have these top-level keys (from precompute)
    expected_keys = {"logic", "event_description"}
    present_keys = set(ctx.keys())
    for k in expected_keys:
        assert k in present_keys, f"Missing key '{k}' in context. Keys: {present_keys}"

    # Branch is nested under logic
    assert "branch" in ctx["logic"]
    valid_branches = {"routine", "boundary_momentum", "wicket_drama", "pressure_builder", "over_transition", "extra_gift"}
    assert ctx["logic"]["branch"] in valid_branches, f"Invalid branch: {ctx['logic']['branch']}"

    # Other expected context keys
    assert "tracking" in ctx        # match state tracking
    assert "narratives" in ctx      # narrative triggers (list)
    assert "match_over" in ctx      # boolean

    # Check a mid-match delivery too
    mid_d = deliveries[len(deliveries) // 2]
    assert mid_d["context"] is not None
    assert mid_d["context"]["logic"]["branch"] in valid_branches


# =========================================================================== #
#  match_players table + player IDs on deliveries
# =========================================================================== #

async def test_match_players_populated(loaded_match):
    """Verify match_players were extracted and inserted."""
    mid = loaded_match["match_id"]

    players = await db.get_match_players(mid)
    assert len(players) > 0, "No match_players found"

    india = await db.get_match_players(mid, team="India")
    sa = await db.get_match_players(mid, team="South Africa")
    assert len(india) > 0, "No India players"
    assert len(sa) > 0, "No SA players"

    # Rohit, Kohli should be India players
    india_names = {p["player_name"] for p in india}
    assert "Rohit" in india_names
    assert "Kohli" in india_names

    # Marco Jansen should be SA player
    sa_names = {p["player_name"] for p in sa}
    assert "Marco Jansen" in sa_names


async def test_delivery_player_ids_populated(loaded_match):
    """Verify batter_id, bowler_id are populated on deliveries after precompute."""
    mid = loaded_match["match_id"]

    inn1 = await db.get_deliveries(mid, 1)
    assert len(inn1) > 0

    # First delivery: Rohit off Marco Jansen — both should have IDs
    d0 = inn1[0]
    assert d0["batter_id"] is not None, "batter_id should be populated"
    assert d0["bowler_id"] is not None, "bowler_id should be populated"

    # non_batter should be populated by precompute (StateManager infers it)
    # For the first ball, there's no explicit non_batter in JSON,
    # but by ball 2 the StateManager should have inferred it
    d1 = inn1[1]
    assert d1["non_batter"] is not None, "non_batter should be populated by precompute"
    assert d1["non_batter_id"] is not None, "non_batter_id should be resolved from match_players"


# =========================================================================== #
#  Cross-table consistency
# =========================================================================== #

async def test_cross_table_consistency(loaded_match, raw_data):
    """
    Verify data is consistent across tables:
    - Total runs from deliveries matches innings table
    - Wicket count matches FOW count
    - Batter runs sum up correctly
    """
    mid = loaded_match["match_id"]

    for innings_num in (1, 2):
        deliveries = await db.get_deliveries(mid, innings_num)
        inn_record = await db.get_innings(mid, innings_num)
        fow = await db.get_fall_of_wickets(mid, innings_num)
        batters = await db.get_innings_batters(mid, innings_num)

        # Delivery runs should sum to innings total
        total_from_deliveries = sum(d["runs"] + d["extras"] for d in deliveries)
        assert total_from_deliveries == inn_record["total_runs"], \
            f"Innings {innings_num}: deliveries sum {total_from_deliveries} != innings total {inn_record['total_runs']}"

        # FOW count should match innings wickets
        assert len(fow) == inn_record["total_wickets"], \
            f"Innings {innings_num}: FOW count {len(fow)} != innings wickets {inn_record['total_wickets']}"

        # Wicket count from deliveries
        wickets_from_deliveries = sum(1 for d in deliveries if d["is_wicket"])
        assert wickets_from_deliveries == inn_record["total_wickets"], \
            f"Innings {innings_num}: delivery wickets {wickets_from_deliveries} != {inn_record['total_wickets']}"

        # Batter runs should sum to total_runs (minus extras)
        batter_runs_total = sum(b["runs"] for b in batters)
        extras_total = sum(d["extras"] for d in deliveries)
        expected_batter_runs = inn_record["total_runs"] - extras_total
        assert batter_runs_total == expected_batter_runs, \
            f"Innings {innings_num}: batter runs {batter_runs_total} != {expected_batter_runs} (total {inn_record['total_runs']} - extras {extras_total})"
