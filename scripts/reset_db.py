#!/usr/bin/env python3
"""
Drop all tables from the database and recreate them via init_db().

Usage:
    python scripts/reset_db.py              # uses data/matches.db
    python scripts/reset_db.py path/to.db   # custom path
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

# Allow importing app when run as script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# All table names in dependency order (children first)
TABLES = [
    "match_commentaries",
    "fall_of_wickets",
    "innings_batters",
    "innings_bowlers",
    "partnerships",
    "innings",
    "match_players",
    "deliveries",
    "matches",
]


def reset(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    for table in TABLES:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        print(f"  Dropped {table}")

    conn.commit()
    conn.close()
    print(f"\nRecreating tables via init_db()...")

    # Point database module at this path and run init_db
    import app.storage.database as db_mod
    path = Path(db_path)
    db_mod.DB_DIR = path.parent
    db_mod.DB_PATH = path

    async def recreate() -> None:
        await db_mod.init_db()
        await db_mod.close_db()

    asyncio.run(recreate())
    print(f"Done. All tables dropped and recreated: {db_path}")
    print("Database is ready for use.")


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(Path("data") / "matches.db")
    if not Path(db).exists():
        print(f"Database not found: {db}")
        sys.exit(1)
    reset(db)
