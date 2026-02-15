#!/usr/bin/env python3
"""
Scrape Cricbuzz match pages using the Firecrawl API.

Fetches four pages for a given Cricbuzz match ID:
  1. Live scores  (with infinite scroll — 30 scrolls, 5s delay each)
  2. Match facts   (static)
  3. Scorecard     (static)
  4. Squads        (static)

Usage:
    python scripts/scrape_match.py <match_id> [--output-dir DIR]

Example:
    python scripts/scrape_match.py 110406
    python scripts/scrape_match.py 110406 --output-dir data/sample/my_match/

Requires FIRECRAWL_API_KEY in .env (or environment).
"""

import argparse
import sys
import time
from pathlib import Path

# Allow running from repo root: `python scripts/scrape_match.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firecrawl import Firecrawl

from app.config import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://www.cricbuzz.com"

SCROLL_COUNT = 25
SCROLL_DELAY_MS = 2000  # 2 seconds between scrolls

PAGES = [
    {
        "slug": "live-cricket-scores",
        "suffix": "live",
        "needs_scroll": True,
    },
    {
        "slug": "cricket-match-facts",
        "suffix": "info",
        "needs_scroll": False,
    },
    {
        "slug": "live-cricket-scorecard",
        "suffix": "scorecard",
        "needs_scroll": False,
    },
    {
        "slug": "cricket-match-squads",
        "suffix": "squads",
        "needs_scroll": False,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_scroll_actions(count: int = SCROLL_COUNT, delay_ms: int = SCROLL_DELAY_MS) -> list[dict]:
    """Build a list of scroll-down + wait actions for infinite-scroll pages."""
    actions: list[dict] = []
    for _ in range(count):
        actions.append({"type": "scroll", "direction": "down"})
        actions.append({"type": "wait", "milliseconds": delay_ms})
    return actions


def scrape_page(client: Firecrawl, url: str, *, actions: list[dict] | None = None, timeout: int = 30000) -> str:
    """Scrape a single URL and return HTML content."""
    kwargs: dict = {
        "formats": ["html"],
        "only_main_content": True,
        "timeout": timeout,
    }
    if actions:
        kwargs["actions"] = actions

    result = client.scrape(url, **kwargs)
    return result.html or ""


def scrape_match(match_id: str, output_dir: Path) -> None:
    """Scrape all four Cricbuzz pages for the given match ID."""
    api_key = settings.firecrawl_api_key
    if not api_key:
        print("Error: FIRECRAWL_API_KEY is not set. Add it to your .env file.")
        sys.exit(1)

    client = Firecrawl(api_key=api_key)
    output_dir.mkdir(parents=True, exist_ok=True)

    for page in PAGES:
        url = f"{BASE_URL}/{page['slug']}/{match_id}"
        out_file = output_dir / f"match_{match_id}_{page['suffix']}.html"

        print(f"\n{'='*60}")
        print(f"Scraping: {url}")

        actions = None
        timeout = 30000  # 30s default

        if page["needs_scroll"]:
            actions = build_scroll_actions()
            print("No of actions:", len(actions))
            # 30 scrolls × 5s = 150s of waiting + network time → 180s timeout
            timeout = 180000
            print(f"  Infinite scroll enabled: {SCROLL_COUNT} scrolls, {SCROLL_DELAY_MS / 1000:.0f}s delay each")
            print(f"  This will take ~{SCROLL_COUNT * SCROLL_DELAY_MS / 1000:.0f}s — please wait...")

        start = time.time()
        try:
            html = scrape_page(client, url, actions=actions, timeout=timeout)
        except Exception as exc:
            print(f"  ERROR scraping {url}: {exc}")
            continue
        elapsed = time.time() - start

        out_file.write_text(html, encoding="utf-8")
        print(f"  Saved: {out_file}  ({len(html):,} chars, {elapsed:.1f}s)")

    print(f"\n{'='*60}")
    print(f"Done. Output directory: {output_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape Cricbuzz match pages via Firecrawl API",
    )
    parser.add_argument("match_id", help="Cricbuzz numeric match ID (e.g. 110406)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: data/sample/match_{match_id}/)",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or Path(f"data/sample/match_{args.match_id}")
    scrape_match(args.match_id, output_dir)


if __name__ == "__main__":
    main()
