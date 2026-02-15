#!/usr/bin/env python3
"""
Convert Cricbuzz HTML pages (info, scorecard, squads, live commentary)
into a single structured JSON file for the AI Cricket Commentary Engine.

Parses four HTML files from a match folder and produces one comprehensive
JSON compatible with load_match.py, plus enriched scorecard/squad data.

Usage:
    python scripts/convert_html_to_json.py data/sample/match_1/
    python scripts/convert_html_to_json.py data/sample/match_1/ --output data/sample/match_1.json

Requires: beautifulsoup4
"""

import argparse
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString


# ──────────────────────────────────────────────────────────────────────
# Team-name mapping (short → full).  Extend as needed.
# ──────────────────────────────────────────────────────────────────────
TEAM_SHORT_TO_FULL = {
    "IND": "India",
    "RSA": "South Africa",
    "AUS": "Australia",
    "ENG": "England",
    "NZ": "New Zealand",
    "PAK": "Pakistan",
    "SL": "Sri Lanka",
    "BAN": "Bangladesh",
    "WI": "West Indies",
    "ZIM": "Zimbabwe",
    "AFG": "Afghanistan",
    "IRE": "Ireland",
    "SCO": "Scotland",
    "NEP": "Nepal",
    "NAM": "Namibia",
    "USA": "United States of America",
    "UAE": "United Arab Emirates",
    "OMA": "Oman",
    "PNG": "Papua New Guinea",
    "NED": "Netherlands",
    "ITA": "Italy",
}


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _clean(text: str | None) -> str:
    """Strip whitespace, \xa0, HTML comments, etc."""
    if text is None:
        return ""
    return text.replace("\xa0", " ").strip()


def _profile_id(href: str | None) -> int | None:
    """Extract numeric profile id from '/profiles/576/rohit'."""
    if not href:
        return None
    m = re.search(r"/profiles/(\d+)/", href)
    return int(m.group(1)) if m else None


def _safe_int(val: str | None, default: int = 0) -> int:
    try:
        return int(_clean(val))
    except (ValueError, TypeError):
        return default


def _safe_float(val: str | None, default: float = 0.0) -> float:
    try:
        return float(_clean(val))
    except (ValueError, TypeError):
        return default


def _soup(html_path: Path) -> BeautifulSoup:
    """Read HTML file and return a BeautifulSoup tree."""
    text = html_path.read_text(encoding="utf-8")
    return BeautifulSoup(text, "html.parser")


# ──────────────────────────────────────────────────────────────────────
# 1. parse_info  —  match_info from *_info.html
# ──────────────────────────────────────────────────────────────────────

def parse_info(html_path: Path) -> dict:
    """
    Extract match metadata from the Cricbuzz info page.
    Returns a dict suitable for the ``match_info`` key in the output JSON.
    """
    soup = _soup(html_path)

    # Key-value facts rows
    facts: dict[str, str] = {}
    for row in soup.select("div.facts-row-grid"):
        label_el = row.find("div", class_="font-bold")
        if not label_el:
            continue
        label = _clean(label_el.get_text())
        # Value is the next sibling (could be a <div> or an <a>)
        value_parts: list[str] = []
        for sib in label_el.find_next_siblings():
            t = _clean(sib.get_text())
            if t:
                value_parts.append(t)
        value = " ".join(value_parts)
        if label and value:
            facts[label] = value

    # Result text
    result_el = soup.select_one("div.text-cbComplete")
    result_text = _clean(result_el.get_text()) if result_el else ""

    # Parse the "Match" field:  "RSA vs IND • Final • ICC Mens T20 World Cup 2024"
    match_field = facts.get("Match", "")
    parts = [p.strip() for p in match_field.split("•")]

    # Derive teams from first part "RSA vs IND"
    team_abbrs: list[str] = []
    teams_full: list[str] = []
    if parts:
        vs_match = re.match(r"(\w+)\s+vs\s+(\w+)", parts[0])
        if vs_match:
            team_abbrs = [vs_match.group(1), vs_match.group(2)]
            teams_full = [
                TEAM_SHORT_TO_FULL.get(a, a) for a in team_abbrs
            ]

    # Build title with full team names:  "South Africa vs India, Final, ICC Mens T20 World Cup 2024"
    if teams_full and len(parts) >= 1:
        title_parts = [f"{teams_full[0]} vs {teams_full[1]}"] + parts[1:]
    else:
        title_parts = parts
    title = ", ".join(title_parts) if title_parts else match_field

    series = parts[2] if len(parts) >= 3 else ""

    # Venue details (from venue guide section)
    venue_details: dict[str, str] = {}
    for key in ("Stadium", "City", "Capacity", "Ends", "Hosts To"):
        if key in facts:
            venue_details[key.lower().replace(" ", "_")] = facts[key]

    # Detect format from title or series
    fmt = "T20"
    lower_title = title.lower()
    if "test" in lower_title:
        fmt = "Test"
    elif "odi" in lower_title or "one day" in lower_title:
        fmt = "ODI"

    info = {
        "title": title,
        "venue": facts.get("Venue", ""),
        "format": fmt,
        "teams": teams_full,
        "team1": teams_full[0] if len(teams_full) > 0 else "",
        "team2": teams_full[1] if len(teams_full) > 1 else "",
        "team1_short": team_abbrs[0] if len(team_abbrs) > 0 else "",
        "team2_short": team_abbrs[1] if len(team_abbrs) > 1 else "",
        "match_date": facts.get("Date", ""),
        "time": facts.get("Time", ""),
        "toss": facts.get("Toss", ""),
        "result": result_text,
        "series": series,
        "umpires": facts.get("Umpires", ""),
        "third_umpire": facts.get("3rd Umpire", ""),
        "referee": facts.get("Referee", ""),
        "venue_details": venue_details,
    }
    return info


# ──────────────────────────────────────────────────────────────────────
# 2. parse_squads  —  players from *_squads.html
# ──────────────────────────────────────────────────────────────────────

def _parse_player_card(a_tag, team_name: str, player_status: str) -> dict:
    """Parse a single player <a> card from the squads page."""
    name_spans = a_tag.select("span")
    player_name = ""
    is_captain = False
    is_keeper = False
    for sp in name_spans:
        txt = _clean(sp.get_text())
        if txt == "(C)":
            is_captain = True
        elif txt == "(WK)":
            is_keeper = True
        elif txt and not player_name:
            player_name = txt

    role_el = a_tag.select_one("div.text-cbTxtSec.text-xs")
    role = _clean(role_el.get_text()) if role_el else ""

    profile = _profile_id(a_tag.get("href"))

    img_el = a_tag.select_one("img")
    image_url = img_el.get("src", "") if img_el else ""

    return {
        "player_name": player_name,
        "team": team_name,
        "role": role,
        "is_captain": is_captain,
        "is_keeper": is_keeper,
        "player_status": player_status,
        "profile_id": profile,
        "image_url": image_url,
    }


def parse_squads(html_path: Path, team_map: dict[str, str] | None = None) -> list[dict]:
    """
    Extract players from the Cricbuzz squads page.

    ``team_map`` maps short names (e.g. "RSA") to full names (e.g. "South Africa").
    If not supplied, uses the built-in TEAM_SHORT_TO_FULL.
    """
    if team_map is None:
        team_map = TEAM_SHORT_TO_FULL

    soup = _soup(html_path)

    # Determine team names from header
    header = soup.select_one("div.bg-cbInactTab")
    team1_short = ""
    team2_short = ""
    if header:
        h1_tags = header.select("h1.font-bold")
        if len(h1_tags) >= 2:
            team1_short = _clean(h1_tags[0].get_text())
            team2_short = _clean(h1_tags[1].get_text())

    team1_full = team_map.get(team1_short, team1_short)
    team2_full = team_map.get(team2_short, team2_short)

    players: list[dict] = []

    # Sections: "playing XI", "bench", "support staff"
    sections = soup.select("div.pb-5")
    for section in sections:
        heading_el = section.select_one("h1.capitalize")
        if not heading_el:
            continue
        heading = _clean(heading_el.get_text()).lower()

        if "playing" in heading:
            status = "Playing XI"
        elif "bench" in heading:
            status = "Bench"
        elif "support" in heading:
            status = "Support Staff"
        else:
            status = heading.title()

        # Two columns: left = team1, right = team2
        halves = section.select("div.w-1\\/2")
        if len(halves) >= 2:
            # Team 1 (left)
            for a_tag in halves[0].select("a[href*='/profiles/']"):
                p = _parse_player_card(a_tag, team1_full, status)
                if p["player_name"]:
                    players.append(p)
            # Team 2 (right)
            for a_tag in halves[1].select("a[href*='/profiles/']"):
                p = _parse_player_card(a_tag, team2_full, status)
                if p["player_name"]:
                    players.append(p)

    return players


# ──────────────────────────────────────────────────────────────────────
# 3. parse_scorecard  —  batting, bowling, FOW, partnerships, etc.
# ──────────────────────────────────────────────────────────────────────

def _parse_innings_section(scard_div) -> dict:
    """
    Parse a single innings scorecard section (the div[id^='scard-team-...']).
    Returns dict with batters, bowlers, fall_of_wickets, partnerships,
    extras, total, did_not_bat, powerplays.
    """
    innings: dict = {
        "batters": [],
        "bowlers": [],
        "fall_of_wickets": [],
        "partnerships": [],
        "extras_total": 0,
        "extras_detail": {},
        "total_runs": 0,
        "total_wickets": 0,
        "total_overs": 0.0,
        "run_rate": 0.0,
        "did_not_bat": [],
        "powerplays": [],
    }

    # ---- Batting ----
    bat_grids = scard_div.select("div.scorecard-bat-grid")
    position = 0
    for row in bat_grids:
        # Skip the header row (contains "Batter" text in a font-bold div)
        first_child = row.find("div")
        if first_child and first_child.get("class") and "font-bold" in first_child.get("class", []):
            header_text = _clean(first_child.get_text())
            if header_text == "Batter":
                continue

        # Player name + dismissal
        name_link = row.select_one("a.text-cbTextLink")
        if not name_link:
            continue

        position += 1
        name_raw = _clean(name_link.get_text())
        # Remove (c), (wk) suffixes for clean name
        batter_name = re.sub(r"\s*\((?:c|wk)\)\s*$", "", name_raw, flags=re.IGNORECASE).strip()

        pid = _profile_id(name_link.get("href"))

        is_captain = "(c)" in name_raw.lower()
        is_keeper = "(wk)" in name_raw.lower()

        # Dismissal info
        dismissal_el = row.select_one("div.text-cbTxtSec")
        dismissal = _clean(dismissal_el.get_text()) if dismissal_el else "not out"
        is_out = dismissal.lower() != "not out"

        # Stats: the grid has R, B, 4s, 6s, SR in child divs
        # After the first div (name+dismissal), the next divs contain stats
        stat_divs = row.select("div.flex.justify-center.items-center")
        runs = _safe_int(stat_divs[0].get_text()) if len(stat_divs) > 0 else 0
        balls = _safe_int(stat_divs[1].get_text()) if len(stat_divs) > 1 else 0
        fours = _safe_int(stat_divs[2].get_text()) if len(stat_divs) > 2 else 0
        sixes = _safe_int(stat_divs[3].get_text()) if len(stat_divs) > 3 else 0
        sr = _safe_float(stat_divs[4].get_text()) if len(stat_divs) > 4 else 0.0

        innings["batters"].append({
            "name": batter_name,
            "runs": runs,
            "balls": balls,
            "fours": fours,
            "sixes": sixes,
            "strike_rate": sr,
            "dismissal_info": dismissal,
            "is_out": is_out,
            "is_captain": is_captain,
            "is_keeper": is_keeper,
            "position": position,
            "profile_id": pid,
        })

    # ---- Extras ----
    for div in scard_div.select("div"):
        bold = div.find("div", class_="font-bold")
        if bold and _clean(bold.get_text()) == "Extras":
            # Parse "7 (b 0, lb 0, w 6, nb 1, p 0)"
            val_div = div.select_one("div.tb\\:w-2\\/5, div.wb\\:w-2\\/5")
            if not val_div:
                # fallback: get the sibling of the bold
                siblings = bold.find_next_siblings("div")
                val_div = siblings[0] if siblings else None
            if val_div:
                total_span = val_div.find("span", class_="font-bold")
                innings["extras_total"] = _safe_int(total_span.get_text() if total_span else "0")
                detail_text = val_div.get_text()
                m = re.search(r"\(b\s*(\d+),\s*lb\s*(\d+),\s*w\s*(\d+),\s*nb\s*(\d+),\s*p\s*(\d+)\)", detail_text)
                if m:
                    innings["extras_detail"] = {
                        "b": int(m.group(1)),
                        "lb": int(m.group(2)),
                        "w": int(m.group(3)),
                        "nb": int(m.group(4)),
                        "p": int(m.group(5)),
                    }
            break

    # ---- Total ----
    for div in scard_div.select("div"):
        bold = div.find("div", class_="font-bold")
        if bold and _clean(bold.get_text()) == "Total":
            val_div = div.select_one("div.tb\\:w-2\\/5, div.wb\\:w-2\\/5")
            if not val_div:
                siblings = bold.find_next_siblings("div")
                val_div = siblings[0] if siblings else None
            if val_div:
                text = _clean(val_div.get_text())
                # "176-7 (20 Overs, RR: 8.8)"
                tm = re.match(r"(\d+)-(\d+)\s*\(([\d.]+)\s*Overs?,\s*RR:\s*([\d.]+)\)", text)
                if tm:
                    innings["total_runs"] = int(tm.group(1))
                    innings["total_wickets"] = int(tm.group(2))
                    innings["total_overs"] = float(tm.group(3))
                    innings["run_rate"] = float(tm.group(4))
            break

    # ---- Did not bat ----
    for div in scard_div.select("div"):
        bold = div.find("div", class_="font-bold")
        if bold and "Did not" in _clean(bold.get_text()):
            links = div.select("a.text-cbTextLink")
            for lnk in links:
                name = _clean(lnk.get_text()).rstrip(", ")
                if name:
                    innings["did_not_bat"].append(name)
            break

    # ---- Bowling ----
    bowl_grids = scard_div.select("div.scorecard-bowl-grid")
    for row in bowl_grids:
        # Skip header
        first = row.find("div")
        if first and first.get("class") and "font-bold" in first.get("class", []):
            header_text = _clean(first.get_text())
            if header_text == "Bowler":
                continue

        name_link = row.select_one("a.text-cbTextLink")
        if not name_link:
            continue

        bowler_name_raw = _clean(name_link.get_text())
        bowler_name = re.sub(r"\s*\(c\)\s*$", "", bowler_name_raw, flags=re.IGNORECASE).strip()
        pid = _profile_id(name_link.get("href"))

        # Stats: O, M, R, W, NB, WD, ECO
        stat_divs = row.select("div.flex.justify-center.items-center")
        # Also pick up hidden NB/WD columns
        hidden_divs = row.select("div.justify-center.items-center")
        # Combine: visible stat_divs first, then hidden ones for NB/WD
        all_stats: list[str] = []
        for d in row.children:
            if isinstance(d, NavigableString):
                continue
            if d.name == "a":
                # Skip name link and highlights link
                if "text-cbTextLink" in d.get("class", []):
                    continue
                continue
            txt = _clean(d.get_text())
            classes = d.get("class", [])
            if "justify-center" in classes and "items-center" in classes:
                all_stats.append(txt)

        overs = _safe_float(all_stats[0]) if len(all_stats) > 0 else 0.0
        maidens = _safe_int(all_stats[1]) if len(all_stats) > 1 else 0
        runs = _safe_int(all_stats[2]) if len(all_stats) > 2 else 0
        wickets = _safe_int(all_stats[3]) if len(all_stats) > 3 else 0
        noballs = _safe_int(all_stats[4]) if len(all_stats) > 4 else 0
        wides = _safe_int(all_stats[5]) if len(all_stats) > 5 else 0
        economy = _safe_float(all_stats[6]) if len(all_stats) > 6 else 0.0

        innings["bowlers"].append({
            "name": bowler_name,
            "overs": overs,
            "maidens": maidens,
            "runs": runs,
            "wickets": wickets,
            "noballs": noballs,
            "wides": wides,
            "economy": economy,
            "profile_id": pid,
        })

    # ---- Fall of Wickets ----
    fow_header_seen = False
    pship_header_seen = False
    pp_header_seen = False
    fow_grids = scard_div.select("div.scorecard-fow-grid")
    for row in fow_grids:
        header_div = row.find("div", class_="font-bold")
        if header_div:
            htxt = _clean(header_div.get_text())
            if htxt == "Fall of Wickets":
                fow_header_seen = True
                pship_header_seen = False
                pp_header_seen = False
                continue
            elif htxt == "Partnerships":
                pship_header_seen = True
                fow_header_seen = False
                pp_header_seen = False
                continue
            elif htxt == "Powerplays":
                pp_header_seen = True
                fow_header_seen = False
                pship_header_seen = False
                continue
            else:
                # Other header (Score, Over, Overs, Runs labels)
                continue

        if fow_header_seen:
            lnk = row.select_one("a.text-cbTextLink")
            stat_divs = row.select("div.flex.justify-center.items-center")
            if lnk and len(stat_divs) >= 2:
                batter_name = _clean(lnk.get_text())
                score_text = _clean(stat_divs[0].get_text())
                over_text = _clean(stat_divs[1].get_text())
                # Parse score "23-1" -> team_score=23, wicket_number=1
                sm = re.match(r"(\d+)-(\d+)", score_text)
                team_score = int(sm.group(1)) if sm else 0
                wkt_num = int(sm.group(2)) if sm else 0
                innings["fall_of_wickets"].append({
                    "batter": batter_name,
                    "score": score_text,
                    "team_score": team_score,
                    "wicket_number": wkt_num,
                    "overs": over_text,
                })

        elif pp_header_seen:
            # Powerplay rows reuse the fow-grid layout
            divs = row.select("div")
            texts = [_clean(d.get_text()) for d in divs
                     if not d.find("div")]  # leaf divs only
            if len(texts) >= 3:
                innings["powerplays"].append({
                    "type": texts[0],
                    "overs": texts[1],
                    "runs": _safe_int(texts[2]),
                })
            elif len(texts) == 1 and texts[0] and texts[0] != "Powerplays":
                # single-child row – parse from full text
                pass

    # ---- Partnerships ----
    pship_grids = scard_div.select("div.scorecard-pship-grid")
    wicket_num = 0
    for row in pship_grids:
        # Each row: batter1 name(runs), partnership_runs(balls), batter2 name(runs)
        sides = row.select("div.flex.justify-self-start, div.flex.justify-self-end")
        center = row.select_one("div.justify-self-center")
        if not center or len(sides) < 2:
            continue

        wicket_num += 1

        def _parse_pship_side(side_div) -> tuple[str, int]:
            lnk = side_div.select_one("a.text-cbTextLink")
            name = _clean(lnk.get_text()) if lnk else ""
            runs_el = side_div.select_one("div.text-cbTxtSec")
            runs_txt = _clean(runs_el.get_text()) if runs_el else "0"
            # "9(0)" or "47(0)" – first number is runs
            rm = re.match(r"(\d+)", runs_txt)
            runs_val = int(rm.group(1)) if rm else 0
            return name, runs_val

        b1_name, b1_runs = _parse_pship_side(sides[0])
        b2_name, b2_runs = _parse_pship_side(sides[1])

        # Center: "23(10)" → runs=23, balls=10
        center_text = _clean(center.get_text())
        cm = re.match(r"(\d+)\s*\((\d+)\)", center_text)
        p_runs = int(cm.group(1)) if cm else 0
        p_balls = int(cm.group(2)) if cm else 0

        innings["partnerships"].append({
            "wicket_number": wicket_num,
            "batter1": b1_name,
            "batter2": b2_name,
            "runs": p_runs,
            "balls": p_balls,
            "batter1_runs": b1_runs,
            "batter2_runs": b2_runs,
        })

    return innings


def parse_scorecard(html_path: Path) -> tuple[list[dict], str]:
    """
    Parse the Cricbuzz scorecard HTML.
    Returns (innings_list, result_text).
    innings_list is a list of innings dicts (one per innings), in batting order.
    Each innings dict has: batting_team, bowling_team, batters, bowlers,
    fall_of_wickets, partnerships, extras, total, did_not_bat, powerplays.
    """
    soup = _soup(html_path)

    # Result text (e.g. "India won by 7 runs")
    result_el = soup.select_one("div.text-cbComplete")
    result_text = _clean(result_el.get_text()) if result_el else ""

    innings_list: list[dict] = []
    seen_ids: set[str] = set()

    # Find innings header divs: id like "team-2-innings-1"
    for header_div in soup.select("div[id^='team-'][id*='-innings-']"):
        div_id = header_div.get("id", "")
        # Avoid processing duplicates (mobile + desktop versions)
        if div_id in seen_ids:
            continue
        seen_ids.add(div_id)

        # Extract innings number from id
        m = re.search(r"innings-(\d+)", div_id)
        if not m:
            continue
        innings_num = int(m.group(1))

        # Team short name (mobile: first .font-bold that's not hidden)
        short_el = header_div.select_one("div.tb\\:hidden.font-bold")
        team_short = _clean(short_el.get_text()) if short_el else ""

        # Team full name (desktop: hidden tb:block)
        full_el = header_div.select_one("div.hidden.tb\\:block.font-bold")
        team_full = _clean(full_el.get_text()) if full_el else TEAM_SHORT_TO_FULL.get(team_short, team_short)

        # Score text: "176-7" and "(20 Ov)"
        score_el = header_div.select_one("span.font-bold")
        score_text = _clean(score_el.get_text()) if score_el else ""

        # Find the corresponding scorecard section
        scard_id = f"scard-{div_id}"
        scard_div = soup.find("div", id=scard_id)
        if not scard_div:
            continue

        innings_data = _parse_innings_section(scard_div)
        innings_data["innings_number"] = innings_num
        innings_data["batting_team"] = team_full
        innings_data["batting_team_short"] = team_short

        innings_list.append(innings_data)

    # Sort by innings_number
    innings_list.sort(key=lambda x: x["innings_number"])

    # Assign bowling teams (opponent of batting team)
    if len(innings_list) >= 2:
        innings_list[0]["bowling_team"] = innings_list[1]["batting_team"]
        innings_list[1]["bowling_team"] = innings_list[0]["batting_team"]
    elif len(innings_list) == 1:
        innings_list[0]["bowling_team"] = ""

    return innings_list, result_text


# ──────────────────────────────────────────────────────────────────────
# 4. parse_live  —  ball-by-ball from *_live.html
# ──────────────────────────────────────────────────────────────────────

def _parse_ball_text(over_ball: str, text: str) -> dict | None:
    """
    Parse a single ball commentary line.
    Reuses logic from the original convert_html_to_json.py.
    """
    over_parts = over_ball.split(".")
    if len(over_parts) != 2:
        return None

    over = int(over_parts[0])
    ball = int(over_parts[1])

    match = re.match(r"^(.+?)\s+to\s+(.+?),\s*(.+)$", text, re.DOTALL)
    if not match:
        return None

    bowler = match.group(1).strip()
    batsman = match.group(2).strip()
    rest = match.group(3).strip()

    result, commentary = _extract_result_and_commentary(rest)
    event = _classify_result(result)
    event.update({
        "over": over,
        "ball": ball,
        "batsman": batsman,
        "bowler": bowler,
        "commentary": commentary,
        "result_text": result,
    })
    return event


def _extract_result_and_commentary(rest: str) -> tuple[str, str]:
    """Split 'result, commentary text...' into (result, commentary)."""
    wicket_match = re.match(
        r"(out\s+.+?)(?:!!|\.\.)\s*(.*)", rest, re.DOTALL | re.IGNORECASE
    )
    if wicket_match:
        return wicket_match.group(1).strip(), wicket_match.group(2).strip()

    parts = rest.split(",", 1)
    result = parts[0].strip()
    commentary = parts[1].strip() if len(parts) > 1 else ""
    return result, commentary


def _classify_result(result: str) -> dict:
    """Classify the result text into structured fields."""
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

    if result_lower.startswith("out"):
        event["is_wicket"] = True
        wkt_match = re.search(
            r"out\s+(caught|bowled|lbw|run\s*out|stumped|hit\s*wicket)",
            result_lower,
        )
        if wkt_match:
            event["wicket_type"] = wkt_match.group(1).strip().replace(" ", "_")
        else:
            event["wicket_type"] = "unknown"
        caught_match = re.search(r"caught\s+by\s+(.+)", result, re.IGNORECASE)
        if caught_match:
            event["wicket_type"] = "caught"
        return event

    if result_lower in ("six", "six!"):
        event["runs"] = 6
        event["is_six"] = True
        event["is_boundary"] = True
        return event

    if result_lower in ("four", "four!"):
        event["runs"] = 4
        event["is_boundary"] = True
        return event

    if "wide" in result_lower:
        event["extras"] = 1
        event["extras_type"] = "wide"
        run_match = re.search(r"(\d+)\s*run", result_lower)
        if run_match:
            event["extras"] += int(run_match.group(1))
        return event

    if "no ball" in result_lower or "no-ball" in result_lower:
        event["extras"] = 1
        event["extras_type"] = "noball"
        run_match = re.search(r"(\d+)\s*run", result_lower)
        if run_match:
            event["runs"] = int(run_match.group(1))
        return event

    if "leg bye" in result_lower or "leg byes" in result_lower:
        event["extras_type"] = "legbye"
        run_match = re.search(r"(\d+)\s*run", result_lower)
        if run_match:
            event["extras"] = int(run_match.group(1))
        else:
            event["extras"] = 1
        return event

    if "bye" in result_lower and "leg" not in result_lower:
        event["extras_type"] = "bye"
        run_match = re.search(r"(\d+)\s*run", result_lower)
        if run_match:
            event["extras"] = int(run_match.group(1))
        else:
            event["extras"] = 1
        return event

    if result_lower in ("no run", "no runs"):
        event["runs"] = 0
        return event

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


def _detect_innings_breaks(events: list[dict]) -> list[list[dict]]:
    """Split events into innings based on over number resets."""
    if not events:
        return []
    innings: list[list[dict]] = []
    current: list[dict] = [events[0]]
    prev_over = events[0]["over"]
    for event in events[1:]:
        if event["over"] < prev_over - 1:
            innings.append(current)
            current = []
        current.append(event)
        prev_over = event["over"]
    if current:
        innings.append(current)
    return innings


def parse_live(html_path: Path) -> list[list[dict]]:
    """
    Parse Cricbuzz ball-by-ball live commentary HTML.
    Returns a list of innings, each a list of ball events in chronological order.
    """
    html = html_path.read_text(encoding="utf-8")

    parts = re.split(r'min-w-\[1\.5rem\]">', html)
    events: list[dict] = []
    for part in parts[1:]:
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
        event = _parse_ball_text(over_ball, text)
        if event:
            events.append(event)

    events.reverse()
    return _detect_innings_breaks(events)


# ──────────────────────────────────────────────────────────────────────
# 5. merge_all  —  combine into final JSON
# ──────────────────────────────────────────────────────────────────────

def merge_all(
    match_info: dict,
    players: list[dict],
    scorecard_innings: list[dict],
    live_innings: list[list[dict]],
) -> dict:
    """
    Combine data from all four parsers into the final JSON structure.

    The output is backward-compatible with ``ind_vs_sa_final.json``
    (the ``innings[].balls`` field uses the same schema), but includes
    enriched data from the scorecard and squads parsers.
    """
    # Build merged innings list
    innings_out: list[dict] = []
    target = None

    for i, sc_inn in enumerate(scorecard_innings):
        inn_num = sc_inn.get("innings_number", i + 1)
        batting_team = sc_inn.get("batting_team", "")
        bowling_team = sc_inn.get("bowling_team", "")

        # Match live innings to scorecard innings by index
        balls = live_innings[i] if i < len(live_innings) else []

        # Calculate target for 2nd innings
        if i == 0:
            target = sc_inn.get("total_runs", 0) + 1
        inn_target = target if i >= 1 else None

        innings_out.append({
            "innings_number": inn_num,
            "batting_team": batting_team,
            "bowling_team": bowling_team,
            "total_runs": sc_inn.get("total_runs", 0),
            "total_wickets": sc_inn.get("total_wickets", 0),
            "total_overs": sc_inn.get("total_overs", 0.0),
            "run_rate": sc_inn.get("run_rate", 0.0),
            "extras_total": sc_inn.get("extras_total", 0),
            "extras_detail": sc_inn.get("extras_detail", {}),
            "target": inn_target,
            "batters": sc_inn.get("batters", []),
            "bowlers": sc_inn.get("bowlers", []),
            "fall_of_wickets": sc_inn.get("fall_of_wickets", []),
            "partnerships": sc_inn.get("partnerships", []),
            "powerplays": sc_inn.get("powerplays", []),
            "did_not_bat": sc_inn.get("did_not_bat", []),
            "balls": balls,
        })

    return {
        "match_info": match_info,
        "players": players,
        "innings": innings_out,
    }


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

def find_files(folder: Path) -> dict[str, Path]:
    """
    Auto-detect the four HTML files in a match folder by naming convention.
    Looks for files ending with _info.html, _scorecard.html, _squads.html, _live.html.
    """
    files: dict[str, Path] = {}
    for p in folder.iterdir():
        if not p.is_file() or p.suffix != ".html":
            continue
        stem = p.stem.lower()
        if stem.endswith("_info"):
            files["info"] = p
        elif stem.endswith("_scorecard"):
            files["scorecard"] = p
        elif stem.endswith("_squads"):
            files["squads"] = p
        elif stem.endswith("_live"):
            files["live"] = p
    return files


def main():
    parser = argparse.ArgumentParser(
        description="Convert Cricbuzz HTML match pages to structured JSON"
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="Path to match folder containing *_info.html, *_scorecard.html, *_squads.html, *_live.html",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output JSON path (default: data/sample/<folder_name>.json)",
    )
    args = parser.parse_args()

    folder = args.folder.resolve()
    if not folder.is_dir():
        print(f"ERROR: {folder} is not a directory")
        sys.exit(1)

    files = find_files(folder)
    missing = [k for k in ("info", "scorecard", "squads", "live") if k not in files]
    if missing:
        print(f"ERROR: Missing HTML files in {folder}: {', '.join(missing)}")
        print(f"  Expected files ending with: _info.html, _scorecard.html, _squads.html, _live.html")
        sys.exit(1)

    print(f"Match folder: {folder}")
    for kind, path in sorted(files.items()):
        print(f"  {kind:12s}: {path.name}")

    # --- Parse each file ---

    print("\nParsing info...")
    match_info = parse_info(files["info"])
    print(f"  Title: {match_info.get('title', '?')}")
    print(f"  Teams: {match_info.get('teams', [])}")
    print(f"  Result: {match_info.get('result', '?')}")

    print("\nParsing squads...")
    # Build team map from info
    team_map = dict(TEAM_SHORT_TO_FULL)
    if match_info.get("team1_short") and match_info.get("team1"):
        team_map[match_info["team1_short"]] = match_info["team1"]
    if match_info.get("team2_short") and match_info.get("team2"):
        team_map[match_info["team2_short"]] = match_info["team2"]
    players = parse_squads(files["squads"], team_map)
    playing_xi = [p for p in players if p["player_status"] == "Playing XI"]
    bench = [p for p in players if p["player_status"] == "Bench"]
    staff = [p for p in players if p["player_status"] == "Support Staff"]
    print(f"  Playing XI: {len(playing_xi)}, Bench: {len(bench)}, Staff: {len(staff)}")

    print("\nParsing scorecard...")
    scorecard_innings, result_text = parse_scorecard(files["scorecard"])
    # Fill in result from scorecard if info page didn't have it
    if result_text and not match_info.get("result"):
        match_info["result"] = result_text
    for inn in scorecard_innings:
        print(f"  Innings {inn['innings_number']}: {inn['batting_team']} "
              f"{inn['total_runs']}/{inn['total_wickets']} ({inn['total_overs']} Ov) "
              f"— {len(inn['batters'])} batters, {len(inn['bowlers'])} bowlers, "
              f"{len(inn['fall_of_wickets'])} FOW, {len(inn['partnerships'])} partnerships")

    print("\nParsing live commentary...")
    live_innings = parse_live(files["live"])
    for i, balls in enumerate(live_innings):
        total = sum(e["runs"] + e["extras"] for e in balls)
        wkts = sum(1 for e in balls if e["is_wicket"])
        first_ball = f"{balls[0]['over']}.{balls[0]['ball']}" if balls else "?"
        last_ball = f"{balls[-1]['over']}.{balls[-1]['ball']}" if balls else "?"
        print(f"  Innings {i+1}: {len(balls)} balls, {total}/{wkts}, {first_ball} - {last_ball}")

    # --- Merge ---
    print("\nMerging...")
    result = merge_all(match_info, players, scorecard_innings, live_innings)

    # --- Write ---
    output_path = args.output
    if output_path is None:
        output_path = folder / f"{folder.name}.json"

    output_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    total_balls = sum(len(inn["balls"]) for inn in result["innings"])
    print(f"\nWritten to {output_path}")
    print(f"  Innings: {len(result['innings'])}")
    print(f"  Players: {len(result['players'])}")
    print(f"  Total deliveries: {total_balls}")


if __name__ == "__main__":
    main()
