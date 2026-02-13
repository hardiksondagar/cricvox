"""
SQLite persistence layer.

Tables:
  - matches: match metadata (teams, venue, target, languages)
  - match_balls: ball-by-ball input data (one row per delivery per innings)
  - match_commentaries: generated output (one row per language per moment, FK to balls)

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
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS match_balls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id    INTEGER NOT NULL,
            innings     INTEGER NOT NULL,
            ball_index  INTEGER NOT NULL,
            over        INTEGER NOT NULL,
            ball        INTEGER NOT NULL,
            batsman     TEXT NOT NULL,
            bowler      TEXT NOT NULL,
            data        TEXT NOT NULL,
            context     TEXT,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );

        CREATE INDEX IF NOT EXISTS idx_match_balls
            ON match_balls(match_id, innings, ball_index);

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
            FOREIGN KEY (ball_id) REFERENCES match_balls(id)
        );

        CREATE INDEX IF NOT EXISTS idx_match_commentaries
            ON match_commentaries(match_id, seq);

        CREATE INDEX IF NOT EXISTS idx_match_commentaries_lang
            ON match_commentaries(match_id, language, seq);
    """)

    # Migrate: add context column if missing (for existing DBs)
    try:
        await _db.execute("SELECT context FROM match_balls LIMIT 1")
    except Exception:
        await _db.execute("ALTER TABLE match_balls ADD COLUMN context TEXT")
        logger.info("Migrated match_balls: added 'context' column")

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
) -> dict:
    """Insert a new match. Returns the created record with auto-generated ID."""
    db = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    langs = languages or ["hi"]
    cursor = await db.execute(
        """INSERT INTO matches (title, status, match_info, languages, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (title, status, json.dumps(match_info, default=str), json.dumps(langs), now),
    )
    await db.commit()
    return {
        "match_id": cursor.lastrowid,
        "title": title,
        "status": status,
        "match_info": match_info,
        "languages": langs,
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
        "created_at": row["created_at"],
    }


# ------------------------------------------------------------------ #
#  Match Balls CRUD
# ------------------------------------------------------------------ #

async def insert_ball(
    match_id: int,
    innings: int,
    ball_index: int,
    over: int,
    ball: int,
    batsman: str,
    bowler: str,
    data: dict,
) -> int:
    """Insert one ball delivery. Returns the ball row ID."""
    db = _get_db()
    cursor = await db.execute(
        """INSERT INTO match_balls (match_id, innings, ball_index, over, ball, batsman, bowler, data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (match_id, innings, ball_index, over, ball, batsman, bowler, json.dumps(data, default=str)),
    )
    await db.commit()
    return cursor.lastrowid


async def insert_balls_bulk(match_id: int, innings: int, balls: list[dict]) -> int:
    """Bulk insert balls for an innings. Returns count inserted."""
    db = _get_db()
    rows = [
        (match_id, innings, i, b.get("over", 0), b.get("ball", 0),
         b.get("batsman", ""), b.get("bowler", ""), json.dumps(b, default=str))
        for i, b in enumerate(balls)
    ]
    await db.executemany(
        """INSERT INTO match_balls (match_id, innings, ball_index, over, ball, batsman, bowler, data)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    await db.commit()
    return len(rows)


async def get_balls(match_id: int, innings: int) -> list[dict]:
    """Fetch all balls for a match innings, ordered by ball_index."""
    db = _get_db()
    async with db.execute(
        "SELECT * FROM match_balls WHERE match_id = ? AND innings = ? ORDER BY ball_index",
        (match_id, innings),
    ) as cur:
        return [_row_to_ball(r) for r in await cur.fetchall()]


async def update_ball_context(ball_id: int, context: dict) -> None:
    """Update the pre-computed context for a single ball."""
    db = _get_db()
    await db.execute(
        "UPDATE match_balls SET context = ? WHERE id = ?",
        (json.dumps(context, default=str), ball_id),
    )
    await db.commit()


async def update_balls_context_bulk(updates: list[tuple[int, dict]]) -> int:
    """
    Bulk-update pre-computed context for multiple balls.
    Each item: (ball_id, context_dict).
    Returns count updated.
    """
    db = _get_db()
    rows = [(json.dumps(ctx, default=str), bid) for bid, ctx in updates]
    await db.executemany(
        "UPDATE match_balls SET context = ? WHERE id = ?",
        rows,
    )
    await db.commit()
    return len(rows)


def _row_to_ball(row: aiosqlite.Row) -> dict:
    ctx_raw = row["context"]
    return {
        "id": row["id"],
        "match_id": row["match_id"],
        "innings": row["innings"],
        "ball_index": row["ball_index"],
        "over": row["over"],
        "ball": row["ball"],
        "batsman": row["batsman"],
        "bowler": row["bowler"],
        "data": json.loads(row["data"]),
        "context": json.loads(ctx_raw) if ctx_raw else None,
    }


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
    Fetch commentaries with seq > after_seq, joined with ball data.
    Filters to the requested language + language-independent events (language IS NULL).
    """
    db = _get_db()
    if language:
        query = """
            SELECT c.*, b.over as b_over, b.ball as b_ball,
                   b.batsman as b_batsman, b.bowler as b_bowler, b.data as ball_data
            FROM match_commentaries c
            LEFT JOIN match_balls b ON c.ball_id = b.id
            WHERE c.match_id = ? AND c.seq > ? AND (c.language = ? OR c.language IS NULL)
            ORDER BY c.seq, c.id
        """
        params: tuple = (match_id, after_seq, language)
    else:
        query = """
            SELECT c.*, b.over as b_over, b.ball as b_ball,
                   b.batsman as b_batsman, b.bowler as b_bowler, b.data as ball_data
            FROM match_commentaries c
            LEFT JOIN match_balls b ON c.ball_id = b.id
            WHERE c.match_id = ? AND c.seq > ?
            ORDER BY c.seq, c.id
        """
        params = (match_id, after_seq)

    async with db.execute(query, params) as cur:
        return [_row_to_commentary(r) for r in await cur.fetchall()]


async def delete_commentaries(match_id: int) -> int:
    """Delete all commentaries for a match. Returns count deleted."""
    db = _get_db()
    cursor = await db.execute(
        "DELETE FROM match_commentaries WHERE match_id = ?", (match_id,)
    )
    await db.commit()
    return cursor.rowcount


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
    # Include joined ball data if present
    if row["b_over"] is not None:
        result["ball_info"] = {
            "over": row["b_over"],
            "ball": row["b_ball"],
            "batsman": row["b_batsman"],
            "bowler": row["b_bowler"],
            "data": json.loads(row["ball_data"]) if row["ball_data"] else None,
        }
    else:
        result["ball_info"] = None
    return result


# ------------------------------------------------------------------ #
#  Seed data
# ------------------------------------------------------------------ #

async def seed_matches() -> None:
    """
    Scan app/feed/ for JSON match files. Create match + insert balls.
    Idempotent — skips files whose match title already exists.
    """
    from app.feed.mock_feed import load_match_data, _compute_innings_summary

    feed_dir = Path("app/feed")
    if not feed_dir.exists():
        return

    for filepath in sorted(feed_dir.glob("*.json")):
        try:
            with open(filepath) as f:
                raw = json.load(f)

            match_info_raw = raw.get("match_info", {})
            title = match_info_raw.get("title", filepath.stem.replace("_", " ").title())

            # Skip if already seeded
            existing = await get_match_by_title(title)
            if existing:
                logger.debug(f"Seed: '{title}' already in DB (id={existing['match_id']}), skipping")
                continue

            innings_data = raw.get("innings", [])
            if not innings_data or not isinstance(innings_data[0], dict):
                logger.warning(f"Seed: {filepath.name} has no innings data, skipping")
                continue

            # Build enriched match_info (same logic as mock_feed.load_match_data)
            # Use innings 2 as the "main" innings for chase commentary
            inn2_idx = min(1, len(innings_data) - 1)
            inn2 = innings_data[inn2_idx]

            match_info = {
                **match_info_raw,
                "batting_team": inn2.get("batting_team", ""),
                "bowling_team": inn2.get("bowling_team", ""),
                "target": inn2.get("target") or (innings_data[0].get("total_runs", 0) + 1),
            }

            # Attach first innings summary
            if len(innings_data) >= 2:
                match_info["first_innings"] = _compute_innings_summary(innings_data[0])

            # Innings summaries for display
            innings_summary = []
            for i, inn in enumerate(innings_data):
                if isinstance(inn, dict) and "batting_team" in inn:
                    innings_summary.append({
                        "innings_number": inn.get("innings_number", i + 1),
                        "batting_team": inn.get("batting_team", ""),
                        "bowling_team": inn.get("bowling_team", ""),
                        "total_runs": inn.get("total_runs", 0),
                        "total_wickets": inn.get("total_wickets", 0),
                        "total_balls": len(inn.get("balls", [])),
                    })
            match_info["innings_summary"] = innings_summary

            # Create match
            match_record = await create_match(
                title=title,
                match_info=match_info,
                status="ready",
            )
            match_id = match_record["match_id"]

            # Insert balls for each innings
            total_balls = 0
            for i, inn in enumerate(innings_data):
                if isinstance(inn, dict) and "balls" in inn:
                    balls = inn["balls"]
                    count = await insert_balls_bulk(match_id, i + 1, balls)
                    total_balls += count

            # Pre-compute ball-by-ball context for LLM
            from app.precompute import precompute_match_context
            ctx_count = await precompute_match_context(match_id)

            logger.info(
                f"Seed: added '{title}' (id={match_id}) "
                f"with {total_balls} balls ({ctx_count} pre-computed) from {filepath.name}"
            )

        except Exception as e:
            logger.error(f"Seed: failed to load {filepath}: {e}")
