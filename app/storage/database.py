"""
SQLite persistence layer.

Tables:
  - matches: match metadata (teams, venue, target, languages)
  - deliveries: ball-by-ball input data (one row per delivery per innings)
  - match_commentaries: generated output (one row per language per moment, FK to deliveries)
  - innings_batters: per-innings batter stats
  - innings_bowlers: per-innings bowler stats
  - fall_of_wickets: fall of wickets log
  - innings: per-innings summary
  - partnerships: partnership records
  - match_players: squad / playing XI / substitutions per match

Uses aiosqlite for async access. Database file: data/matches.db
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

DB_DIR = Path("data")
DB_PATH = DB_DIR / "matches.db"

_db: aiosqlite.Connection | None = None


# ------------------------------------------------------------------ #
#  Connection management
# ------------------------------------------------------------------ #

async def init_db() -> None:
    """Create tables if they don't exist. Called once at app startup."""
    global _db
    DB_DIR.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(str(DB_PATH))
    _db.row_factory = aiosqlite.Row

    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS matches (
            match_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'ready',
            match_info  TEXT NOT NULL DEFAULT '{}',
            languages   TEXT NOT NULL DEFAULT '["hi"]',
            venue       TEXT,
            format      TEXT,
            team1       TEXT,
            team2       TEXT,
            match_date  TEXT,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS deliveries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id    INTEGER NOT NULL,
            innings     INTEGER NOT NULL,
            ball_index  INTEGER NOT NULL,
            over        INTEGER NOT NULL,
            ball        INTEGER NOT NULL,
            batter      TEXT NOT NULL,
            bowler      TEXT NOT NULL,
            non_batter  TEXT,
            batter_id       INTEGER,
            non_batter_id   INTEGER,
            bowler_id       INTEGER,
            runs        INTEGER NOT NULL DEFAULT 0,
            extras      INTEGER NOT NULL DEFAULT 0,
            extras_type TEXT,
            is_wicket   INTEGER NOT NULL DEFAULT 0,
            is_boundary INTEGER NOT NULL DEFAULT 0,
            is_six      INTEGER NOT NULL DEFAULT 0,
            total_runs      INTEGER NOT NULL DEFAULT 0,
            total_wickets   INTEGER NOT NULL DEFAULT 0,
            overs_completed INTEGER NOT NULL DEFAULT 0,
            balls_in_over   INTEGER NOT NULL DEFAULT 0,
            crr             REAL,
            rrr             REAL,
            runs_needed     INTEGER,
            balls_remaining INTEGER,
            match_phase     TEXT,
            data        TEXT NOT NULL DEFAULT '{}',
            context     TEXT,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );

        CREATE INDEX IF NOT EXISTS idx_deliveries
            ON deliveries(match_id, innings, ball_index);

        CREATE TABLE IF NOT EXISTS match_commentaries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id    INTEGER NOT NULL,
            ball_id     INTEGER,
            seq         INTEGER NOT NULL,
            event_type  TEXT NOT NULL,
            language    TEXT,
            text        TEXT,
            audio_url   TEXT,
            data        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            FOREIGN KEY (match_id) REFERENCES matches(match_id),
            FOREIGN KEY (ball_id) REFERENCES deliveries(id)
        );

        CREATE INDEX IF NOT EXISTS idx_match_commentaries
            ON match_commentaries(match_id, seq);

        CREATE INDEX IF NOT EXISTS idx_match_commentaries_lang
            ON match_commentaries(match_id, language, seq);

        CREATE TABLE IF NOT EXISTS innings_batters (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id    INTEGER NOT NULL,
            innings     INTEGER NOT NULL,
            name        TEXT NOT NULL,
            position    INTEGER NOT NULL DEFAULT 0,
            runs        INTEGER NOT NULL DEFAULT 0,
            balls_faced INTEGER NOT NULL DEFAULT 0,
            fours       INTEGER NOT NULL DEFAULT 0,
            sixes       INTEGER NOT NULL DEFAULT 0,
            dots        INTEGER NOT NULL DEFAULT 0,
            is_out      INTEGER NOT NULL DEFAULT 0,
            strike_rate REAL,
            out_status  TEXT,
            dismissal_info TEXT,
            UNIQUE(match_id, innings, name),
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );

        CREATE TABLE IF NOT EXISTS innings_bowlers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        INTEGER NOT NULL,
            innings         INTEGER NOT NULL,
            name            TEXT NOT NULL,
            balls_bowled    INTEGER NOT NULL DEFAULT 0,
            runs_conceded   INTEGER NOT NULL DEFAULT 0,
            wickets         INTEGER NOT NULL DEFAULT 0,
            maidens         INTEGER NOT NULL DEFAULT 0,
            dots            INTEGER NOT NULL DEFAULT 0,
            fours_conceded  INTEGER NOT NULL DEFAULT 0,
            sixes_conceded  INTEGER NOT NULL DEFAULT 0,
            wides           INTEGER NOT NULL DEFAULT 0,
            noballs         INTEGER NOT NULL DEFAULT 0,
            economy         REAL,
            overs_bowled    REAL,
            UNIQUE(match_id, innings, name),
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );

        CREATE TABLE IF NOT EXISTS fall_of_wickets (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        INTEGER NOT NULL,
            innings         INTEGER NOT NULL,
            wicket_number   INTEGER NOT NULL,
            batter          TEXT NOT NULL,
            batter_runs     INTEGER NOT NULL DEFAULT 0,
            team_score      INTEGER NOT NULL DEFAULT 0,
            overs           TEXT NOT NULL,
            bowler          TEXT NOT NULL,
            how             TEXT,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );

        CREATE TABLE IF NOT EXISTS innings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        INTEGER NOT NULL,
            innings_number  INTEGER NOT NULL,
            batting_team    TEXT NOT NULL,
            bowling_team    TEXT NOT NULL,
            total_runs      INTEGER NOT NULL DEFAULT 0,
            total_wickets   INTEGER NOT NULL DEFAULT 0,
            total_overs     REAL,
            extras_total    INTEGER NOT NULL DEFAULT 0,
            UNIQUE(match_id, innings_number),
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );

        CREATE TABLE IF NOT EXISTS partnerships (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        INTEGER NOT NULL,
            innings         INTEGER NOT NULL,
            wicket_number   INTEGER NOT NULL,
            batter1         TEXT NOT NULL,
            batter2         TEXT NOT NULL,
            runs            INTEGER NOT NULL DEFAULT 0,
            balls           INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );

        CREATE TABLE IF NOT EXISTS match_players (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id        INTEGER NOT NULL,
            player_id       INTEGER,
            player_name     TEXT NOT NULL,
            team            TEXT NOT NULL,
            is_captain      INTEGER NOT NULL DEFAULT 0,
            is_keeper       INTEGER NOT NULL DEFAULT 0,
            player_status   TEXT NOT NULL DEFAULT 'Playing XI',
            UNIQUE(match_id, player_name, team),
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );
    """)

    # ---- Migrations for existing DBs ---- #

    # Migrate: add context column on deliveries if missing
    try:
        await _db.execute("SELECT context FROM deliveries LIMIT 1")
    except Exception:
        try:
            await _db.execute("ALTER TABLE deliveries ADD COLUMN context TEXT")
            logger.info("Migrated deliveries: added 'context' column")
        except Exception:
            pass

    # Migrate: add promoted delivery columns if missing
    delivery_cols = [
        ("runs", "INTEGER NOT NULL DEFAULT 0"),
        ("extras", "INTEGER NOT NULL DEFAULT 0"),
        ("extras_type", "TEXT"),
        ("is_wicket", "INTEGER NOT NULL DEFAULT 0"),
        ("is_boundary", "INTEGER NOT NULL DEFAULT 0"),
        ("is_six", "INTEGER NOT NULL DEFAULT 0"),
        # Snapshot columns (per-delivery match state)
        ("total_runs", "INTEGER NOT NULL DEFAULT 0"),
        ("total_wickets", "INTEGER NOT NULL DEFAULT 0"),
        ("overs_completed", "INTEGER NOT NULL DEFAULT 0"),
        ("balls_in_over", "INTEGER NOT NULL DEFAULT 0"),
        ("crr", "REAL"),
        ("rrr", "REAL"),
        ("runs_needed", "INTEGER"),
        ("balls_remaining", "INTEGER"),
        ("match_phase", "TEXT"),
        ("non_batter", "TEXT"),
        # Player ID columns (FK-ready for future players table)
        ("batter_id", "INTEGER"),
        ("non_batter_id", "INTEGER"),
        ("bowler_id", "INTEGER"),
    ]
    for col_name, col_def in delivery_cols:
        try:
            await _db.execute(f"SELECT {col_name} FROM deliveries LIMIT 1")
        except Exception:
            try:
                await _db.execute(f"ALTER TABLE deliveries ADD COLUMN {col_name} {col_def}")
                logger.info(f"Migrated deliveries: added '{col_name}' column")
            except Exception:
                pass


    # Migrate: add new columns on innings_batters if missing
    batter_new_cols = [
        ("strike_rate", "REAL"),
        ("out_status", "TEXT"),
        ("dismissal_info", "TEXT"),
    ]
    for col_name, col_def in batter_new_cols:
        try:
            await _db.execute(f"SELECT {col_name} FROM innings_batters LIMIT 1")
        except Exception:
            try:
                await _db.execute(f"ALTER TABLE innings_batters ADD COLUMN {col_name} {col_def}")
                logger.info(f"Migrated innings_batters: added '{col_name}' column")
            except Exception:
                pass

    # Migrate: add new columns on innings_bowlers if missing
    bowler_new_cols = [
        ("economy", "REAL"),
        ("overs_bowled", "REAL"),
    ]
    for col_name, col_def in bowler_new_cols:
        try:
            await _db.execute(f"SELECT {col_name} FROM innings_bowlers LIMIT 1")
        except Exception:
            try:
                await _db.execute(f"ALTER TABLE innings_bowlers ADD COLUMN {col_name} {col_def}")
                logger.info(f"Migrated innings_bowlers: added '{col_name}' column")
            except Exception:
                pass

    # Migrate: add new columns on matches if missing
    match_new_cols = [
        ("venue", "TEXT"),
        ("format", "TEXT"),
        ("team1", "TEXT"),
        ("team2", "TEXT"),
        ("match_date", "TEXT"),
    ]
    for col_name, col_def in match_new_cols:
        try:
            await _db.execute(f"SELECT {col_name} FROM matches LIMIT 1")
        except Exception:
            try:
                await _db.execute(f"ALTER TABLE matches ADD COLUMN {col_name} {col_def}")
                logger.info(f"Migrated matches: added '{col_name}' column")
            except Exception:
                pass

    await _db.commit()
    logger.info(f"SQLite database initialized at {DB_PATH}")


async def close_db() -> None:
    """Close the database connection."""
    global _db
    if _db:
        await _db.close()
        _db = None


def _get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _db


# ------------------------------------------------------------------ #
#  Matches CRUD
# ------------------------------------------------------------------ #

async def create_match(
    title: str,
    match_info: dict,
    languages: list[str] | None = None,
    status: str = "ready",
    *,
    venue: str | None = None,
    format: str | None = None,
    team1: str | None = None,
    team2: str | None = None,
    match_date: str | None = None,
) -> dict:
    """Insert a new match. Returns the created record with auto-generated ID."""
    db = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    langs = languages or ["hi"]
    cursor = await db.execute(
        """INSERT INTO matches
           (title, status, match_info, languages, venue, format, team1, team2, match_date, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, status, json.dumps(match_info, default=str), json.dumps(langs),
         venue, format, team1, team2, match_date, now),
    )
    await db.commit()
    return {
        "match_id": cursor.lastrowid,
        "title": title,
        "status": status,
        "match_info": match_info,
        "languages": langs,
        "venue": venue,
        "format": format,
        "team1": team1,
        "team2": team2,
        "match_date": match_date,
        "created_at": now,
    }


async def get_match(match_id: int) -> dict | None:
    db = _get_db()
    async with db.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)) as cur:
        row = await cur.fetchone()
        return _row_to_match(row) if row else None


async def get_match_by_title(title: str) -> dict | None:
    """For seed idempotency — check if a match with this title already exists."""
    db = _get_db()
    async with db.execute("SELECT * FROM matches WHERE title = ? LIMIT 1", (title,)) as cur:
        row = await cur.fetchone()
        return _row_to_match(row) if row else None


async def list_matches(status: str | None = None) -> list[dict]:
    db = _get_db()
    if status:
        query = "SELECT * FROM matches WHERE status = ? ORDER BY created_at DESC"
        params: tuple = (status,)
    else:
        query = "SELECT * FROM matches ORDER BY created_at DESC"
        params = ()
    async with db.execute(query, params) as cur:
        return [_row_to_match(r) for r in await cur.fetchall()]


async def update_match(match_id: int, **fields) -> dict | None:
    """
    Update one or more match fields.  Supported keys:
      status, languages, match_info, title, venue, format, team1, team2, match_date
    Returns the updated match dict, or None if not found.
    """
    db = _get_db()
    allowed = {"status", "languages", "match_info", "title", "venue", "format", "team1", "team2", "match_date"}
    sets: list[str] = []
    vals: list = []
    for key, val in fields.items():
        if key not in allowed:
            continue
        if key in ("languages", "match_info"):
            val = json.dumps(val, default=str)
        sets.append(f"{key} = ?")
        vals.append(val)
    if not sets:
        return await get_match(match_id)
    vals.append(match_id)
    await db.execute(
        f"UPDATE matches SET {', '.join(sets)} WHERE match_id = ?", vals
    )
    await db.commit()
    return await get_match(match_id)


async def update_match_status(match_id: int, status: str) -> None:
    db = _get_db()
    await db.execute("UPDATE matches SET status = ? WHERE match_id = ?", (status, match_id))
    await db.commit()


async def update_match_languages(match_id: int, languages: list[str]) -> None:
    db = _get_db()
    await db.execute(
        "UPDATE matches SET languages = ? WHERE match_id = ?",
        (json.dumps(languages), match_id),
    )
    await db.commit()


def _row_to_match(row: aiosqlite.Row) -> dict:
    return {
        "match_id": row["match_id"],
        "title": row["title"],
        "status": row["status"],
        "match_info": json.loads(row["match_info"]),
        "languages": json.loads(row["languages"]),
        "venue": row["venue"],
        "format": row["format"],
        "team1": row["team1"],
        "team2": row["team2"],
        "match_date": row["match_date"],
        "created_at": row["created_at"],
    }


# ------------------------------------------------------------------ #
#  Deliveries CRUD
# ------------------------------------------------------------------ #

async def insert_delivery(
    match_id: int,
    innings: int,
    ball_index: int,
    over: int,
    ball: int,
    batter: str,
    bowler: str,
    data: dict,
    *,
    non_batter: str | None = None,
    batter_id: int | None = None,
    non_batter_id: int | None = None,
    bowler_id: int | None = None,
    runs: int = 0,
    extras: int = 0,
    extras_type: str | None = None,
    is_wicket: bool = False,
    is_boundary: bool = False,
    is_six: bool = False,
) -> int:
    """Insert one delivery. Returns the delivery row ID."""
    db = _get_db()
    cursor = await db.execute(
        """INSERT INTO deliveries
           (match_id, innings, ball_index, over, ball, batter, bowler, non_batter,
            batter_id, non_batter_id, bowler_id,
            runs, extras, extras_type, is_wicket, is_boundary, is_six, data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (match_id, innings, ball_index, over, ball, batter, bowler, non_batter,
         batter_id, non_batter_id, bowler_id,
         runs, extras, extras_type, int(is_wicket), int(is_boundary), int(is_six),
         json.dumps(data, default=str)),
    )
    await db.commit()
    return cursor.lastrowid


async def insert_deliveries_bulk(match_id: int, innings: int, balls: list[dict]) -> int:
    """Bulk insert deliveries for an innings. Returns count inserted."""
    db = _get_db()
    rows = [
        (match_id, innings, i,
         b.get("over", 0), b.get("ball", 0),
         b.get("batter", ""), b.get("bowler", ""),
         b.get("non_batter"),
         b.get("batter_id"), b.get("non_batter_id"), b.get("bowler_id"),
         b.get("runs", 0), b.get("extras", 0), b.get("extras_type"),
         int(b.get("is_wicket", False)), int(b.get("is_boundary", False)),
         int(b.get("is_six", False)),
         json.dumps(b, default=str))
        for i, b in enumerate(balls)
    ]
    await db.executemany(
        """INSERT INTO deliveries
           (match_id, innings, ball_index, over, ball, batter, bowler, non_batter,
            batter_id, non_batter_id, bowler_id,
            runs, extras, extras_type, is_wicket, is_boundary, is_six, data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    await db.commit()
    return len(rows)


async def get_deliveries(match_id: int, innings: int) -> list[dict]:
    """Fetch all deliveries for a match innings, ordered by ball_index."""
    db = _get_db()
    async with db.execute(
        "SELECT * FROM deliveries WHERE match_id = ? AND innings = ? ORDER BY ball_index",
        (match_id, innings),
    ) as cur:
        return [_row_to_delivery(r) for r in await cur.fetchall()]


async def get_all_deliveries(match_id: int) -> list[dict]:
    """Fetch all deliveries for a match across all innings, ordered by innings then ball_index."""
    db = _get_db()
    async with db.execute(
        "SELECT * FROM deliveries WHERE match_id = ? ORDER BY innings, ball_index",
        (match_id,),
    ) as cur:
        return [_row_to_delivery(r) for r in await cur.fetchall()]


async def get_delivery_by_id(ball_id: int) -> dict | None:
    """Fetch a single delivery by its row ID."""
    db = _get_db()
    async with db.execute("SELECT * FROM deliveries WHERE id = ?", (ball_id,)) as cur:
        row = await cur.fetchone()
        return _row_to_delivery(row) if row else None


async def count_deliveries(match_id: int, innings: int | None = None) -> int:
    """Return delivery count for a match (optionally filtered by innings)."""
    db = _get_db()
    if innings is not None:
        query = "SELECT COUNT(*) as cnt FROM deliveries WHERE match_id = ? AND innings = ?"
        params: tuple = (match_id, innings)
    else:
        query = "SELECT COUNT(*) as cnt FROM deliveries WHERE match_id = ?"
        params = (match_id,)
    async with db.execute(query, params) as cur:
        row = await cur.fetchone()
        return row["cnt"] if row else 0


async def update_delivery_context(ball_id: int, context: dict) -> None:
    """Update the pre-computed context for a single delivery."""
    db = _get_db()
    await db.execute(
        "UPDATE deliveries SET context = ? WHERE id = ?",
        (json.dumps(context, default=str), ball_id),
    )
    await db.commit()


async def update_deliveries_context_bulk(updates: list[tuple[int, dict]]) -> int:
    """
    Bulk-update pre-computed context for multiple deliveries.
    Each item: (ball_id, context_dict).
    Returns count updated.
    """
    db = _get_db()
    rows = [(json.dumps(ctx, default=str), bid) for bid, ctx in updates]
    await db.executemany(
        "UPDATE deliveries SET context = ? WHERE id = ?",
        rows,
    )
    await db.commit()
    return len(rows)


def _row_to_delivery(row: aiosqlite.Row) -> dict:
    ctx_raw = row["context"]
    return {
        "id": row["id"],
        "match_id": row["match_id"],
        "innings": row["innings"],
        "ball_index": row["ball_index"],
        "over": row["over"],
        "ball": row["ball"],
        "batter": row["batter"],
        "bowler": row["bowler"],
        "non_batter": row["non_batter"],
        # Player ID references
        "batter_id": row["batter_id"],
        "non_batter_id": row["non_batter_id"],
        "bowler_id": row["bowler_id"],
        "runs": row["runs"],
        "extras": row["extras"],
        "extras_type": row["extras_type"],
        "is_wicket": bool(row["is_wicket"]),
        "is_boundary": bool(row["is_boundary"]),
        "is_six": bool(row["is_six"]),
        # Per-delivery match snapshot
        "total_runs": row["total_runs"],
        "total_wickets": row["total_wickets"],
        "overs_completed": row["overs_completed"],
        "balls_in_over": row["balls_in_over"],
        "crr": row["crr"],
        "rrr": row["rrr"],
        "runs_needed": row["runs_needed"],
        "balls_remaining": row["balls_remaining"],
        "match_phase": row["match_phase"],
        "data": json.loads(row["data"]),
        "context": json.loads(ctx_raw) if ctx_raw else None,
    }


def row_to_delivery_event(row: dict):
    """
    Build a BallEvent from a delivery row dict (as returned by get_deliveries / get_delivery_by_id).

    Reads core fields from dedicated columns. Optional fields (wicket_type,
    dismissal_batter, commentary, result_text) come from the data JSON since
    they are not always present. non_batter is read from the column.
    """
    from app.models import BallEvent  # noqa: E402 — lazy to avoid circular import

    data = row.get("data") or {}
    return BallEvent(
        over=row["over"],
        ball=row["ball"],
        batter=row["batter"],
        bowler=row["bowler"],
        runs=row["runs"],
        extras=row["extras"],
        extras_type=row["extras_type"],
        is_wicket=bool(row["is_wicket"]),
        is_boundary=bool(row["is_boundary"]),
        is_six=bool(row["is_six"]),
        # non_batter from column, optional fields from data JSON
        non_batter=row.get("non_batter") or data.get("non_batter"),
        wicket_type=data.get("wicket_type"),
        dismissal_batter=data.get("dismissal_batter"),
        commentary=data.get("commentary"),
        result_text=data.get("result_text"),
    )


# ------------------------------------------------------------------ #
#  Delivery snapshot columns
# ------------------------------------------------------------------ #

async def update_delivery_snapshot(
    ball_id: int,
    total_runs: int,
    total_wickets: int,
    overs_completed: int,
    balls_in_over: int,
    crr: float | None,
    rrr: float | None,
    runs_needed: int | None,
    balls_remaining: int | None,
    match_phase: str | None,
    *,
    non_batter: str | None = None,
    batter_id: int | None = None,
    non_batter_id: int | None = None,
    bowler_id: int | None = None,
) -> None:
    """Update per-delivery match snapshot columns + optional player fields."""
    db = _get_db()
    await db.execute(
        """UPDATE deliveries
           SET total_runs=?, total_wickets=?, overs_completed=?, balls_in_over=?,
               crr=?, rrr=?, runs_needed=?, balls_remaining=?, match_phase=?,
               non_batter=COALESCE(?, non_batter),
               batter_id=COALESCE(?, batter_id),
               non_batter_id=COALESCE(?, non_batter_id),
               bowler_id=COALESCE(?, bowler_id)
           WHERE id=?""",
        (total_runs, total_wickets, overs_completed, balls_in_over,
         crr, rrr, runs_needed, balls_remaining, match_phase,
         non_batter, batter_id, non_batter_id, bowler_id, ball_id),
    )


async def update_delivery_snapshot_bulk(
    updates: list[tuple[int, dict]],
) -> int:
    """Bulk-update snapshot columns + player fields. Each item: (ball_id, snapshot_dict)."""
    db = _get_db()
    rows = [
        (s["total_runs"], s["total_wickets"], s["overs_completed"],
         s["balls_in_over"], s.get("crr"), s.get("rrr"),
         s.get("runs_needed"), s.get("balls_remaining"),
         s.get("match_phase"),
         s.get("non_batter"), s.get("batter_id"),
         s.get("non_batter_id"), s.get("bowler_id"),
         bid)
        for bid, s in updates
    ]
    await db.executemany(
        """UPDATE deliveries
           SET total_runs=?, total_wickets=?, overs_completed=?, balls_in_over=?,
               crr=?, rrr=?, runs_needed=?, balls_remaining=?, match_phase=?,
               non_batter=COALESCE(?, non_batter),
               batter_id=COALESCE(?, batter_id),
               non_batter_id=COALESCE(?, non_batter_id),
               bowler_id=COALESCE(?, bowler_id)
           WHERE id=?""",
        rows,
    )
    await db.commit()
    return len(rows)


# ------------------------------------------------------------------ #
#  Innings Batters CRUD
# ------------------------------------------------------------------ #

async def upsert_innings_batter(
    match_id: int, innings: int, name: str, *,
    position: int = 0, runs: int = 0, balls_faced: int = 0,
    fours: int = 0, sixes: int = 0, dots: int = 0, is_out: bool = False,
    strike_rate: float | None = None, out_status: str | None = None,
    dismissal_info: str | None = None,
) -> None:
    """Insert or replace batter stats for an innings."""
    db = _get_db()
    await db.execute(
        """INSERT INTO innings_batters
           (match_id, innings, name, position, runs, balls_faced, fours, sixes, dots, is_out,
            strike_rate, out_status, dismissal_info)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(match_id, innings, name)
           DO UPDATE SET position=excluded.position, runs=excluded.runs,
                         balls_faced=excluded.balls_faced, fours=excluded.fours,
                         sixes=excluded.sixes, dots=excluded.dots,
                         is_out=excluded.is_out,
                         strike_rate=excluded.strike_rate,
                         out_status=excluded.out_status,
                         dismissal_info=excluded.dismissal_info""",
        (match_id, innings, name, position, runs, balls_faced,
         fours, sixes, dots, int(is_out), strike_rate, out_status, dismissal_info),
    )


async def upsert_innings_batters_bulk(
    match_id: int, innings: int, batsmen: list[dict],
) -> int:
    """Bulk upsert all batter stats for an innings."""
    db = _get_db()
    rows = [
        (match_id, innings, b["name"], b.get("position", 0),
         b.get("runs", 0), b.get("balls_faced", 0),
         b.get("fours", 0), b.get("sixes", 0), b.get("dots", 0),
         int(b.get("is_out", False)),
         b.get("strike_rate"), b.get("out_status"), b.get("dismissal_info"))
        for b in batsmen
    ]
    await db.executemany(
        """INSERT INTO innings_batters
           (match_id, innings, name, position, runs, balls_faced, fours, sixes, dots, is_out,
            strike_rate, out_status, dismissal_info)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(match_id, innings, name)
           DO UPDATE SET position=excluded.position, runs=excluded.runs,
                         balls_faced=excluded.balls_faced, fours=excluded.fours,
                         sixes=excluded.sixes, dots=excluded.dots,
                         is_out=excluded.is_out,
                         strike_rate=excluded.strike_rate,
                         out_status=excluded.out_status,
                         dismissal_info=excluded.dismissal_info""",
        rows,
    )
    await db.commit()
    return len(rows)


async def get_innings_batters(match_id: int, innings: int) -> list[dict]:
    """Get all batter stats for an innings, ordered by position."""
    db = _get_db()
    async with db.execute(
        """SELECT * FROM innings_batters
           WHERE match_id = ? AND innings = ?
           ORDER BY position""",
        (match_id, innings),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ------------------------------------------------------------------ #
#  Innings Bowlers CRUD
# ------------------------------------------------------------------ #

async def upsert_innings_bowlers_bulk(
    match_id: int, innings: int, bowlers: list[dict],
) -> int:
    """Bulk upsert all bowler stats for an innings."""
    db = _get_db()
    rows = [
        (match_id, innings, b["name"],
         b.get("balls_bowled", 0), b.get("runs_conceded", 0),
         b.get("wickets", 0), b.get("maidens", 0), b.get("dots", 0),
         b.get("fours_conceded", 0), b.get("sixes_conceded", 0),
         b.get("wides", 0), b.get("noballs", 0),
         b.get("economy"), b.get("overs_bowled"))
        for b in bowlers
    ]
    await db.executemany(
        """INSERT INTO innings_bowlers
           (match_id, innings, name, balls_bowled, runs_conceded, wickets,
            maidens, dots, fours_conceded, sixes_conceded, wides, noballs,
            economy, overs_bowled)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(match_id, innings, name)
           DO UPDATE SET balls_bowled=excluded.balls_bowled,
                         runs_conceded=excluded.runs_conceded,
                         wickets=excluded.wickets, maidens=excluded.maidens,
                         dots=excluded.dots, fours_conceded=excluded.fours_conceded,
                         sixes_conceded=excluded.sixes_conceded,
                         wides=excluded.wides, noballs=excluded.noballs,
                         economy=excluded.economy,
                         overs_bowled=excluded.overs_bowled""",
        rows,
    )
    await db.commit()
    return len(rows)


async def get_innings_bowlers(match_id: int, innings: int) -> list[dict]:
    """Get all bowler stats for an innings."""
    db = _get_db()
    async with db.execute(
        """SELECT * FROM innings_bowlers
           WHERE match_id = ? AND innings = ?""",
        (match_id, innings),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ------------------------------------------------------------------ #
#  Fall of Wickets CRUD
# ------------------------------------------------------------------ #

async def insert_fall_of_wickets_bulk(
    match_id: int, innings: int, wickets: list[dict],
) -> int:
    """Bulk insert fall of wickets for an innings."""
    db = _get_db()
    rows = [
        (match_id, innings, w["wicket_number"],
         w.get("batter", ""),
         w.get("batter_runs", 0),
         w.get("team_score", 0),
         w.get("overs", ""), w.get("bowler", ""), w.get("how"))
        for w in wickets
    ]
    await db.executemany(
        """INSERT INTO fall_of_wickets
           (match_id, innings, wicket_number, batter, batter_runs,
            team_score, overs, bowler, how)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    await db.commit()
    return len(rows)


async def get_fall_of_wickets(match_id: int, innings: int) -> list[dict]:
    """Get fall of wickets for an innings, ordered by wicket number."""
    db = _get_db()
    async with db.execute(
        """SELECT * FROM fall_of_wickets
           WHERE match_id = ? AND innings = ?
           ORDER BY wicket_number""",
        (match_id, innings),
    ) as cur:
        rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "match_id": r["match_id"],
                "innings": r["innings"],
                "wicket_number": r["wicket_number"],
                "batter": r["batter"],
                "batter_runs": r["batter_runs"],
                "team_score": r["team_score"],
                "overs": r["overs"],
                "bowler": r["bowler"],
                "how": r["how"],
            }
            for r in rows
        ]


# ------------------------------------------------------------------ #
#  Innings CRUD
# ------------------------------------------------------------------ #

async def upsert_innings(
    match_id: int,
    innings_number: int,
    batting_team: str,
    bowling_team: str,
    total_runs: int = 0,
    total_wickets: int = 0,
    total_overs: float | None = None,
    extras_total: int = 0,
) -> None:
    """Insert or replace innings summary."""
    db = _get_db()
    await db.execute(
        """INSERT INTO innings
           (match_id, innings_number, batting_team, bowling_team,
            total_runs, total_wickets, total_overs, extras_total)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(match_id, innings_number)
           DO UPDATE SET batting_team=excluded.batting_team,
                         bowling_team=excluded.bowling_team,
                         total_runs=excluded.total_runs,
                         total_wickets=excluded.total_wickets,
                         total_overs=excluded.total_overs,
                         extras_total=excluded.extras_total""",
        (match_id, innings_number, batting_team, bowling_team,
         total_runs, total_wickets, total_overs, extras_total),
    )
    await db.commit()


async def get_innings(match_id: int, innings_number: int | None = None) -> dict | list[dict]:
    """
    If innings_number given, return single dict (or None).
    Otherwise return list of all innings for the match.
    """
    db = _get_db()
    if innings_number is not None:
        async with db.execute(
            "SELECT * FROM innings WHERE match_id = ? AND innings_number = ?",
            (match_id, innings_number),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
    else:
        async with db.execute(
            "SELECT * FROM innings WHERE match_id = ? ORDER BY innings_number",
            (match_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ------------------------------------------------------------------ #
#  Partnerships CRUD
# ------------------------------------------------------------------ #

async def upsert_partnerships_bulk(
    match_id: int, innings: int, partnerships: list[dict],
) -> int:
    """Delete existing partnerships for this match+innings, then bulk insert."""
    db = _get_db()
    await db.execute(
        "DELETE FROM partnerships WHERE match_id = ? AND innings = ?",
        (match_id, innings),
    )
    rows = [
        (match_id, innings, p["wicket_number"],
         p["batter1"], p["batter2"],
         p.get("runs", 0), p.get("balls", 0))
        for p in partnerships
    ]
    await db.executemany(
        """INSERT INTO partnerships
           (match_id, innings, wicket_number, batter1, batter2, runs, balls)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    await db.commit()
    return len(rows)


async def get_partnerships(match_id: int, innings: int) -> list[dict]:
    """Return list of partnership dicts ordered by wicket_number."""
    db = _get_db()
    async with db.execute(
        """SELECT * FROM partnerships
           WHERE match_id = ? AND innings = ?
           ORDER BY wicket_number""",
        (match_id, innings),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ------------------------------------------------------------------ #
#  Match Players (Squad / Playing XI)
# ------------------------------------------------------------------ #

async def upsert_match_players_bulk(
    match_id: int, players: list[dict],
) -> int:
    """
    Bulk upsert match players (squad / playing XI).

    Each player dict should have: player_name, team, and optionally:
    player_id, is_captain, is_keeper, player_status.
    """
    db = _get_db()
    rows = [
        (match_id, p.get("player_id"), p["player_name"], p["team"],
         int(p.get("is_captain", False)), int(p.get("is_keeper", False)),
         p.get("player_status", "Playing XI"))
        for p in players
    ]
    await db.executemany(
        """INSERT INTO match_players
           (match_id, player_id, player_name, team, is_captain, is_keeper, player_status)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(match_id, player_name, team)
           DO UPDATE SET player_id=excluded.player_id,
                         is_captain=excluded.is_captain,
                         is_keeper=excluded.is_keeper,
                         player_status=excluded.player_status""",
        rows,
    )
    await db.commit()
    return len(rows)


async def get_match_players(
    match_id: int, team: str | None = None,
) -> list[dict]:
    """Get match players, optionally filtered by team."""
    db = _get_db()
    if team:
        query = """SELECT * FROM match_players
                   WHERE match_id = ? AND team = ?
                   ORDER BY player_status, player_name"""
        params: tuple = (match_id, team)
    else:
        query = """SELECT * FROM match_players
                   WHERE match_id = ?
                   ORDER BY team, player_status, player_name"""
        params = (match_id,)
    async with db.execute(query, params) as cur:
        return [
            {
                "id": r["id"],
                "match_id": r["match_id"],
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "team": r["team"],
                "is_captain": bool(r["is_captain"]),
                "is_keeper": bool(r["is_keeper"]),
                "player_status": r["player_status"],
            }
            for r in await cur.fetchall()
        ]


async def delete_match_players(match_id: int) -> int:
    """Delete all players for a match. Returns count deleted."""
    db = _get_db()
    cursor = await db.execute(
        "DELETE FROM match_players WHERE match_id = ?", (match_id,)
    )
    await db.commit()
    return cursor.rowcount


# ------------------------------------------------------------------ #
#  Cleanup for re-precompute
# ------------------------------------------------------------------ #

async def delete_innings_stats(match_id: int, innings: int) -> None:
    """Delete batters, bowlers, FOW, and partnerships for an innings (used before re-precompute)."""
    db = _get_db()
    await db.execute(
        "DELETE FROM innings_batters WHERE match_id = ? AND innings = ?",
        (match_id, innings),
    )
    await db.execute(
        "DELETE FROM innings_bowlers WHERE match_id = ? AND innings = ?",
        (match_id, innings),
    )
    await db.execute(
        "DELETE FROM fall_of_wickets WHERE match_id = ? AND innings = ?",
        (match_id, innings),
    )
    await db.execute(
        "DELETE FROM partnerships WHERE match_id = ? AND innings = ?",
        (match_id, innings),
    )
    await db.commit()


# ------------------------------------------------------------------ #
#  Match Commentaries CRUD
# ------------------------------------------------------------------ #

async def insert_commentary(
    match_id: int,
    ball_id: int | None,
    seq: int,
    event_type: str,
    language: str | None,
    text: str | None,
    audio_url: str | None,
    data: dict,
) -> int:
    """Insert one commentary row. Returns the row ID."""
    db = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        """INSERT INTO match_commentaries
           (match_id, ball_id, seq, event_type, language, text, audio_url, data, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (match_id, ball_id, seq, event_type, language, text, audio_url,
         json.dumps(data, default=str), now),
    )
    await db.commit()
    return cursor.lastrowid


async def get_commentaries_after(
    match_id: int,
    after_seq: int,
    language: str | None = None,
) -> list[dict]:
    """
    Fetch commentaries with seq > after_seq, joined with delivery data.
    Filters to the requested language + language-independent events (language IS NULL).
    """
    db = _get_db()
    if language:
        query = """
            SELECT c.*, b.over as b_over, b.ball as b_ball,
                   b.batter as b_batter, b.bowler as b_bowler, b.non_batter as b_non_batter,
                   b.runs as b_runs, b.extras as b_extras, b.extras_type as b_extras_type,
                   b.is_wicket as b_is_wicket, b.is_boundary as b_is_boundary, b.is_six as b_is_six,
                   b.total_runs as b_total_runs, b.total_wickets as b_total_wickets,
                   b.overs_completed as b_overs_completed, b.balls_in_over as b_balls_in_over,
                   b.crr as b_crr, b.rrr as b_rrr,
                   b.runs_needed as b_runs_needed, b.balls_remaining as b_balls_remaining,
                   b.match_phase as b_match_phase, b.data as ball_data
            FROM match_commentaries c
            LEFT JOIN deliveries b ON c.ball_id = b.id
            WHERE c.match_id = ? AND c.seq > ? AND (c.language = ? OR c.language IS NULL)
            ORDER BY c.seq, c.id
        """
        params: tuple = (match_id, after_seq, language)
    else:
        query = """
            SELECT c.*, b.over as b_over, b.ball as b_ball,
                   b.batter as b_batter, b.bowler as b_bowler, b.non_batter as b_non_batter,
                   b.runs as b_runs, b.extras as b_extras, b.extras_type as b_extras_type,
                   b.is_wicket as b_is_wicket, b.is_boundary as b_is_boundary, b.is_six as b_is_six,
                   b.total_runs as b_total_runs, b.total_wickets as b_total_wickets,
                   b.overs_completed as b_overs_completed, b.balls_in_over as b_balls_in_over,
                   b.crr as b_crr, b.rrr as b_rrr,
                   b.runs_needed as b_runs_needed, b.balls_remaining as b_balls_remaining,
                   b.match_phase as b_match_phase, b.data as ball_data
            FROM match_commentaries c
            LEFT JOIN deliveries b ON c.ball_id = b.id
            WHERE c.match_id = ? AND c.seq > ?
            ORDER BY c.seq, c.id
        """
        params = (match_id, after_seq)

    async with db.execute(query, params) as cur:
        return [_row_to_commentary(r) for r in await cur.fetchall()]


async def get_commentary_by_id(commentary_id: int) -> dict | None:
    """Fetch a single commentary row by its ID, joined with delivery data."""
    db = _get_db()
    query = """
        SELECT c.*, b.over as b_over, b.ball as b_ball,
               b.batter as b_batter, b.bowler as b_bowler, b.non_batter as b_non_batter,
               b.runs as b_runs, b.extras as b_extras, b.extras_type as b_extras_type,
               b.is_wicket as b_is_wicket, b.is_boundary as b_is_boundary, b.is_six as b_is_six,
               b.total_runs as b_total_runs, b.total_wickets as b_total_wickets,
               b.overs_completed as b_overs_completed, b.balls_in_over as b_balls_in_over,
               b.crr as b_crr, b.rrr as b_rrr,
               b.runs_needed as b_runs_needed, b.balls_remaining as b_balls_remaining,
               b.match_phase as b_match_phase, b.data as ball_data
        FROM match_commentaries c
        LEFT JOIN deliveries b ON c.ball_id = b.id
        WHERE c.id = ?
    """
    async with db.execute(query, (commentary_id,)) as cur:
        row = await cur.fetchone()
        return _row_to_commentary(row) if row else None


async def get_commentaries_pending_audio(
    match_id: int,
    language: str | None = None,
) -> list[dict]:
    """
    Fetch commentaries that have text but no audio_url yet.
    Used by the audio generation pipeline to find work to do.
    """
    db = _get_db()
    if language:
        query = """
            SELECT c.*, b.over as b_over, b.ball as b_ball,
                   b.batter as b_batter, b.bowler as b_bowler, b.non_batter as b_non_batter,
                   b.runs as b_runs, b.extras as b_extras, b.extras_type as b_extras_type,
                   b.is_wicket as b_is_wicket, b.is_boundary as b_is_boundary, b.is_six as b_is_six,
                   b.total_runs as b_total_runs, b.total_wickets as b_total_wickets,
                   b.overs_completed as b_overs_completed, b.balls_in_over as b_balls_in_over,
                   b.crr as b_crr, b.rrr as b_rrr,
                   b.runs_needed as b_runs_needed, b.balls_remaining as b_balls_remaining,
                   b.match_phase as b_match_phase, b.data as ball_data
            FROM match_commentaries c
            LEFT JOIN deliveries b ON c.ball_id = b.id
            WHERE c.match_id = ? AND c.language = ?
              AND c.text IS NOT NULL AND c.text != ''
              AND c.audio_url IS NULL
            ORDER BY c.seq, c.id
        """
        params: tuple = (match_id, language)
    else:
        query = """
            SELECT c.*, b.over as b_over, b.ball as b_ball,
                   b.batter as b_batter, b.bowler as b_bowler, b.non_batter as b_non_batter,
                   b.runs as b_runs, b.extras as b_extras, b.extras_type as b_extras_type,
                   b.is_wicket as b_is_wicket, b.is_boundary as b_is_boundary, b.is_six as b_is_six,
                   b.total_runs as b_total_runs, b.total_wickets as b_total_wickets,
                   b.overs_completed as b_overs_completed, b.balls_in_over as b_balls_in_over,
                   b.crr as b_crr, b.rrr as b_rrr,
                   b.runs_needed as b_runs_needed, b.balls_remaining as b_balls_remaining,
                   b.match_phase as b_match_phase, b.data as ball_data
            FROM match_commentaries c
            LEFT JOIN deliveries b ON c.ball_id = b.id
            WHERE c.match_id = ? AND c.language IS NOT NULL
              AND c.text IS NOT NULL AND c.text != ''
              AND c.audio_url IS NULL
            ORDER BY c.seq, c.id
        """
        params = (match_id,)

    async with db.execute(query, params) as cur:
        return [_row_to_commentary(r) for r in await cur.fetchall()]


async def get_deliveries_by_overs(
    match_id: int,
    innings: int,
    overs: list[int],
) -> list[dict]:
    """
    Fetch deliveries for specific over numbers (0-indexed) in a given innings.
    Returns deliveries ordered by ball_index.
    """
    if not overs:
        return []
    db = _get_db()
    placeholders = ",".join("?" * len(overs))
    query = (
        f"SELECT * FROM deliveries "
        f"WHERE match_id = ? AND innings = ? AND over IN ({placeholders}) "
        f"ORDER BY ball_index"
    )
    params = [match_id, innings] + overs
    async with db.execute(query, params) as cur:
        return [_row_to_delivery(r) for r in await cur.fetchall()]


async def get_commentaries_pending_audio_by_ball_ids(
    match_id: int,
    ball_ids: list[int],
    language: str | None = None,
) -> list[dict]:
    """
    Fetch commentaries that have text but no audio_url yet,
    filtered to specific ball (delivery) IDs.
    Used by overs-based audio generation.
    """
    if not ball_ids:
        return []
    db = _get_db()
    placeholders = ",".join("?" * len(ball_ids))
    if language:
        query = f"""
            SELECT c.*, b.over as b_over, b.ball as b_ball,
                   b.batter as b_batter, b.bowler as b_bowler, b.non_batter as b_non_batter,
                   b.runs as b_runs, b.extras as b_extras, b.extras_type as b_extras_type,
                   b.is_wicket as b_is_wicket, b.is_boundary as b_is_boundary, b.is_six as b_is_six,
                   b.total_runs as b_total_runs, b.total_wickets as b_total_wickets,
                   b.overs_completed as b_overs_completed, b.balls_in_over as b_balls_in_over,
                   b.crr as b_crr, b.rrr as b_rrr,
                   b.runs_needed as b_runs_needed, b.balls_remaining as b_balls_remaining,
                   b.match_phase as b_match_phase, b.data as ball_data
            FROM match_commentaries c
            LEFT JOIN deliveries b ON c.ball_id = b.id
            WHERE c.match_id = ? AND c.ball_id IN ({placeholders})
              AND c.language = ?
              AND c.text IS NOT NULL AND c.text != ''
              AND c.audio_url IS NULL
            ORDER BY c.seq, c.id
        """
        params: list = [match_id] + ball_ids + [language]
    else:
        query = f"""
            SELECT c.*, b.over as b_over, b.ball as b_ball,
                   b.batter as b_batter, b.bowler as b_bowler, b.non_batter as b_non_batter,
                   b.runs as b_runs, b.extras as b_extras, b.extras_type as b_extras_type,
                   b.is_wicket as b_is_wicket, b.is_boundary as b_is_boundary, b.is_six as b_is_six,
                   b.total_runs as b_total_runs, b.total_wickets as b_total_wickets,
                   b.overs_completed as b_overs_completed, b.balls_in_over as b_balls_in_over,
                   b.crr as b_crr, b.rrr as b_rrr,
                   b.runs_needed as b_runs_needed, b.balls_remaining as b_balls_remaining,
                   b.match_phase as b_match_phase, b.data as ball_data
            FROM match_commentaries c
            LEFT JOIN deliveries b ON c.ball_id = b.id
            WHERE c.match_id = ? AND c.ball_id IN ({placeholders})
              AND c.language IS NOT NULL
              AND c.text IS NOT NULL AND c.text != ''
              AND c.audio_url IS NULL
            ORDER BY c.seq, c.id
        """
        params = [match_id] + ball_ids

    async with db.execute(query, params) as cur:
        return [_row_to_commentary(r) for r in await cur.fetchall()]


async def update_commentary_audio(commentary_id: int, audio_url: str) -> None:
    """Set the audio_url for a single commentary row."""
    db = _get_db()
    await db.execute(
        "UPDATE match_commentaries SET audio_url = ? WHERE id = ?",
        (audio_url, commentary_id),
    )
    await db.commit()


async def get_recent_commentary_texts(
    match_id: int,
    language: str,
    limit: int = 6,
) -> list[str]:
    """
    Return the last N commentary texts for a match+language (most recent last).
    Used to build commentary_history for LLM context.
    """
    db = _get_db()
    query = """
        SELECT text FROM match_commentaries
        WHERE match_id = ? AND language = ?
          AND event_type = 'commentary' AND text IS NOT NULL AND text != ''
        ORDER BY seq DESC, id DESC
        LIMIT ?
    """
    async with db.execute(query, (match_id, language, limit)) as cur:
        rows = await cur.fetchall()
    # Reverse so oldest is first (chronological order)
    return [r["text"] for r in reversed(rows)]


async def delete_commentaries(match_id: int) -> int:
    """Delete all commentaries for a match. Returns count deleted."""
    db = _get_db()
    cursor = await db.execute(
        "DELETE FROM match_commentaries WHERE match_id = ?", (match_id,)
    )
    await db.commit()
    return cursor.rowcount


async def delete_commentaries_by_ball_ids(match_id: int, ball_ids: list[int]) -> int:
    """Delete commentaries for specific ball (delivery) IDs. Returns count deleted."""
    if not ball_ids:
        return 0
    db = _get_db()
    placeholders = ",".join("?" * len(ball_ids))
    cursor = await db.execute(
        f"DELETE FROM match_commentaries WHERE match_id = ? AND ball_id IN ({placeholders})",
        [match_id] + ball_ids,
    )
    await db.commit()
    return cursor.rowcount


async def delete_match(match_id: int) -> dict:
    """Delete a match and all related data. Returns counts."""
    db = _get_db()
    c1 = await db.execute(
        "DELETE FROM match_commentaries WHERE match_id = ?", (match_id,)
    )
    await db.execute("DELETE FROM innings_batters WHERE match_id = ?", (match_id,))
    await db.execute("DELETE FROM innings_bowlers WHERE match_id = ?", (match_id,))
    await db.execute("DELETE FROM fall_of_wickets WHERE match_id = ?", (match_id,))
    await db.execute("DELETE FROM partnerships WHERE match_id = ?", (match_id,))
    await db.execute("DELETE FROM innings WHERE match_id = ?", (match_id,))
    await db.execute("DELETE FROM match_players WHERE match_id = ?", (match_id,))
    c2 = await db.execute(
        "DELETE FROM deliveries WHERE match_id = ?", (match_id,)
    )
    c3 = await db.execute(
        "DELETE FROM matches WHERE match_id = ?", (match_id,)
    )
    await db.commit()
    return {
        "commentaries_deleted": c1.rowcount,
        "deliveries_deleted": c2.rowcount,
        "match_deleted": c3.rowcount,
    }


async def get_max_seq(match_id: int) -> int:
    """Return the highest seq number for a match, or 0 if none."""
    db = _get_db()
    async with db.execute(
        "SELECT COALESCE(MAX(seq), 0) as max_seq FROM match_commentaries WHERE match_id = ?",
        (match_id,),
    ) as cur:
        row = await cur.fetchone()
        return row["max_seq"] if row else 0


def _row_to_commentary(row: aiosqlite.Row) -> dict:
    result = {
        "id": row["id"],
        "match_id": row["match_id"],
        "ball_id": row["ball_id"],
        "seq": row["seq"],
        "event_type": row["event_type"],
        "language": row["language"],
        "text": row["text"],
        "audio_url": row["audio_url"],
        "data": json.loads(row["data"]),
        "created_at": row["created_at"],
    }
    # Include joined delivery data if present
    if row["b_over"] is not None:
        ball_runs = (row["b_runs"] or 0) + (row["b_extras"] or 0)
        overs_display = f"{row['b_overs_completed']}.{row['b_balls_in_over']}"
        result["ball_info"] = {
            "over": row["b_over"],
            "ball": row["b_ball"],
            "batter": row["b_batter"],
            "bowler": row["b_bowler"],
            "non_batter": row["b_non_batter"],
            "runs": row["b_runs"],
            "extras": row["b_extras"],
            "extras_type": row["b_extras_type"],
            "is_wicket": bool(row["b_is_wicket"]),
            "is_boundary": bool(row["b_is_boundary"]),
            "is_six": bool(row["b_is_six"]),
            "ball_runs": ball_runs,
            # Match snapshot after this delivery
            "total_runs": row["b_total_runs"],
            "total_wickets": row["b_total_wickets"],
            "overs": overs_display,
            "crr": row["b_crr"],
            "rrr": row["b_rrr"],
            "runs_needed": row["b_runs_needed"],
            "balls_remaining": row["b_balls_remaining"],
            "match_phase": row["b_match_phase"],
            "data": json.loads(row["ball_data"]) if row["ball_data"] else None,
        }
    else:
        result["ball_info"] = None
    return result
