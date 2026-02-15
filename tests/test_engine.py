"""
Unit tests for the engine logic: StateManager, LogicEngine, and precompute.
"""

import pytest

from app.models import BallEvent, NarrativeBranch
from app.engine.state_manager import StateManager
from app.engine.logic_engine import LogicEngine
from app.engine.precompute import precompute_match_context, precompute_ball_context
from app.storage.database import (
    create_match,
    insert_deliveries_bulk,
    get_delivery_by_id,
    get_deliveries,
)


def _ball(over=0, ball=1, batter="A", bowler="X", runs=0, **kw):
    return BallEvent(over=over, ball=ball, batter=batter, bowler=bowler, runs=runs, **kw)


# --------------------------------------------------------------------------- #
#  StateManager tests (pure in-memory, no DB)
# --------------------------------------------------------------------------- #


def test_state_manager_basic():
    sm = StateManager(batting_team="Team B", bowling_team="Team A", target=151)
    for i in range(3):
        sm.update(_ball(over=0, ball=i + 1, batter="A", bowler="X", runs=0))

    s = sm.state
    assert s.total_runs == 0
    assert s.wickets == 0
    assert s.total_balls_bowled == 3
    assert s.balls_in_current_over == 3

    sm.update(_ball(over=0, ball=4, batter="A", bowler="X", runs=4, is_boundary=True))
    assert sm.state.total_runs == 4


def test_state_manager_runs_and_extras():
    sm = StateManager(batting_team="Team B", bowling_team="Team A", target=151)
    sm.update(_ball(over=0, ball=1, batter="A", bowler="X", runs=0))
    sm.update(
        _ball(
            over=0,
            ball=2,
            batter="A",
            bowler="X",
            runs=0,
            extras=1,
            extras_type="wide",
        )
    )
    sm.update(
        _ball(
            over=0,
            ball=2,
            batter="A",
            bowler="X",
            runs=0,
            extras=1,
            extras_type="noball",
        )
    )

    s = sm.state
    assert s.total_extras == 2
    assert s.total_wides == 1
    assert s.total_noballs == 1
    # Wide and noball don't increment balls_in_current_over
    assert s.balls_in_current_over == 1
    assert s.total_balls_bowled == 1
    assert s.total_runs == 2


def test_state_manager_wicket():
    sm = StateManager(batting_team="Team B", bowling_team="Team A", target=151)
    sm.update(_ball(over=0, ball=1, batter="A", bowler="X", runs=0, non_batter="B"))
    sm.update(_ball(over=0, ball=2, batter="A", bowler="X", runs=1, non_batter="B"))
    sm.update(
        _ball(
            over=0,
            ball=3,
            batter="A",
            bowler="X",
            runs=0,
            is_wicket=True,
            wicket_type="bowled",
            non_batter="B",
        )
    )

    s = sm.state
    assert s.wickets == 1
    assert len(s.fall_of_wickets) == 1
    fow = s.fall_of_wickets[0]
    assert fow.batter == "A"
    assert fow.team_score == 1
    assert fow.how == "bowled"

    # Next ball with new batter — is_new_batter should be True
    sm.update(_ball(over=0, ball=4, batter="C", bowler="X", runs=0, non_batter="B"))
    assert sm.state.is_new_batter is True
    assert sm.state.new_batter_name == "C"


def test_state_manager_over_transition():
    sm = StateManager(batting_team="Team B", bowling_team="Team A", target=151)
    for i in range(6):
        sm.update(
            _ball(over=0, ball=i + 1, batter="A", bowler="X", runs=0, non_batter="B")
        )

    s = sm.state
    assert s.overs_completed == 1
    assert s.balls_in_current_over == 0
    assert len(s.over_runs_history) == 1

    # Process first ball of next over with new bowler
    sm.update(_ball(over=1, ball=1, batter="A", bowler="Y", runs=1, non_batter="B"))
    assert sm.state.is_new_bowler is True
    assert sm.state.is_new_over is True


def test_state_manager_partnership():
    sm = StateManager(batting_team="Team B", bowling_team="Team A", target=151)
    for i in range(5):
        sm.update(
            _ball(over=0, ball=i + 1, batter="A", bowler="X", runs=1, non_batter="B")
        )

    assert sm.state.partnership_runs == 5
    assert sm.state.partnership_number == 1

    # Wicket
    sm.update(
        _ball(
            over=0,
            ball=6,
            batter="A",
            bowler="X",
            runs=0,
            is_wicket=True,
            wicket_type="bowled",
            non_batter="B",
        )
    )

    assert sm.state.partnership_runs == 0
    assert sm.state.partnership_number == 2


def test_state_manager_innings_summary():
    sm = StateManager(batting_team="Team B", bowling_team="Team A", target=151)
    sm.update(_ball(over=0, ball=1, batter="A", bowler="X", runs=0, non_batter="B"))
    sm.update(_ball(over=0, ball=2, batter="A", bowler="X", runs=4, is_boundary=True, non_batter="B"))
    sm.update(_ball(over=0, ball=3, batter="B", bowler="X", runs=2, non_batter="A"))
    sm.update(
        _ball(
            over=0,
            ball=4,
            batter="B",
            bowler="X",
            runs=0,
            is_wicket=True,
            wicket_type="caught",
            non_batter="A",
        )
    )
    sm.update(_ball(over=0, ball=5, batter="C", bowler="X", runs=1, non_batter="A"))
    sm.update(_ball(over=0, ball=6, batter="A", bowler="X", runs=1, non_batter="C"))

    summary = sm.get_innings_summary()
    assert isinstance(summary, dict)
    required_keys = {"batting_team", "bowling_team", "total_runs", "batters", "bowlers"}
    for k in required_keys:
        assert k in summary, f"Missing key: {k}"
    assert summary["batting_team"] == "Team B"
    assert summary["bowling_team"] == "Team A"
    assert summary["total_runs"] == 8
    assert len(summary["batters"]) >= 3
    assert len(summary["bowlers"]) >= 1


# --------------------------------------------------------------------------- #
#  LogicEngine tests (pure in-memory, no DB)
# --------------------------------------------------------------------------- #


def test_logic_engine_routine():
    sm = StateManager(batting_team="Team B", bowling_team="Team A", target=151)
    ball = _ball(over=0, ball=1, batter="A", bowler="X", runs=0)
    state = sm.update(ball)

    engine = LogicEngine()
    result = engine.analyze(state, ball)
    assert result.branch == NarrativeBranch.ROUTINE


def test_logic_engine_wicket_drama():
    sm = StateManager(batting_team="Team B", bowling_team="Team A", target=151)
    ball = _ball(
        over=0,
        ball=1,
        batter="A",
        bowler="X",
        runs=0,
        is_wicket=True,
        wicket_type="bowled",
        non_batter="B",
    )
    state = sm.update(ball)

    engine = LogicEngine()
    result = engine.analyze(state, ball)
    assert result.branch == NarrativeBranch.WICKET_DRAMA


def test_logic_engine_boundary_momentum():
    sm = StateManager(batting_team="Team B", bowling_team="Team A", target=151)
    ball = _ball(over=0, ball=1, batter="A", bowler="X", runs=4, is_boundary=True)
    state = sm.update(ball)

    engine = LogicEngine()
    result = engine.analyze(state, ball)
    assert result.branch == NarrativeBranch.BOUNDARY_MOMENTUM


# --------------------------------------------------------------------------- #
#  Precompute tests (require DB, autouse fixture provides fresh DB)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_precompute_single():
    match_info = {
        "target": 151,
        "innings_summary": [
            {"innings_number": 1, "batting_team": "Team A", "bowling_team": "Team B"},
            {"innings_number": 2, "batting_team": "Team B", "bowling_team": "Team A"},
        ],
    }
    match = await create_match(
        title="Precompute Single Test",
        match_info=match_info,
        languages=["hi"],
    )
    match_id = match["match_id"]

    deliveries = [
        {"over": 0, "ball": 1, "batter": "A", "bowler": "X", "runs": 0, "non_batter": "B"},
        {"over": 0, "ball": 2, "batter": "A", "bowler": "X", "runs": 4, "is_boundary": True, "non_batter": "B"},
        {"over": 0, "ball": 3, "batter": "B", "bowler": "X", "runs": 1, "non_batter": "A"},
    ]
    await insert_deliveries_bulk(match_id, 2, deliveries)

    balls = await get_deliveries(match_id, innings=2)
    last_ball_id = balls[-1]["id"]

    result = await precompute_ball_context(last_ball_id)
    assert result.get("status") == "ok"
    assert "context" in result
    assert result["context"] is not None

    # Verify the delivery's context was persisted
    delivery = await get_delivery_by_id(last_ball_id)
    assert delivery is not None
    assert delivery["context"] is not None


@pytest.mark.asyncio
async def test_precompute_match():
    match_info = {
        "target": 151,
        "innings_summary": [
            {"innings_number": 1, "batting_team": "Team A", "bowling_team": "Team B"},
            {"innings_number": 2, "batting_team": "Team B", "bowling_team": "Team A"},
        ],
    }
    match = await create_match(
        title="Precompute Match Test",
        match_info=match_info,
        languages=["hi"],
    )
    match_id = match["match_id"]

    inn1_deliveries = [
        {"over": i, "ball": j, "batter": "A", "bowler": "X", "runs": 0, "non_batter": "B"}
        for i in range(1)
        for j in range(1, 7)
    ]
    inn2_deliveries = [
        {"over": i, "ball": j, "batter": "A", "bowler": "X", "runs": 0, "non_batter": "B"}
        for i in range(1)
        for j in range(1, 7)
    ]
    await insert_deliveries_bulk(match_id, 1, inn1_deliveries)
    await insert_deliveries_bulk(match_id, 2, inn2_deliveries)

    total = await precompute_match_context(match_id)
    assert total > 0

    # Verify first delivery has context
    balls_1 = await get_deliveries(match_id, innings=1)
    assert len(balls_1) >= 1
    first_ball = await get_delivery_by_id(balls_1[0]["id"])
    assert first_ball is not None
    assert first_ball["context"] is not None
