from app.models import BallEvent, BatsmanStats, BowlerStats, FallOfWicket, MatchState


class StateManager:
    """
    Maintains the live match state, updating it ball by ball.
    Tracks score, run rates, momentum, milestones, per-batsman/bowler stats,
    fall of wickets, over history, boundary droughts, extras breakdown,
    partnership details, batting order, and phase-wise scoring.
    """

    def __init__(self, batting_team: str, bowling_team: str, target: int) -> None:
        self._next_batting_position = 1
        self.state = MatchState(
            batting_team=batting_team,
            bowling_team=bowling_team,
            target=target,
        )

    def update(self, ball: BallEvent) -> MatchState:
        """Process a single ball event and return the updated match state."""
        s = self.state

        # --- Detect transitions BEFORE updating ---
        self._detect_transitions(ball)

        # --- Runs & extras ---
        total_ball_runs = ball.runs + ball.extras
        s.total_runs += total_ball_runs

        # --- Extras breakdown ---
        if ball.extras > 0:
            s.total_extras += ball.extras
            if ball.extras_type == "wide":
                s.total_wides += ball.extras
            elif ball.extras_type == "noball":
                s.total_noballs += ball.extras

        # --- Ball counting (wides/no-balls don't count as legal deliveries) ---
        is_legal = ball.extras_type not in ("wide", "noball")
        if is_legal:
            s.balls_in_current_over += 1
            s.total_balls_bowled += 1

        # --- Momentum tracking (last 6 legal deliveries) ---
        if is_legal:
            s.last_6_balls.append(ball.runs)
            if len(s.last_6_balls) > 6:
                s.last_6_balls.pop(0)

        # --- Consecutive dots ---
        is_dot = ball.runs == 0 and ball.extras == 0 and not ball.is_wicket
        if is_dot:
            s.consecutive_dots += 1
        else:
            s.consecutive_dots = 0

        # --- Current over stats ---
        s.current_over_runs += total_ball_runs
        if ball.is_wicket:
            s.current_over_wickets += 1

        # --- Boundary & drought tracking ---
        if ball.is_boundary or ball.is_six:
            s.balls_since_last_boundary = 0
            if ball.is_boundary:
                s.total_fours += 1
            if ball.is_six:
                s.total_sixes += 1
        elif is_legal:
            s.balls_since_last_boundary += 1

        # --- Balls since last wicket ---
        if is_legal:
            s.balls_since_last_wicket += 1

        # --- Batsman stats ---
        batsman_name = ball.batsman
        if batsman_name not in s.batsmen:
            s.batsmen[batsman_name] = BatsmanStats(
                name=batsman_name, position=self._next_batting_position
            )
            s.batting_order.append(batsman_name)
            self._next_batting_position += 1

        batter = s.batsmen[batsman_name]
        if is_legal:
            batter.balls_faced += 1
            if is_dot:
                batter.dots += 1
        batter.runs += ball.runs
        if ball.is_boundary:
            batter.fours += 1
        if ball.is_six:
            batter.sixes += 1

        # Track non-striker too
        if ball.non_striker and ball.non_striker not in s.batsmen:
            s.batsmen[ball.non_striker] = BatsmanStats(
                name=ball.non_striker, position=self._next_batting_position
            )
            s.batting_order.append(ball.non_striker)
            self._next_batting_position += 1

        # --- Bowler stats ---
        bowler_name = ball.bowler
        if bowler_name not in s.bowlers:
            s.bowlers[bowler_name] = BowlerStats(name=bowler_name)
        bowler = s.bowlers[bowler_name]
        bowler.runs_conceded += total_ball_runs
        if is_legal:
            bowler.balls_bowled += 1
            if is_dot:
                bowler.dots += 1
        if ball.is_boundary:
            bowler.fours_conceded += 1
        if ball.is_six:
            bowler.sixes_conceded += 1
        if ball.extras_type == "wide":
            bowler.wides += ball.extras
        elif ball.extras_type == "noball":
            bowler.noballs += ball.extras
        if ball.is_wicket and ball.wicket_type != "run_out":
            bowler.wickets += 1

        # --- Partnership tracking ---
        if is_legal:
            s.partnership_balls += 1
        s.partnership_runs += total_ball_runs

        # --- Wickets ---
        if ball.is_wicket:
            s.wickets += 1
            dismissed = ball.dismissal_batsman or ball.batsman
            if dismissed in s.batsmen:
                s.batsmen[dismissed].is_out = True

            # Log fall of wicket
            s.fall_of_wickets.append(FallOfWicket(
                wicket_number=s.wickets,
                batsman=dismissed,
                batsman_runs=s.batsmen[dismissed].runs if dismissed in s.batsmen else 0,
                team_score=s.total_runs,
                overs=s.overs_display,
                bowler=ball.bowler,
                how=ball.wicket_type,
            ))

            # Reset partnership & wicket distance
            s.partnership_runs = 0
            s.partnership_balls = 0
            s.partnership_number += 1
            s.balls_since_last_wicket = 0

        # --- Over transition ---
        if s.balls_in_current_over >= 6:
            # Maiden detection
            if s.current_over_runs == 0 and bowler_name in s.bowlers:
                s.bowlers[bowler_name].maidens += 1
            # Store over runs in history
            s.over_runs_history.append(s.current_over_runs)
            # Build over summary before resetting
            s.previous_over_summary = (
                f"Over {s.overs_completed}: {s.current_over_runs} runs, "
                f"{s.current_over_wickets} wicket(s). "
                f"Bowler: {ball.bowler} — figures: {s.bowlers[bowler_name].figures_str}"
            )
            s.overs_completed += 1
            s.balls_in_current_over = 0
            s.current_over_runs = 0
            s.current_over_wickets = 0

        # --- Update current batsman/bowler/non-striker ---
        s.previous_batsman = s.current_batsman
        s.previous_bowler = s.current_bowler
        s.current_batsman = ball.batsman
        s.current_bowler = ball.bowler
        s.non_striker = ball.non_striker

        return s

    def _detect_transitions(self, ball: BallEvent) -> None:
        """Detect bowler changes, strike changes, new batsmen before state update."""
        s = self.state

        # Reset transition flags
        s.is_new_bowler = False
        s.is_new_over = False
        s.is_strike_change = False
        s.is_new_batsman = False
        s.new_batsman_name = None

        # First ball of the match
        if s.current_bowler is None:
            return

        # New bowler?
        if ball.bowler != s.current_bowler:
            s.is_new_bowler = True
            # New bowler almost always means new over
            s.is_new_over = True

        # Strike change? (different batsman on strike vs previous ball)
        if s.current_batsman and ball.batsman != s.current_batsman:
            s.is_strike_change = True

        # New batsman? (someone we haven't seen batting before)
        if ball.batsman not in s.batsmen:
            s.is_new_batsman = True
            s.new_batsman_name = ball.batsman
        if ball.non_striker and ball.non_striker not in s.batsmen:
            s.is_new_batsman = True
            s.new_batsman_name = ball.non_striker

    def get_state(self) -> MatchState:
        """Return the current match state."""
        return self.state

    def get_batsman_milestone(self, batsman_name: str) -> str | None:
        """Check if a batsman just reached or is approaching a milestone."""
        if batsman_name not in self.state.batsmen:
            return None
        batter = self.state.batsmen[batsman_name]
        if batter.approaching_hundred:
            return "approaching_100"
        if batter.approaching_fifty:
            return "approaching_50"
        if batter.runs >= 100:
            return "century"
        if batter.runs >= 50:
            return "half_century"
        return None
