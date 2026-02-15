from app.models import BallEvent, MatchState


def detect_pivot(state: MatchState, ball: BallEvent) -> bool:
    """
    Detect if this ball is a high-leverage "pivot" moment.

    A pivot is a single delivery that significantly shifts the match equation.
    Examples:
    - A six when RRR > 12
    - A wicket of a set batter (30+ runs)
    - A boundary in the death overs when runs needed per ball > 1.5
    - Back-to-back boundaries
    - A wicket when only 2 wickets remain
    """
    # Six when required rate is very high
    if ball.is_six and state.rrr > 12:
        return True

    # Boundary in death overs with high equation pressure
    if ball.is_boundary and state.match_phase == "death":
        runs_per_ball_needed = state.runs_needed / max(state.balls_remaining, 1)
        if runs_per_ball_needed > 1.5:
            return True

    # Wicket of a set batter
    if ball.is_wicket:
        dismissed = ball.dismissal_batter or ball.batter
        if dismissed in state.batters:
            batter = state.batters[dismissed]
            if batter.runs >= 30:
                return True
        # Wicket when tail is exposed (7+ wickets down)
        if state.wickets >= 7:
            return True

    # Back-to-back boundaries (last ball was also 4+)
    if (ball.is_boundary or ball.is_six) and len(state.last_6_balls) >= 2:
        if state.last_6_balls[-2] >= 4:
            return True

    # Extras (wide/no-ball) in a very tight game
    if ball.extras > 0 and state.balls_remaining <= 12 and state.runs_needed <= 20:
        return True

    return False


def calculate_equation_shift(
    state: MatchState, ball: BallEvent
) -> str | None:
    """
    Calculate how the match equation shifted due to this ball.
    Returns a human-readable string like 'From 12 per over down to 9.5'.
    """
    total_runs_on_ball = ball.runs + ball.extras
    if total_runs_on_ball <= 1:
        return None

    # Calculate what RRR was before this ball
    balls_before = state.balls_remaining + (1 if ball.extras_type not in ("wide", "noball") else 0)
    runs_before = state.runs_needed + total_runs_on_ball
    overs_before = balls_before / 6

    if overs_before <= 0:
        return None

    rrr_before = round(runs_before / overs_before, 1)
    rrr_after = state.rrr

    if abs(rrr_before - rrr_after) < 0.5:
        return None

    return f"From {rrr_before} per over down to {rrr_after}"
