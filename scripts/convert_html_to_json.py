#!/usr/bin/env python3
"""
Convert Cricbuzz ball-by-ball HTML commentary into structured JSON
for the AI Cricket Commentary Engine.

Usage:
    python scripts/convert_html_to_json.py app/feed/sample_match_2.html app/feed/ind_vs_sa_final.json

The output JSON has a richer structure than the original sample_match.json,
including the original commentary text from the feed.
"""

import json
import re
import sys
from pathlib import Path


def parse_cricbuzz_html(html: str) -> list[dict]:
    """
    Parse Cricbuzz ball-by-ball commentary HTML.
    Returns a list of ball events in chronological order.
    """
    # Split on ball-number markers; each chunk starts with "X.Y</div>...commentary"
    parts = re.split(r'min-w-\[1\.5rem\]">', html)

    events = []
    for part in parts[1:]:  # skip the first chunk (before any ball)
        # Some balls have a badge div (W/4/6/F) between ball number and commentary
        # Pattern handles both: with and without the badge
        m = re.match(
            r"([\d.]+)</div>(?:<div[^>]*>.*?</div>)?</div><div>(.*?)</div>",
            part,
            re.DOTALL,
        )
        if not m:
            continue
        over_ball = m.group(1)
        raw_text = m.group(2)
        text = re.sub(r"<[^>]+>", "", raw_text).strip()
        if not text:
            continue

        event = parse_ball_text(over_ball, text)
        if event:
            events.append(event)

    # Cricbuzz HTML is newest-first; reverse to get chronological order
    events.reverse()
    return events


def parse_ball_text(over_ball: str, text: str) -> dict | None:
    """
    Parse a single ball commentary line like:
      'Hardik Pandya to Nortje, 1 run, India are T20 Champions...'
      'Arshdeep Singh to Maharaj, no run, superb yorker from Arshdeep...'
      'Jasprit Bumrah to Miller, FOUR, short and wide outside off...'
      'Axar Patel to Klaasen, out Caught by Suryakumar Yadav!!...'
    """
    over_parts = over_ball.split(".")
    if len(over_parts) != 2:
        return None

    over = int(over_parts[0])
    ball = int(over_parts[1])

    # Extract bowler and batsman: "Bowler to Batsman, result, commentary"
    match = re.match(r"^(.+?)\s+to\s+(.+?),\s*(.+)$", text, re.DOTALL)
    if not match:
        return None

    bowler = match.group(1).strip()
    batsman = match.group(2).strip()
    rest = match.group(3).strip()

    # Split into result and commentary
    # The result is the first segment before the next comma (usually)
    # But some results have commas like "out Caught by X!! commentary"
    result, commentary = extract_result_and_commentary(rest)

    # Parse the result to determine runs, wicket, boundary, extras
    event = classify_result(result)
    event.update(
        {
            "over": over,
            "ball": ball,
            "batsman": batsman,
            "bowler": bowler,
            "commentary": commentary,
            "result_text": result,
        }
    )
    return event


def extract_result_and_commentary(rest: str) -> tuple[str, str]:
    """Split 'result, commentary text...' into (result, commentary)."""
    # Handle wicket case: "out Caught by Player!! Commentary..."
    wicket_match = re.match(
        r"(out\s+.+?)(?:!!|\.\.)\s*(.*)", rest, re.DOTALL | re.IGNORECASE
    )
    if wicket_match:
        return wicket_match.group(1).strip(), wicket_match.group(2).strip()

    # Handle "no run, commentary" / "1 run, commentary" / "FOUR, commentary"
    parts = rest.split(",", 1)
    result = parts[0].strip()
    commentary = parts[1].strip() if len(parts) > 1 else ""
    return result, commentary


def classify_result(result: str) -> dict:
    """
    Classify the result text into structured fields.
    Returns dict with: runs, extras, extras_type, is_wicket, wicket_type,
                       is_boundary, is_six
    """
    result_lower = result.lower().strip()

    event = {
        "runs": 0,
        "extras": 0,
        "extras_type": None,
        "is_wicket": False,
        "wicket_type": None,
        "dismissal_batsman": None,
        "is_boundary": False,
        "is_six": False,
    }

    # Wicket
    if result_lower.startswith("out"):
        event["is_wicket"] = True
        # Extract wicket type: "out Caught by X", "out Bowled", "out LBW", "out Run Out"
        wkt_match = re.search(
            r"out\s+(caught|bowled|lbw|run\s*out|stumped|hit\s*wicket)",
            result_lower,
        )
        if wkt_match:
            event["wicket_type"] = wkt_match.group(1).strip().replace(" ", "_")
        else:
            event["wicket_type"] = "unknown"
        # Try to extract who caught it
        caught_match = re.search(r"caught\s+by\s+(.+)", result, re.IGNORECASE)
        if caught_match:
            event["wicket_type"] = "caught"
        return event

    # SIX
    if result_lower in ("six", "six!"):
        event["runs"] = 6
        event["is_six"] = True
        event["is_boundary"] = True
        return event

    # FOUR
    if result_lower in ("four", "four!"):
        event["runs"] = 4
        event["is_boundary"] = True
        return event

    # Wide
    if "wide" in result_lower:
        event["extras"] = 1
        event["extras_type"] = "wide"
        # Check for additional runs on the wide
        run_match = re.search(r"(\d+)\s*run", result_lower)
        if run_match:
            event["extras"] += int(run_match.group(1))
        return event

    # No ball
    if "no ball" in result_lower or "no-ball" in result_lower:
        event["extras"] = 1
        event["extras_type"] = "noball"
        run_match = re.search(r"(\d+)\s*run", result_lower)
        if run_match:
            event["runs"] = int(run_match.group(1))
        return event

    # Leg byes
    if "leg bye" in result_lower or "leg byes" in result_lower:
        event["extras_type"] = "legbye"
        run_match = re.search(r"(\d+)\s*run", result_lower)
        if run_match:
            event["extras"] = int(run_match.group(1))
        else:
            event["extras"] = 1
        return event

    # Byes
    if "bye" in result_lower and "leg" not in result_lower:
        event["extras_type"] = "bye"
        run_match = re.search(r"(\d+)\s*run", result_lower)
        if run_match:
            event["extras"] = int(run_match.group(1))
        else:
            event["extras"] = 1
        return event

    # No run / dot ball
    if result_lower in ("no run", "no runs"):
        event["runs"] = 0
        return event

    # N runs
    run_match = re.match(r"(\d+)\s*runs?", result_lower)
    if run_match:
        event["runs"] = int(run_match.group(1))
        if event["runs"] == 4:
            event["is_boundary"] = True
        if event["runs"] == 6:
            event["is_six"] = True
            event["is_boundary"] = True
        return event

    return event


def detect_innings_breaks(events: list[dict]) -> list[list[dict]]:
    """
    Split events into innings based on over number resets.
    When we see over 0 again after higher overs, it's a new innings.
    """
    if not events:
        return []

    innings = []
    current_innings = [events[0]]
    prev_over = events[0]["over"]

    for event in events[1:]:
        # If over number drops significantly, it's a new innings
        if event["over"] < prev_over - 1:
            innings.append(current_innings)
            current_innings = []
        current_innings.append(event)
        prev_over = event["over"]

    if current_innings:
        innings.append(current_innings)

    return innings


def build_match_json(
    events: list[dict],
    batting_team_1: str = "India",
    bowling_team_1: str = "South Africa",
    target: int | None = None,
    venue: str = "Kensington Oval, Barbados",
    match_format: str = "T20",
    match_title: str = "ICC T20 World Cup 2024 Final",
) -> dict:
    """Build the full match JSON with innings separation."""
    innings_list = detect_innings_breaks(events)

    match_data = {
        "match_info": {
            "title": match_title,
            "venue": venue,
            "format": match_format,
            "teams": [batting_team_1, bowling_team_1],
        },
        "innings": [],
    }

    for i, innings_events in enumerate(innings_list):
        if i == 0:
            batting = batting_team_1
            bowling = bowling_team_1
        else:
            batting = bowling_team_1
            bowling = batting_team_1

        # Calculate total from events
        total_runs = sum(e["runs"] + e["extras"] for e in innings_events)
        total_wickets = sum(1 for e in innings_events if e["is_wicket"])

        innings_data = {
            "innings_number": i + 1,
            "batting_team": batting,
            "bowling_team": bowling,
            "total_runs": total_runs,
            "total_wickets": total_wickets,
            "target": target if i == 1 else None,
            "balls": innings_events,
        }

        if i == 0:
            # First innings total + 1 = target for second innings
            target = total_runs + 1

        match_data["innings"].append(innings_data)

    return match_data


def main():
    if len(sys.argv) < 3:
        print("Usage: python convert_html_to_json.py <input.html> <output.json>")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    html = input_path.read_text(encoding="utf-8")
    events = parse_cricbuzz_html(html)
    print(f"Parsed {len(events)} ball events")

    innings = detect_innings_breaks(events)
    for i, inn in enumerate(innings):
        total = sum(e["runs"] + e["extras"] for e in inn)
        wkts = sum(1 for e in inn if e["is_wicket"])
        first_ball = f"{inn[0]['over']}.{inn[0]['ball']}"
        last_ball = f"{inn[-1]['over']}.{inn[-1]['ball']}"
        print(f"  Innings {i+1}: {len(inn)} balls, {total}/{wkts}, {first_ball} - {last_ball}")

    match_json = build_match_json(events)

    output_path.write_text(
        json.dumps(match_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Written to {output_path}")


if __name__ == "__main__":
    main()
