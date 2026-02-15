#!/usr/bin/env python3
"""
Drop all tables from the database. Tables are recreated by init_db() on next app startup.

Usage:
    python scripts/reset_db.py              # uses data/matches.db
    python scripts/reset_db.py path/to.db   # custom path
"""

import sqlite3
import sys
from pathlib import Path

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
    print(f"\nDone. All tables dropped from: {db_path}")
    print("Tables will be recreated on next app startup (init_db).")


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(Path("data") / "matches.db")
    if not Path(db).exists():
        print(f"Database not found: {db}")
        sys.exit(1)
    reset(db)
