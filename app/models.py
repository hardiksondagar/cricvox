from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# =========================================================================== #
#  Language Support Configuration
# =========================================================================== #

# Common naturalness rule appended to every Indian language instruction
_NATURAL_SPEECH_RULE = (
    "CRITICAL — NATURAL SPEECH, NOT TRANSLATION: "
    "Do NOT translate English commentary word-by-word. "
    "Write as a NATIVE speaker would actually talk while watching cricket with friends or on local TV. "
    "Use colloquial, everyday spoken language — the way real people speak, not textbook/literary language. "
    "If an English phrase has no natural equivalent, just say it in English — that is how real code-mixing works. "
    "Avoid awkward literal translations that no native speaker would ever say. "
    "Numbers, scores, and player names should stay in English/Latin script for clarity."
)

SUPPORTED_LANGUAGES: dict[str, dict] = {
    "en": {
        "name": "English",
        "native_name": "English",
        "llm_instruction": "",
        "elevenlabs_model": "eleven_v3",
        "sarvam_language_code": "en-IN",
    },
    "hi": {
        "name": "Hindi",
        "native_name": "हिन्दी",
        "llm_instruction": (
            "LANGUAGE: Generate ALL commentary in HINDI (Devanagari script). "
            "Cricket terms like 'boundary', 'wicket', 'six', 'four', 'over', 'run rate', "
            "'powerplay', 'maiden', 'dot ball', 'no ball', 'wide', 'free hit', 'run out', "
            "'caught', 'bowled', 'LBW' should stay in ENGLISH — this is natural Hinglish "
            "as Indian TV commentators speak. Everything else MUST be in Hindi. "
            + _NATURAL_SPEECH_RULE
        ),
        "elevenlabs_model": "eleven_v3",
        "sarvam_language_code": "hi-IN",
    },
    "ta": {
        "name": "Tamil",
        "native_name": "தமிழ்",
        "llm_instruction": (
            "LANGUAGE: Generate ALL commentary in TAMIL (Tamil script). "
            "Cricket terms like 'boundary', 'wicket', 'six', 'four', 'over', 'run rate', "
            "'powerplay', 'maiden', 'dot ball', 'no ball', 'wide' should stay in ENGLISH — "
            "this is natural Tamil-English code-mixing as Tamil TV commentators speak. "
            "Everything else MUST be in Tamil. "
            + _NATURAL_SPEECH_RULE
        ),
        "elevenlabs_model": "eleven_v3",
        "sarvam_language_code": "ta-IN",
    },
    "te": {
        "name": "Telugu",
        "native_name": "తెలుగు",
        "llm_instruction": (
            "LANGUAGE: Generate ALL commentary in TELUGU (Telugu script). "
            "Cricket terms like 'boundary', 'wicket', 'six', 'four', 'over', 'run rate', "
            "'powerplay', 'maiden', 'dot ball' should stay in ENGLISH — "
            "this is natural Telugu-English code-mixing. Everything else MUST be in Telugu. "
            + _NATURAL_SPEECH_RULE
        ),
        "elevenlabs_model": "eleven_v3",
        "sarvam_language_code": "te-IN",
    },
    "kn": {
        "name": "Kannada",
        "native_name": "ಕನ್ನಡ",
        "llm_instruction": (
            "LANGUAGE: Generate ALL commentary in KANNADA (Kannada script). "
            "Cricket terms like 'boundary', 'wicket', 'six', 'four', 'over', 'run rate', "
            "'powerplay', 'maiden', 'dot ball' should stay in ENGLISH — "
            "natural Kannada-English code-mixing. Everything else MUST be in Kannada. "
            + _NATURAL_SPEECH_RULE
        ),
        "elevenlabs_model": "eleven_v3",
        "sarvam_language_code": "kn-IN",
    },
    "ml": {
        "name": "Malayalam",
        "native_name": "മലയാളം",
        "llm_instruction": (
            "LANGUAGE: Generate ALL commentary in MALAYALAM (Malayalam script). "
            "Cricket terms like 'boundary', 'wicket', 'six', 'four', 'over', 'run rate', "
            "'powerplay', 'maiden', 'dot ball' should stay in ENGLISH — "
            "natural Malayalam-English code-mixing. Everything else MUST be in Malayalam. "
            + _NATURAL_SPEECH_RULE
        ),
        "elevenlabs_model": "eleven_v3",
        "sarvam_language_code": "ml-IN",
    },
    "bn": {
        "name": "Bengali",
        "native_name": "বাংলা",
        "llm_instruction": (
            "LANGUAGE: Generate ALL commentary in BENGALI (Bengali script). "
            "Cricket terms like 'boundary', 'wicket', 'six', 'four', 'over', 'run rate', "
            "'powerplay', 'maiden', 'dot ball' should stay in ENGLISH — "
            "natural Bengali-English code-mixing. Everything else MUST be in Bengali. "
            + _NATURAL_SPEECH_RULE
        ),
        "elevenlabs_model": "eleven_v3",
        "sarvam_language_code": "bn-IN",
    },
    "mr": {
        "name": "Marathi",
        "native_name": "मराठी",
        "llm_instruction": (
            "LANGUAGE: Generate ALL commentary in MARATHI (Devanagari script). "
            "Cricket terms like 'boundary', 'wicket', 'six', 'four', 'over', 'run rate', "
            "'powerplay', 'maiden', 'dot ball' should stay in ENGLISH — "
            "natural Marathi-English code-mixing. Everything else MUST be in Marathi. "
            + _NATURAL_SPEECH_RULE
        ),
        "elevenlabs_model": "eleven_v3",
        "sarvam_language_code": "mr-IN",
    },
    "gu": {
        "name": "Gujarati",
        "native_name": "ગુજરાતી",
        "llm_instruction": (
            "LANGUAGE: Generate ALL commentary in GUJARATI (Gujarati script). "
            "Cricket terms like 'boundary', 'wicket', 'six', 'four', 'over', 'run rate', "
            "'powerplay', 'maiden', 'dot ball' should stay in ENGLISH — "
            "natural Gujarati-English code-mixing. Everything else MUST be in Gujarati. "
            + _NATURAL_SPEECH_RULE
        ),
        "elevenlabs_model": "eleven_v3",
        "sarvam_language_code": "gu-IN",
    },
    "pa": {
        "name": "Punjabi",
        "native_name": "ਪੰਜਾਬੀ",
        "llm_instruction": (
            "LANGUAGE: Generate ALL commentary in PUNJABI (Gurmukhi script). "
            "Cricket terms like 'boundary', 'wicket', 'six', 'four', 'over', 'run rate', "
            "'powerplay', 'maiden', 'dot ball' should stay in ENGLISH — "
            "natural Punjabi-English code-mixing. Everything else MUST be in Punjabi. "
            + _NATURAL_SPEECH_RULE
        ),
        "elevenlabs_model": "eleven_v3",
        "sarvam_language_code": "pa-IN",
    },
}


class NarrativeBranch(str, Enum):
    """Categories for every ball event, driving commentary tone and content."""

    ROUTINE = "routine"
    BOUNDARY_MOMENTUM = "boundary_momentum"
    WICKET_DRAMA = "wicket_drama"
    PRESSURE_BUILDER = "pressure_builder"
    OVER_TRANSITION = "over_transition"
    EXTRA_GIFT = "extra_gift"


class NarrativeMoment(str, Enum):
    """Non-ball narrative moments that happen between deliveries."""

    # Innings-level moments (distinct events)
    FIRST_INNINGS_START = "first_innings_start"
    FIRST_INNINGS_END = "first_innings_end"
    SECOND_INNINGS_START = "second_innings_start"
    MATCH_RESULT = "match_result"

    # In-play moments
    END_OF_OVER = "end_of_over"
    NEW_BATSMAN = "new_batsman"
    PHASE_CHANGE = "phase_change"
    MILESTONE = "milestone"


class BallEvent(BaseModel):
    """A single ball delivery from the JSON feed."""

    over: int = Field(..., description="Over number (0-indexed)")
    ball: int = Field(..., description="Ball number within the over (1-6)")
    batsman: str
    bowler: str
    runs: int = Field(0, description="Runs scored off the bat")
    extras: int = Field(0, description="Extra runs (wides, no-balls, etc.)")
    extras_type: Optional[str] = Field(None, description="Type of extra: wide, noball, bye, legbye")
    is_wicket: bool = False
    wicket_type: Optional[str] = Field(None, description="e.g. bowled, caught, lbw, run_out")
    dismissal_batsman: Optional[str] = Field(None, description="Batsman dismissed (if different from striker)")
    is_boundary: bool = False
    is_six: bool = False
    non_striker: Optional[str] = None
    # Rich fields from parsed HTML commentary feeds
    commentary: Optional[str] = Field(None, description="Original commentary text from the feed")
    result_text: Optional[str] = Field(None, description="Raw result string, e.g. 'FOUR', 'no run', 'out Caught'")


class FallOfWicket(BaseModel):
    """Record of a wicket falling."""

    wicket_number: int
    batsman: str
    batsman_runs: int
    team_score: int
    overs: str
    bowler: str
    how: Optional[str] = None


class BowlerStats(BaseModel):
    """Per-bowler figures tracker."""

    name: str
    balls_bowled: int = 0
    runs_conceded: int = 0
    wickets: int = 0
    maidens: int = 0
    dots: int = 0
    fours_conceded: int = 0
    sixes_conceded: int = 0
    wides: int = 0
    noballs: int = 0

    @property
    def overs_display(self) -> str:
        return f"{self.balls_bowled // 6}.{self.balls_bowled % 6}"

    @property
    def economy(self) -> float:
        overs = self.balls_bowled / 6
        if overs == 0:
            return 0.0
        return round(self.runs_conceded / overs, 2)

    @property
    def dot_percentage(self) -> float:
        if self.balls_bowled == 0:
            return 0.0
        return round((self.dots / self.balls_bowled) * 100, 1)

    @property
    def figures_str(self) -> str:
        return f"{self.wickets}/{self.runs_conceded} ({self.overs_display})"


class BatsmanStats(BaseModel):
    """Per-batsman run tracker for milestone detection."""

    name: str
    runs: int = 0
    balls_faced: int = 0
    fours: int = 0
    sixes: int = 0
    dots: int = 0
    is_out: bool = False
    position: int = 0  # batting order position (1-indexed)

    @property
    def strike_rate(self) -> float:
        if self.balls_faced == 0:
            return 0.0
        return round((self.runs / self.balls_faced) * 100, 2)

    @property
    def dot_percentage(self) -> float:
        if self.balls_faced == 0:
            return 0.0
        return round((self.dots / self.balls_faced) * 100, 1)

    @property
    def boundary_runs(self) -> int:
        return (self.fours * 4) + (self.sixes * 6)

    @property
    def boundary_percentage(self) -> float:
        """What % of runs came from boundaries."""
        if self.runs == 0:
            return 0.0
        return round((self.boundary_runs / self.runs) * 100, 1)

    @property
    def approaching_fifty(self) -> bool:
        return 40 <= self.runs < 50

    @property
    def approaching_hundred(self) -> bool:
        return 90 <= self.runs < 100

    @property
    def just_reached_fifty(self) -> bool:
        return self.runs >= 50 and (self.runs - max(self.fours * 4, self.sixes * 6, 1)) < 50

    @property
    def just_reached_hundred(self) -> bool:
        return self.runs >= 100 and (self.runs - max(self.fours * 4, self.sixes * 6, 1)) < 100


class MatchState(BaseModel):
    """The full current state of the match."""

    batting_team: str
    bowling_team: str
    target: int
    total_runs: int = 0
    wickets: int = 0
    overs_completed: int = 0
    balls_in_current_over: int = 0
    total_balls_bowled: int = 0
    last_6_balls: list[int] = Field(default_factory=list)
    consecutive_dots: int = 0
    current_over_runs: int = 0
    current_over_wickets: int = 0
    batsmen: dict[str, BatsmanStats] = Field(default_factory=dict)
    bowlers: dict[str, BowlerStats] = Field(default_factory=dict)
    current_batsman: Optional[str] = None
    current_bowler: Optional[str] = None
    non_striker: Optional[str] = None
    previous_batsman: Optional[str] = None
    previous_bowler: Optional[str] = None
    last_commentary: str = ""
    commentary_history: list[str] = Field(default_factory=list)  # last N lines

    # Partnership tracking (since last wicket)
    partnership_runs: int = 0
    partnership_balls: int = 0
    partnership_number: int = 1  # 1st wicket stand, 2nd wicket stand, etc.

    # Fall of wickets log
    fall_of_wickets: list[FallOfWicket] = Field(default_factory=list)

    # Over-by-over history (runs per completed over)
    over_runs_history: list[int] = Field(default_factory=list)

    # Boundary & extras tracking
    total_fours: int = 0
    total_sixes: int = 0
    total_extras: int = 0
    total_wides: int = 0
    total_noballs: int = 0
    balls_since_last_boundary: int = 0
    balls_since_last_wicket: int = 0

    # Batting order
    batting_order: list[str] = Field(default_factory=list)

    # Transition flags (reset each ball)
    is_new_bowler: bool = False
    is_new_over: bool = False
    is_strike_change: bool = False
    is_new_batsman: bool = False
    new_batsman_name: Optional[str] = None
    previous_over_summary: Optional[str] = None

    # ------------------------------------------------------------------ #
    #  Core computed properties
    # ------------------------------------------------------------------ #

    @property
    def runs_needed(self) -> int:
        return max(self.target - self.total_runs, 0)

    @property
    def balls_remaining(self) -> int:
        return max(120 - self.total_balls_bowled, 0)  # T20: 120 balls

    @property
    def overs_display(self) -> str:
        return f"{self.overs_completed}.{self.balls_in_current_over}"

    @property
    def crr(self) -> float:
        """Current Run Rate."""
        overs = self.total_balls_bowled / 6
        if overs == 0:
            return 0.0
        return round(self.total_runs / overs, 2)

    @property
    def rrr(self) -> float:
        """Required Run Rate."""
        overs_remaining = self.balls_remaining / 6
        if overs_remaining <= 0:
            return 0.0
        return round(self.runs_needed / overs_remaining, 2)

    @property
    def match_phase(self) -> str:
        """Powerplay / Middle / Death overs."""
        total_overs = self.overs_completed + (1 if self.balls_in_current_over > 0 else 0)
        if total_overs <= 6:
            return "powerplay"
        elif total_overs <= 15:
            return "middle"
        else:
            return "death"

    # ------------------------------------------------------------------ #
    #  Phase-wise run tracking (computed from over_runs_history)
    # ------------------------------------------------------------------ #

    @property
    def powerplay_runs(self) -> int:
        """Runs scored in overs 1-6."""
        return sum(self.over_runs_history[:6])

    @property
    def middle_overs_runs(self) -> int:
        """Runs scored in overs 7-15."""
        return sum(self.over_runs_history[6:15])

    @property
    def death_overs_runs(self) -> int:
        """Runs scored in overs 16-20."""
        return sum(self.over_runs_history[15:20])

    @property
    def run_rate_last_3_overs(self) -> float:
        """Run rate in the last 3 completed overs."""
        if len(self.over_runs_history) < 3:
            return 0.0
        last_3 = self.over_runs_history[-3:]
        return round(sum(last_3) / 3, 2)

    @property
    def run_rate_last_5_overs(self) -> float:
        """Run rate in the last 5 completed overs."""
        if len(self.over_runs_history) < 5:
            return 0.0
        last_5 = self.over_runs_history[-5:]
        return round(sum(last_5) / 5, 2)

    @property
    def scoring_momentum(self) -> str:
        """Is the batting team accelerating, decelerating, or steady?"""
        if len(self.over_runs_history) < 4:
            return "early"
        recent_2 = sum(self.over_runs_history[-2:]) / 2
        previous_2 = sum(self.over_runs_history[-4:-2]) / 2
        diff = recent_2 - previous_2
        if diff >= 3:
            return "accelerating"
        elif diff <= -3:
            return "decelerating"
        return "steady"

    @property
    def dot_ball_percentage(self) -> float:
        """Overall dot ball % in the innings."""
        total_dots = sum(
            b.dots for b in self.batsmen.values() if not b.is_out or b.dots > 0
        )
        if self.total_balls_bowled == 0:
            return 0.0
        return round((total_dots / self.total_balls_bowled) * 100, 1)

    @property
    def boundary_runs_percentage(self) -> float:
        """What % of total runs came from boundaries."""
        boundary_runs = (self.total_fours * 4) + (self.total_sixes * 6)
        if self.total_runs == 0:
            return 0.0
        return round((boundary_runs / self.total_runs) * 100, 1)

    @property
    def is_boundary_drought(self) -> bool:
        """No boundary for 18+ balls (3 overs)."""
        return self.balls_since_last_boundary >= 18

    @property
    def is_collapse(self) -> bool:
        """3+ wickets in the last 18 balls (3 overs)."""
        if len(self.fall_of_wickets) < 3:
            return False
        # Check if last 3 wickets fell within 18 balls of each other
        recent_fows = self.fall_of_wickets[-3:]
        # Use balls_since_last_wicket and FOW timing to approximate
        # Simple heuristic: 3+ wickets and total_balls - fow[-3] ball count < 18
        return True if self.wickets >= 3 and self._recent_wicket_cluster() else False

    def _recent_wicket_cluster(self) -> bool:
        """Check if last 3 wickets fell within 18 balls of current position."""
        if len(self.fall_of_wickets) < 3:
            return False
        # Parse overs string from 3rd-last wicket
        fow = self.fall_of_wickets[-3]
        try:
            parts = fow.overs.split(".")
            fow_balls = int(parts[0]) * 6 + int(parts[1])
            balls_span = self.total_balls_bowled - fow_balls
            return balls_span <= 18
        except (ValueError, IndexError):
            return False


class LogicResult(BaseModel):
    """Output of the Logic Engine for a single ball."""

    branch: NarrativeBranch
    is_pivot: bool = False
    equation_shift: Optional[str] = None
    context_notes: str = ""


class CommentaryResult(BaseModel):
    """Final output for a single ball: text + audio."""

    ball_event: BallEvent
    match_state_snapshot: dict  # Serialized MatchState
    logic_result: LogicResult
    commentary_text: str
    audio_base64: Optional[str] = None
