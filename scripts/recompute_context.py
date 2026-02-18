#!/usr/bin/env python3
"""
Re-precompute delivery context for all matches.

Only updates deliveries.context JSON and snapshot columns + stats tables.
Does NOT touch commentaries, LLM text, or audio — those are fully preserved.

Usage:
    ./env/bin/python scripts/recompute_context.py
    ./env/bin/python scripts/recompute_context.py --match-id 1
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.storage.database import init_db, close_db, list_matches
from app.engine.precompute import precompute_match_context


async def main(match_id: int | None = None):
    await init_db()
    try:
        if match_id:
            matches = [{"match_id": match_id, "title": f"Match {match_id}"}]
        else:
            matches = await list_matches()

        if not matches:
            print("No matches found.")
            return

        for m in matches:
            mid = m["match_id"]
            title = m.get("title", "")
            print(f"Re-precomputing match {mid}: {title} ... ", end="", flush=True)
            count = await precompute_match_context(mid)
            print(f"{count} deliveries updated.")

        print("\nDone. Commentaries and audio are untouched.")
    finally:
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-precompute delivery context")
    parser.add_argument("--match-id", type=int, default=None, help="Single match ID (default: all)")
    args = parser.parse_args()
    asyncio.run(main(args.match_id))
