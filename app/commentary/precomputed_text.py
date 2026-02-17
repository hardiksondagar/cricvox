"""
Generate simple precomputed text for commentary skeletons.

Used when inserting skeletons so the timeline has displayable text before
LLM generation. LLM will replace with sophisticated versions.
"""


def _fmt(d: dict, key: str, default: str = "") -> str:
    return str(d.get(key, default) or default)


def precomputed_delivery_text(delivery: dict) -> str:
    """Simple delivery description from ball data."""
    runs = (delivery.get("runs") or 0) + (delivery.get("extras") or 0)
    oc = delivery.get("overs_completed", delivery.get("over", 0))
    bio = delivery.get("balls_in_over", delivery.get("ball", 1))
    overs = f"{oc}.{bio}"
    total = delivery.get("total_runs", 0)
    wkts = delivery.get("total_wickets", 0)

    parts = [f"{delivery.get('batter', '')} scores {runs} run(s)"]
    if delivery.get("is_wicket"):
        parts.append("— WICKET")
    elif delivery.get("is_six"):
        parts.append("— SIX")
    elif delivery.get("is_boundary"):
        parts.append("— FOUR")
    parts.append(f"off {delivery.get('bowler', '')}. {total}/{wkts} after {overs}.")
    return " ".join(parts)


def precomputed_first_innings_start_text(match_info: dict, first_innings: dict | None = None) -> str:
    """Match intro text — includes match info + first innings."""
    team1 = _fmt(match_info, "team1") or _fmt(match_info, "batting_team")
    team2 = _fmt(match_info, "team2") or _fmt(match_info, "bowling_team")
    venue = _fmt(match_info, "venue", "TBD")
    parts = [f"Match begins: {team1} vs {team2} at {venue}."]
    if first_innings:
        batting = _fmt(first_innings, "batting_team") or team1
        bowling = _fmt(first_innings, "bowling_team") or team2
        parts.append(f"{batting} to bat first, {bowling} to bowl.")
    return " ".join(parts)


def precomputed_first_innings_end_text(first_innings: dict) -> str:
    """Innings break summary."""
    batting = _fmt(first_innings, "batting_team")
    runs = _fmt(first_innings, "total_runs", "0")
    wkts = _fmt(first_innings, "total_wickets", "0")
    return f"Innings break. {batting} finish on {runs}/{wkts}."


def precomputed_second_innings_start_text(match_info: dict, first_innings: dict) -> str:
    """Chase begins."""
    target = _fmt(match_info, "target", "0")
    batting = _fmt(match_info, "batting_team")
    return f"Chase begins. {batting} need {target} to win."


def precomputed_end_of_over_text(data: dict) -> str:
    """End of over summary."""
    over = data.get("over", data.get("overs_completed", "?"))
    runs = data.get("over_runs", 0)
    bowler = data.get("bowler", "")
    wkts = data.get("over_wickets", 0)
    parts = [f"End of over {over}. {runs} runs"]
    if wkts:
        parts.append(f"and {wkts} wicket(s)")
    parts.append(f"from {bowler}." if bowler else ".")
    return " ".join(parts)


def precomputed_phase_change_text(data: dict) -> str:
    """Phase transition."""
    new_phase = data.get("new_phase", "Middle Overs")
    summary = data.get("phase_summary", "")
    if summary:
        return f"Phase change: {new_phase}. {summary}"
    return f"Phase change: now in {new_phase}."


def precomputed_second_innings_end_text(data: dict) -> str:
    """Match end summary."""
    result = data.get("result", "complete")
    score = data.get("final_score", "")
    overs = data.get("overs", "")
    if score:
        return f"Match over. {result.capitalize()} — {score} in {overs} overs."
    return f"Match over. {result.capitalize()}."
