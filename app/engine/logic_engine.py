from app.models import BallEvent, LogicResult, MatchState, NarrativeBranch
from app.engine.pivot_detector import calculate_equation_shift, detect_pivot


class LogicEngine:
    """
    The Commentary Brain: categorizes every ball into a Narrative Branch
    and provides context for the LLM prompt.
    """

    def analyze(self, state: MatchState, ball: BallEvent) -> LogicResult:
        """Classify a ball event and return the narrative branch + context."""
        branch = self._classify_branch(state, ball)
        is_pivot = detect_pivot(state, ball)
        equation_shift = calculate_equation_shift(state, ball)
        context_notes = self._build_context(state, ball, branch)

        return LogicResult(
            branch=branch,
            is_pivot=is_pivot,
            equation_shift=equation_shift,
            context_notes=context_notes,
        )

    def _classify_branch(self, state: MatchState, ball: BallEvent) -> NarrativeBranch:
        """Determine which narrative branch this ball falls into."""

        # Over transition: last ball of the over
        if state.balls_in_current_over == 0 and state.total_balls_bowled > 0:
            # This means the over just completed with the previous ball update
            # But we check if it's the 6th ball
            pass

        # Check if this was the 6th legal ball (over just completed in state_manager)
        is_over_end = (state.balls_in_current_over == 0 and state.overs_completed > 0
                       and ball.extras_type not in ("wide", "noball"))

        # Wicket takes top priority
        if ball.is_wicket:
            return NarrativeBranch.WICKET_DRAMA

        # Extras in a close game (chase innings only for runs_needed check)
        if ball.extras > 0 and ball.extras_type in ("wide", "noball"):
            if (state.target > 0 and state.runs_needed <= 30) or state.match_phase == "death":
                return NarrativeBranch.EXTRA_GIFT

        # Boundary (4 or 6)
        if ball.is_boundary or ball.is_six:
            return NarrativeBranch.BOUNDARY_MOMENTUM

        # Pressure builder: consecutive dots
        if state.consecutive_dots >= 3:
            return NarrativeBranch.PRESSURE_BUILDER

        # Pressure builder: climbing RRR (chase innings only)
        if state.target > 0 and state.rrr > 12 and ball.runs <= 1:
            return NarrativeBranch.PRESSURE_BUILDER

        # Over transition (6th ball, no wicket/boundary)
        if is_over_end:
            return NarrativeBranch.OVER_TRANSITION

        # Default: routine ball
        return NarrativeBranch.ROUTINE

    def _build_context(
        self, state: MatchState, ball: BallEvent, branch: NarrativeBranch
    ) -> str:
        """
        Build SELECTIVE context notes for the LLM prompt.
        Not everything every ball — surface what MATTERS for THIS delivery.
        """
        notes: list[str] = []

        # ============================================================== #
        #  1. TRANSITIONS — always surface, most important for flow
        # ============================================================== #
        if state.is_new_over and state.is_new_bowler:
            notes.append(f"NEW OVER: {ball.bowler} comes into the attack")
            if state.previous_over_summary:
                notes.append(f"Previous: {state.previous_over_summary}")
        elif state.is_new_bowler:
            notes.append(f"Bowling change: {ball.bowler} replaces {state.previous_bowler}")

        if state.is_new_batter and state.new_batter_name:
            pos = state.batters[state.new_batter_name].position if state.new_batter_name in state.batters else self._next_position(state)
            notes.append(f"NEW BATTER: {state.new_batter_name} walks in at #{pos}")
        elif state.is_strike_change and not state.is_new_over:
            notes.append(f"Strike rotated: {ball.batter} now facing (was {state.previous_batter})")

        # ============================================================== #
        #  2. MATCH SITUATION — always present
        # ============================================================== #
        notes.append(f"Phase: {state.match_phase}")
        if state.balls_in_current_over > 0:
            notes.append(
                f"This over so far: {state.current_over_runs}/{state.balls_in_current_over}b"
            )

        # Match situation assessment — explicit signal to the LLM
        situation = self._assess_match_situation(state)
        if situation:
            notes.append(f"MATCH SITUATION: {situation}")

        # ============================================================== #
        #  3. BATSMAN — current form, struggles, milestones
        # ============================================================== #
        batter_name = ball.batter
        if batter_name in state.batters:
            batter = state.batters[batter_name]
            if batter.balls_faced == 0:
                notes.append(f"{batter_name} on strike, yet to face a delivery")
            elif batter.runs == 0 and batter.balls_faced > 0:
                notes.append(f"{batter_name} struggling: 0({batter.balls_faced})")
            else:
                desc = f"{batter_name}: {batter.runs}({batter.balls_faced}) SR {batter.strike_rate}"
                if batter.fours or batter.sixes:
                    desc += f" [{batter.fours}x4, {batter.sixes}x6]"
                notes.append(desc)

            # Dot ball struggle
            if batter.balls_faced >= 10 and batter.dot_percentage >= 60:
                notes.append(f"{batter_name} dot% = {batter.dot_percentage}% — struggling to rotate")

            # Milestones
            if batter.approaching_fifty:
                notes.append(f"MILESTONE: {batter_name} approaching 50 (on {batter.runs})")
            elif batter.approaching_hundred:
                notes.append(f"MILESTONE: {batter_name} approaching 100 (on {batter.runs})")

        # Non-batter
        if ball.non_batter and ball.non_batter in state.batters:
            ns = state.batters[ball.non_batter]
            if not ns.is_out:
                notes.append(f"Non-batter {ball.non_batter}: {ns.runs}({ns.balls_faced})")

        # ============================================================== #
        #  4. BOWLER — figures + context
        # ============================================================== #
        if ball.bowler in state.bowlers:
            bwl = state.bowlers[ball.bowler]
            desc = f"Bowler {ball.bowler}: {bwl.figures_str} econ {bwl.economy}"
            if bwl.dots >= 6:
                desc += f", {bwl.dots} dots"
            if bwl.fours_conceded + bwl.sixes_conceded >= 3:
                desc += f", leaked {bwl.fours_conceded}x4 {bwl.sixes_conceded}x6"
            if bwl.wides + bwl.noballs >= 2:
                desc += f", {bwl.wides}w {bwl.noballs}nb extras"
            notes.append(desc)

        # ============================================================== #
        #  5. PARTNERSHIP — only when meaningful
        # ============================================================== #
        if not ball.is_wicket and state.partnership_balls > 0:
            p_desc = (
                f"{self._ordinal(state.partnership_number)} wicket partnership: "
                f"{state.partnership_runs} off {state.partnership_balls}b"
            )
            notes.append(p_desc)

        # ============================================================== #
        #  6. WICKET CONTEXT — rich info on dismissal
        # ============================================================== #
        if ball.is_wicket:
            dismissed = ball.dismissal_batter or ball.batter
            if dismissed in state.batters:
                batter_d = state.batters[dismissed]
                notes.append(
                    f"{dismissed} out for {batter_d.runs}({batter_d.balls_faced}) "
                    f"[{batter_d.fours}x4, {batter_d.sixes}x6]"
                )
                if batter_d.runs >= 30:
                    notes.append("Set batter gone — was looking dangerous")
                if state.wickets <= 3:
                    notes.append(f"Top-order wicket, #{state.wickets} down")
                elif state.wickets >= 7:
                    notes.append(f"Tail exposed, only {10 - state.wickets} left")

            # Partnership broken
            if state.partnership_runs > 10:
                notes.append(f"{self._ordinal(state.partnership_number)} wkt stand broken at {state.partnership_runs}")

            # Collapse detection
            if state.is_collapse:
                notes.append("COLLAPSE: 3+ wickets in last 3 overs!")

            # Quick follow-up wicket
            if state.balls_since_last_wicket <= 6 and len(state.fall_of_wickets) >= 2:
                notes.append(f"Back-to-back blow! Only {state.balls_since_last_wicket}b since last wicket")

            # FOW summary
            if state.fall_of_wickets:
                fow_str = ", ".join(
                    f"{f.wicket_number}/{f.team_score}({f.overs})"
                    for f in state.fall_of_wickets[-3:]  # last 3 wickets
                )
                notes.append(f"FOW: {fow_str}")

        # ============================================================== #
        #  7. MOMENTUM & SCORING PATTERNS
        #     Skip noise on wicket balls — the dismissal IS the story.
        # ============================================================== #
        if not ball.is_wicket:
            # Last 6 balls
            if len(state.last_6_balls) >= 6:
                recent_runs = sum(state.last_6_balls)
                notes.append(f"Last 6 balls: {recent_runs} runs {state.last_6_balls}")

            # Consecutive dots
            if state.consecutive_dots >= 3:
                notes.append(f"{state.consecutive_dots} consecutive dot balls")

            # Boundary drought (only if significant)
            if state.is_boundary_drought:
                notes.append(
                    f"BOUNDARY DROUGHT: {state.balls_since_last_boundary} balls "
                    f"without a boundary!"
                )
            elif state.balls_since_last_boundary >= 12:
                notes.append(f"No boundary for {state.balls_since_last_boundary} balls")

            # Scoring momentum direction
            if state.scoring_momentum in ("accelerating", "decelerating"):
                notes.append(f"Scoring is {state.scoring_momentum}")

            # Run rate comparison (only after 5+ overs)
            if state.run_rate_last_3_overs > 0:
                notes.append(
                    f"Last 3 overs: {state.run_rate_last_3_overs} RPO "
                    f"(vs match CRR {state.crr})"
                )

        # ============================================================== #
        #  8. INNINGS STATS — surface selectively
        #     Skip on wicket balls (except equation in death).
        # ============================================================== #
        if not ball.is_wicket:
            # Total boundaries (only at milestones or context switches)
            total_boundaries = state.total_fours + state.total_sixes
            if total_boundaries > 0 and (ball.is_boundary or ball.is_six):
                notes.append(
                    f"Innings boundaries: {state.total_fours}x4, {state.total_sixes}x6 "
                    f"({state.boundary_runs_percentage:.0f}% of runs from boundaries)"
                )

            # Extras (only if notable)
            if state.total_extras >= 5:
                notes.append(
                    f"Extras so far: {state.total_extras} "
                    f"({state.total_wides}w, {state.total_noballs}nb)"
                )

            # Dot ball percentage (only if high and in middle/death)
            if state.match_phase != "powerplay" and state.dot_ball_percentage >= 50:
                notes.append(f"Innings dot%: {state.dot_ball_percentage}%")

            # Phase summary (after powerplay ends)
            if state.match_phase == "middle" and state.overs_completed == 6:
                notes.append(f"Powerplay finished: {state.powerplay_runs} runs")

        # ============================================================== #
        #  9. EQUATION — death overs, chase only (always show, even on wickets)
        # ============================================================== #
        if state.match_phase == "death" and state.target > 0:
            notes.append(
                f"Need {state.runs_needed} from {state.balls_remaining} balls "
                f"(RRR {state.rrr})"
            )

        return ". ".join(notes)

    @staticmethod
    def _assess_match_situation(state: MatchState) -> str:
        """
        Assess the overall match situation so the LLM knows the real picture.
        Returns a clear, honest label — no false hope, no fake tension.

        Only applies to the chase innings (target > 0). First innings has no
        chase equation, so return empty.
        """
        # First innings — no chase target, situation assessment doesn't apply
        if state.target <= 0:
            return ""

        rrr = state.rrr
        wickets = state.wickets
        balls = state.balls_remaining
        needed = state.runs_needed

        # Match already won
        if needed <= 0:
            return "MATCH WON by batting team"

        # All out
        if wickets >= 10:
            return "ALL OUT — batting team loses"

        # Virtually impossible: tail exposed + insane RRR
        if wickets >= 8 and rrr > 15:
            return "GAME OVER — virtually impossible, tail exposed, need {:.1f} RPO".format(rrr)

        if wickets >= 7 and rrr > 18:
            return "GAME OVER — need {:.1f} RPO with only {} wickets left".format(rrr, 10 - wickets)

        # Very unlikely: deep trouble
        if wickets >= 6 and rrr > 15:
            return "DEEP TROUBLE — need {:.1f} RPO with {} down, odds stacked against batting team".format(rrr, wickets)

        if rrr > 20 and balls <= 12:
            return "GAME OVER — need {} off {} balls, mathematically near-impossible".format(needed, balls)

        if rrr > 15 and balls <= 18:
            return "LAST GASP — need {} off {} balls at {:.1f} RPO, need boundaries every ball".format(needed, balls, rrr)

        # Tough but alive
        if rrr > 12 and wickets >= 5:
            return "UPHILL — need {:.1f} RPO with {} down, batting team in serious trouble".format(rrr, wickets)

        if rrr > 12 and wickets < 5:
            return "TOUGH — need {:.1f} RPO but wickets in hand, need big hitting".format(rrr)

        # Tight contest
        if 9 <= rrr <= 12 and balls <= 30:
            return "TIGHT — need {} off {} balls at {:.1f} RPO, game in the balance".format(needed, balls, rrr)

        # Under control
        if rrr < 6 and wickets <= 4:
            return "COMFORTABLE — batting team cruising at {:.1f} RPO with wickets in hand".format(rrr)

        if rrr < 8 and wickets <= 3:
            return "IN CONTROL — batting team on track, {:.1f} RPO required".format(rrr)

        # Last over drama
        if balls <= 6:
            if needed <= 6:
                return "LAST OVER THRILLER — need {} off {} balls, anyone's game!".format(needed, balls)
            elif needed <= 12:
                return "LAST OVER — need {} off {} balls, need boundaries".format(needed, balls)
            else:
                return "LAST OVER — need {} off {} balls, virtually impossible".format(needed, balls)

        # Default: normal contest
        return ""

    @staticmethod
    def _ordinal(n: int) -> str:
        """Return ordinal string for a number: 1st, 2nd, 3rd, etc."""
        if 11 <= (n % 100) <= 13:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"

    @staticmethod
    def _next_position(state: MatchState) -> int:
        """Get the next batting position number."""
        return len(state.batting_order) + 1
