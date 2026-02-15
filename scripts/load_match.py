#!/usr/bin/env python3
"""
Load match data from JSON files via the API.

Reads JSON match files from data/sample/ and loads them into the running
server using the public API endpoints — the same endpoints any other
client would use.

Workflow per file:
  1. POST  /api/matches                              — create match (with players, venue, format, teams, date)
  2. POST  /api/matches/{id}/deliveries/bulk          — insert deliveries per innings (context auto-computed)

Supports both the enriched JSON format (from convert_html_to_json.py with
top-level ``players``, scorecard data, etc.) and the original minimal format
(``match_info`` + ``innings[].balls``).

Usage:
    python scripts/load_match.py                       # load all JSON files
    python scripts/load_match.py match_1.json          # load one file
    python scripts/load_match.py --base-url http://localhost:8001

Requires the server to be running (uvicorn app.main:app).
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

FEED_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
DEFAULT_BASE_URL = "http://localhost:8000"


# ------------------------------------------------------------------ #
#  Build enriched match_info
# ------------------------------------------------------------------ #

def build_match_info(raw: dict) -> dict:
    """
    Build enriched match_info from raw JSON data.

    Merges the raw match_info with innings-level summary data so the
    commentary engine has everything it needs (batting/bowling teams,
    target, totals, extras breakdowns).
    """
    match_info_raw = raw.get("match_info", {})
    innings_data = raw.get("innings", [])

    if not innings_data or not isinstance(innings_data[0], dict):
        return match_info_raw

    # Use innings 2 as the "main" innings for chase commentary
    inn2_idx = min(1, len(innings_data) - 1)
    inn2 = innings_data[inn2_idx]

    match_info = {
        **match_info_raw,
        "batting_team": inn2.get("batting_team", ""),
        "bowling_team": inn2.get("bowling_team", ""),
        "target": inn2.get("target") or (innings_data[0].get("total_runs", 0) + 1),
    }

    # Innings summaries for display
    innings_summary = []
    for i, inn in enumerate(innings_data):
        if not isinstance(inn, dict) or "batting_team" not in inn:
            continue
        summary = {
            "innings_number": inn.get("innings_number", i + 1),
            "batting_team": inn.get("batting_team", ""),
            "bowling_team": inn.get("bowling_team", ""),
            "total_runs": inn.get("total_runs", 0),
            "total_wickets": inn.get("total_wickets", 0),
            "total_balls": len(inn.get("balls", [])),
        }
        # Enriched fields from scorecard parser
        if "total_overs" in inn:
            summary["total_overs"] = inn["total_overs"]
        if "run_rate" in inn:
            summary["run_rate"] = inn["run_rate"]
        if "extras_total" in inn:
            summary["extras_total"] = inn["extras_total"]
        if "extras_detail" in inn:
            summary["extras_detail"] = inn["extras_detail"]
        if "target" in inn and inn["target"] is not None:
            summary["target"] = inn["target"]

        innings_summary.append(summary)

    match_info["innings_summary"] = innings_summary
    return match_info


# ------------------------------------------------------------------ #
#  Extract players
# ------------------------------------------------------------------ #

def extract_players(raw: dict) -> list[dict]:
    """
    Extract the player list for match creation.

    Prefers the top-level ``players`` array (from enriched JSON with
    role, captain, keeper, profile_id, image_url, player_status).
    Falls back to scanning ball-by-ball data for backward compatibility.
    """
    # ---- Enriched format: top-level players array ----
    if "players" in raw and raw["players"]:
        players: list[dict] = []
        for p in raw["players"]:
            player = {
                "player_name": p["player_name"],
                "team": p["team"],
            }
            # Forward all optional enriched fields the DB supports
            if p.get("is_captain"):
                player["is_captain"] = True
            if p.get("is_keeper"):
                player["is_keeper"] = True
            if p.get("player_status"):
                player["player_status"] = p["player_status"]
            if p.get("profile_id") is not None:
                player["player_id"] = p["profile_id"]
            players.append(player)
        if players:
            return players

    # ---- Fallback: scan ball data for player names ----
    innings_data = raw.get("innings", [])
    seen: dict[tuple[str, str], dict] = {}

    for inn in innings_data:
        if not isinstance(inn, dict) or "balls" not in inn:
            continue
        batting_team = inn.get("batting_team", "")
        bowling_team = inn.get("bowling_team", "")

        for b in inn["balls"]:
            batter = b.get("batsman") or b.get("batter")
            if batter:
                key = (batter, batting_team)
                if key not in seen:
                    seen[key] = {"player_name": batter, "team": batting_team}
            bowler = b.get("bowler")
            if bowler:
                key = (bowler, bowling_team)
                if key not in seen:
                    seen[key] = {"player_name": bowler, "team": bowling_team}

    return list(seen.values())


# ------------------------------------------------------------------ #
#  Seed a single match file
# ------------------------------------------------------------------ #

def seed_file(client: httpx.Client, filepath: Path) -> bool:
    """
    Seed one JSON match file via the API.  Returns True on success.
    """
    print(f"\n{'='*60}")
    print(f"Seeding: {filepath.name}")
    print(f"{'='*60}")

    with open(filepath) as f:
        raw = json.load(f)

    innings_data = raw.get("innings", [])
    if not innings_data or not isinstance(innings_data[0], dict):
        print(f"  SKIP: no innings data in {filepath.name}")
        return False

    match_info = build_match_info(raw)
    title = match_info.get("title", filepath.stem.replace("_", " ").title())

    # ----------------------------------------------------------
    # 1. Check if already exists (by listing matches)
    # ----------------------------------------------------------
    resp = client.get("/api/matches")
    resp.raise_for_status()
    existing = [m for m in resp.json() if m["title"] == title]
    if existing:
        print(f"  SKIP: '{title}' already exists (match_id={existing[0]['match_id']})")
        return True

    # ----------------------------------------------------------
    # 2. Extract players
    # ----------------------------------------------------------
    player_list = extract_players(raw)
    playing_xi = [p for p in player_list if p.get("player_status", "Playing XI") == "Playing XI"]
    print(f"  Extracted {len(player_list)} players ({len(playing_xi)} Playing XI)")

    # ----------------------------------------------------------
    # 3. Create match (with players + top-level fields)
    # ----------------------------------------------------------
    create_payload: dict = {
        "title": title,
        "match_info": match_info,
        "languages": ["hi"],
        "status": "ready",
        "players": player_list,
    }

    # Promote top-level match fields from match_info
    if match_info.get("venue"):
        create_payload["venue"] = match_info["venue"]
    if match_info.get("format"):
        create_payload["format"] = match_info["format"]
    if match_info.get("team1"):
        create_payload["team1"] = match_info["team1"]
    elif match_info.get("teams") and len(match_info["teams"]) >= 1:
        create_payload["team1"] = match_info["teams"][0]
    if match_info.get("team2"):
        create_payload["team2"] = match_info["team2"]
    elif match_info.get("teams") and len(match_info["teams"]) >= 2:
        create_payload["team2"] = match_info["teams"][1]
    if match_info.get("match_date"):
        create_payload["match_date"] = match_info["match_date"]

    resp = client.post("/api/matches", json=create_payload)
    resp.raise_for_status()
    match = resp.json()
    match_id = match["match_id"]
    print(f"  Created match: id={match_id}, title='{title}'")
    if create_payload.get("venue"):
        print(f"    venue={create_payload['venue']}, format={create_payload.get('format')}")
    if create_payload.get("team1"):
        print(f"    {create_payload['team1']} vs {create_payload.get('team2', '?')}")

    # ----------------------------------------------------------
    # 4. Build name→ID lookup from created players
    # ----------------------------------------------------------
    resp = client.get(f"/api/matches/{match_id}/players")
    resp.raise_for_status()
    player_rows = resp.json().get("players", [])
    # Lookup: (name, team) -> player row id
    player_lookup: dict[tuple[str, str], int] = {}
    for p in player_rows:
        player_lookup[(p["player_name"], p["team"])] = p["id"]
    print(f"  Player lookup: {len(player_lookup)} entries")

    # ----------------------------------------------------------
    # 5. Bulk-insert deliveries for each innings (with player IDs)
    # ----------------------------------------------------------
    total_deliveries = 0
    for inn in innings_data:
        if not isinstance(inn, dict) or "balls" not in inn:
            continue
        innings_num = inn.get("innings_number", 1)
        batting_team = inn.get("batting_team", "")
        bowling_team = inn.get("bowling_team", "")
        raw_balls = inn["balls"]

        # Map batsman→batter, dismissal_batsman→dismissal_batter, resolve IDs
        deliveries = []
        for b in raw_balls:
            d = dict(b)
            if "batsman" in d:
                d["batter"] = d.pop("batsman")
            if "dismissal_batsman" in d:
                d["dismissal_batter"] = d.pop("dismissal_batsman")

            # Resolve player IDs from lookup
            batter_name = d.get("batter", "")
            bowler_name = d.get("bowler", "")
            d["batter_id"] = player_lookup.get((batter_name, batting_team))
            d["bowler_id"] = player_lookup.get((bowler_name, bowling_team))
            # non_batter_id cannot be resolved here (inferred during precompute)

            deliveries.append(d)

        resp = client.post(
            f"/api/matches/{match_id}/deliveries/bulk",
            json={"innings": innings_num, "deliveries": deliveries},
        )
        resp.raise_for_status()
        result = resp.json()
        count = result["deliveries_inserted"]
        ctx = result.get("context_computed", 0)
        total_deliveries += count
        print(f"  Innings {innings_num}: {batting_team} — "
              f"inserted {count} deliveries, {ctx} contexts computed")

    # ----------------------------------------------------------
    # 6. Log final summary
    # ----------------------------------------------------------
    for inn in innings_data:
        if not isinstance(inn, dict):
            continue
        num = inn.get("innings_number", "?")
        team = inn.get("batting_team", "?")
        runs = inn.get("total_runs", "?")
        wkts = inn.get("total_wickets", "?")
        overs = inn.get("total_overs", "?")
        extras = inn.get("extras_total", 0)
        batters = len(inn.get("batters", []))
        bowlers = len(inn.get("bowlers", []))
        fow = len(inn.get("fall_of_wickets", []))
        pships = len(inn.get("partnerships", []))
        if batters:
            print(f"  Innings {num} scorecard: {team} {runs}/{wkts} ({overs} Ov), "
                  f"extras={extras}, {batters} batters, {bowlers} bowlers, "
                  f"{fow} FOW, {pships} partnerships")

    print(f"  SUCCESS: match_id={match_id}, {total_deliveries} deliveries loaded")
    return True


# ------------------------------------------------------------------ #
#  CLI entry point
# ------------------------------------------------------------------ #

def main():
    parser = argparse.ArgumentParser(description="Seed match data via the API")
    parser.add_argument(
        "files", nargs="*",
        help="JSON filenames to seed (default: all *.json in data/sample/)",
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help=f"Server base URL (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args()

    # Resolve files
    if args.files:
        filepaths = []
        for name in args.files:
            p = FEED_DIR / name
            if not p.exists():
                print(f"ERROR: file not found: {p}")
                sys.exit(1)
            filepaths.append(p)
    else:
        filepaths = sorted(FEED_DIR.glob("*.json"))
        if not filepaths:
            print(f"No JSON files found in {FEED_DIR}")
            sys.exit(1)

    print(f"Server: {args.base_url}")
    print(f"Files:  {len(filepaths)}")

    # Verify server is reachable
    client = httpx.Client(base_url=args.base_url, timeout=60.0)
    try:
        resp = client.get("/api/languages")
        resp.raise_for_status()
    except httpx.ConnectError:
        print(f"\nERROR: Cannot connect to {args.base_url}")
        print("Make sure the server is running: uvicorn app.main:app")
        sys.exit(1)

    # Seed each file
    success = 0
    failed = 0
    for fp in filepaths:
        try:
            if seed_file(client, fp):
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"\n  FAILED: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Done: {success} succeeded, {failed} failed")
    print(f"{'='*60}")

    client.close()


if __name__ == "__main__":
    main()
