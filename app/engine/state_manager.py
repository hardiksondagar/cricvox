from app.models import BallEvent, BatterStats, BowlerStats, FallOfWicket, MatchState


class StateManager:
    """
    Maintains the live match state, updating it ball by ball.
    Tracks score, run rates, momentum, milestones, per-batter/bowler stats,
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

        # --- Batter stats ---
        batter_name = ball.batter
        if batter_name not in s.batters:
            s.batters[batter_name] = BatterStats(
                name=batter_name, position=self._next_batting_position
            )
            s.batting_order.append(batter_name)
            self._next_batting_position += 1

        batter = s.batters[batter_name]
        if is_legal:
            batter.balls_faced += 1
            if is_dot:
                batter.dots += 1
        batter.runs += ball.runs
        if ball.is_boundary:
            batter.fours += 1
        if ball.is_six:
            batter.sixes += 1

        # Track non-batter too
        if ball.non_batter and ball.non_batter not in s.batters:
            s.batters[ball.non_batter] = BatterStats(
                name=ball.non_batter, position=self._next_batting_position
            )
            s.batting_order.append(ball.non_batter)
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
            dismissed = ball.dismissal_batter or ball.batter
            if dismissed in s.batters:
                s.batters[dismissed].is_out = True

            # Determine partner (the other batter at the crease)
            partner = None
            active = [n for n in s.batters if not s.batters[n].is_out and n != dismissed]
            if active:
                partner = active[0]

            # Log fall of wicket
            s.fall_of_wickets.append(FallOfWicket(
                wicket_number=s.wickets,
                batter=dismissed,
                batter_runs=s.batters[dismissed].runs if dismissed in s.batters else 0,
                team_score=s.total_runs,
                overs=s.overs_display,
                bowler=ball.bowler,
                how=ball.wicket_type,
                partner=partner,
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

        # --- Update current batter/bowler/non-batter ---
        s.previous_batter = s.current_batter
        s.previous_bowler = s.current_bowler
        s.current_batter = ball.batter
        s.current_bowler = ball.bowler

        # Non-batter: use explicit value if provided, otherwise infer
        if ball.non_batter:
            s.non_batter = ball.non_batter
        else:
            active = [n for n, st in s.batters.items() if not st.is_out and n != ball.batter]
            s.non_batter = active[0] if active else None

        return s

    def _detect_transitions(self, ball: BallEvent) -> None:
        """Detect bowler changes, strike changes, new batters before state update."""
        s = self.state

        # Reset transition flags
        s.is_new_bowler = False
        s.is_new_over = False
        s.is_strike_change = False
        s.is_new_batter = False
        s.new_batter_name = None

        # First ball of the match
        if s.current_bowler is None:
            return

        # New bowler?
        if ball.bowler != s.current_bowler:
            s.is_new_bowler = True
            # New bowler almost always means new over
            s.is_new_over = True

        # Strike change? (different batter on strike vs previous ball)
        if s.current_batter and ball.batter != s.current_batter:
            s.is_strike_change = True

        # New batter? (someone we haven't seen batting before)
        if ball.batter not in s.batters:
            s.is_new_batter = True
            s.new_batter_name = ball.batter
        if ball.non_batter and ball.non_batter not in s.batters:
            s.is_new_batter = True
            s.new_batter_name = ball.non_batter

    def get_state(self) -> MatchState:
        """Return the current match state."""
        return self.state

    def get_batter_milestone(self, batter_name: str) -> str | None:
        """Check if a batter just reached or is approaching a milestone."""
        if batter_name not in self.state.batters:
            return None
        batter = self.state.batters[batter_name]
        if batter.approaching_hundred:
            return "approaching_100"
        if batter.approaching_fifty:
            return "approaching_50"
        if batter.runs >= 100:
            return "century"
        if batter.runs >= 50:
            return "half_century"
        return None

    def get_innings_summary(self) -> dict:
        """
        Extract innings summary from the current accumulated state.

        Returns the same shape as the old compute_innings_summary() but
        derived directly from StateManager's live tracking — no re-processing
        of raw ball data needed.
        """
        s = self.state

        # --- top scorers string (batters with >= 15 runs, top 4) ---
        sorted_batters = sorted(
            s.batters.values(), key=lambda b: b.runs, reverse=True
        )
        top_scorers_str = ", ".join(
            f"{b.name} {b.runs}({b.balls_faced})"
            + (
                f" [{b.fours}x4, {b.sixes}x6]"
                if b.fours + b.sixes > 0
                else ""
            )
            for b in sorted_batters[:4]
            if b.runs >= 15
        )

        # --- top bowlers string (bowlers with wickets, top 3) ---
        sorted_bowlers = sorted(
            s.bowlers.values(),
            key=lambda bw: (-bw.wickets, bw.economy),
        )
        top_bowlers_str = ", ".join(
            f"{bw.name} {bw.figures_str}"
            for bw in sorted_bowlers[:3]
            if bw.wickets > 0
        )

        # --- detailed batters list ---
        batters_list = [
            {
                "name": b.name,
                "runs": b.runs,
                "balls": b.balls_faced,
                "fours": b.fours,
                "sixes": b.sixes,
                "sr": round(b.strike_rate, 1),
            }
            for b in sorted_batters
        ]

        # --- detailed bowlers list (preserve bowling order) ---
        bowlers_list = [
            {
                "name": bw.name,
                "balls": bw.balls_bowled,
                "runs": bw.runs_conceded,
                "wickets": bw.wickets,
                "overs": bw.overs_display,
                "economy": round(bw.economy, 2),
            }
            for bw in s.bowlers.values()
        ]

        return {
            "batting_team": s.batting_team,
            "bowling_team": s.bowling_team,
            "total_runs": s.total_runs,
            "total_wickets": s.wickets,
            "top_scorers": top_scorers_str,
            "top_bowlers": top_bowlers_str,
            "total_fours": s.total_fours,
            "total_sixes": s.total_sixes,
            "total_extras": s.total_extras,
            "batters": batters_list,
            "bowlers": bowlers_list,
        }
