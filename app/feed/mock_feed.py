import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import AsyncGenerator

from app.config import settings
from app.models import BallEvent


FEED_DIR = Path(__file__).parent


def _compute_innings_summary(inn_data: dict) -> dict:
    """Compute a rich summary from an innings' ball data."""
    balls = inn_data.get("balls", [])
    if not balls:
        return {
            "batting_team": inn_data.get("batting_team", ""),
            "bowling_team": inn_data.get("bowling_team", ""),
            "total_runs": inn_data.get("total_runs", 0),
            "total_wickets": inn_data.get("total_wickets", 0),
        }

    # Compute batsman scores
    batsmen_runs: dict[str, int] = defaultdict(int)
    batsmen_balls: dict[str, int] = defaultdict(int)
    batsmen_fours: dict[str, int] = defaultdict(int)
    batsmen_sixes: dict[str, int] = defaultdict(int)
    total_fours = 0
    total_sixes = 0
    total_extras = 0

    # Compute bowler figures
    bowlers_balls: dict[str, int] = defaultdict(int)
    bowlers_runs: dict[str, int] = defaultdict(int)
    bowlers_wickets: dict[str, int] = defaultdict(int)

    for b in balls:
        batsman = b.get("batsman", "")
        bowler = b.get("bowler", "")
        runs = b.get("runs", 0)
        extras = b.get("extras", 0)
        extras_type = b.get("extras_type")
        is_legal = extras_type not in ("wide", "noball")

        batsmen_runs[batsman] += runs
        if is_legal:
            batsmen_balls[batsman] += 1
        if b.get("is_boundary"):
            batsmen_fours[batsman] += 1
            total_fours += 1
        if b.get("is_six"):
            batsmen_sixes[batsman] += 1
            total_sixes += 1
        if extras > 0:
            total_extras += extras

        # Bowler
        bowlers_runs[bowler] += runs + extras
        if is_legal:
            bowlers_balls[bowler] += 1
        if b.get("is_wicket") and b.get("wicket_type") != "run_out":
            bowlers_wickets[bowler] += 1

    # Top scorers (sorted by runs desc)
    top_scorers = sorted(batsmen_runs.items(), key=lambda x: x[1], reverse=True)
    top_scorers_str = ", ".join(
        f"{name} {runs}({batsmen_balls.get(name, 0)})"
        + (f" [{batsmen_fours.get(name, 0)}x4, {batsmen_sixes.get(name, 0)}x6]"
           if batsmen_fours.get(name, 0) + batsmen_sixes.get(name, 0) > 0 else "")
        for name, runs in top_scorers[:4]
        if runs >= 15
    )

    # Top bowlers (sorted by wickets desc, then economy)
    top_bowlers = sorted(
        bowlers_wickets.items(),
        key=lambda x: (-x[1], bowlers_runs.get(x[0], 0) / max(bowlers_balls.get(x[0], 1), 1)),
    )
    top_bowlers_str = ", ".join(
        f"{name} {wkts}/{bowlers_runs.get(name, 0)} ({bowlers_balls.get(name, 0) // 6}.{bowlers_balls.get(name, 0) % 6} ov)"
        for name, wkts in top_bowlers[:3]
        if wkts > 0
    )

    return {
        "batting_team": inn_data.get("batting_team", ""),
        "bowling_team": inn_data.get("bowling_team", ""),
        "total_runs": inn_data.get("total_runs", 0),
        "total_wickets": inn_data.get("total_wickets", 0),
        "top_scorers": top_scorers_str,
        "top_bowlers": top_bowlers_str,
        "total_fours": total_fours,
        "total_sixes": total_sixes,
        "total_extras": total_extras,
    }


def load_match_data(
    filename: str = "ind_vs_sa_final.json",
    innings: int = 2,
) -> tuple[dict, list[BallEvent]]:
    """
    Load match info and ball events from a JSON file.

    Supports two formats:
      - New format (innings-based): { match_info, innings: [{ balls: [...] }] }
      - Legacy flat format:         { match_info, innings: [ ball, ball, ... ] }

    Args:
        filename: JSON file in the feed directory.
        innings:  Which innings to load (1 or 2). Only for new format.
    """
    filepath = FEED_DIR / filename
    with open(filepath) as f:
        data = json.load(f)

    match_info = data["match_info"]

    # Detect format
    innings_data = data["innings"]
    if isinstance(innings_data, list) and len(innings_data) > 0:
        first = innings_data[0]
        if isinstance(first, dict) and "balls" in first:
            # New format: innings-based
            idx = min(innings - 1, len(innings_data) - 1)
            inn = innings_data[idx]
            balls = [BallEvent(**b) for b in inn["balls"]]
            # Merge innings-level info into match_info
            match_info = {
                **match_info,
                "batting_team": inn.get("batting_team", match_info.get("teams", ["", ""])[0]),
                "bowling_team": inn.get("bowling_team", match_info.get("teams", ["", ""])[1]),
                "target": inn.get("target") or (inn.get("total_runs", 0) + 1),
            }

            # Attach first innings summary when loading innings 2
            if innings == 2 and len(innings_data) >= 2:
                match_info["first_innings"] = _compute_innings_summary(innings_data[0])

            # Attach second innings summary (for match result context)
            if len(innings_data) >= 2:
                match_info["second_innings_meta"] = {
                    "total_runs": innings_data[1].get("total_runs", 0),
                    "total_wickets": innings_data[1].get("total_wickets", 0),
                }

            return match_info, balls
        else:
            # Legacy flat format (list of ball dicts directly)
            balls = [BallEvent(**b) for b in innings_data]
            return match_info, balls

    return match_info, []


async def replay_feed(
    delay: float | None = None,
    filename: str = "ind_vs_sa_final.json",
    innings: int = 2,
) -> AsyncGenerator[BallEvent, None]:
    """
    Async generator that replays match ball by ball.
    Yields one BallEvent at a time with a configurable delay between deliveries.
    """
    if delay is None:
        delay = settings.ball_delay_seconds

    _, balls = load_match_data(filename=filename, innings=innings)

    for ball in balls:
        yield ball
        await asyncio.sleep(delay)
