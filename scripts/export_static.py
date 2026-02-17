#!/usr/bin/env python3
"""
Export matches to a static site for GitHub Pages.

Reads from the SQLite database, exports match list, match details, and commentaries
per language to docs/. Also copies audio files and frontend assets.

Usage:
  python scripts/export_static.py [--base-path /ai-commentator]
  # Default base-path is '' (for user/organization pages like user.github.io)
  # Use --base-path /ai-commentator for project pages (user.github.io/ai-commentator)
"""

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

# Add project root for imports
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.commentary.prompts import strip_audio_tags
from app.models import SUPPORTED_LANGUAGES
from app.storage.database import (
    init_db,
    close_db,
    list_matches,
    get_match,
    get_commentaries_after,
)

DOCS_DIR = ROOT / "docs"
STATIC_DIR = ROOT / "static"


def _enrich_languages(langs: list) -> list[dict]:
    """Enrich language codes with display names from SUPPORTED_LANGUAGES."""
    codes = langs if langs else ["hi"]
    if isinstance(codes, str):
        codes = [codes]
    result = []
    for item in codes:
        code = item["code"] if isinstance(item, dict) else item
        cfg = SUPPORTED_LANGUAGES.get(code)
        if cfg:
            result.append({
                "code": code,
                "name": cfg["name"],
                "native_name": cfg["native_name"],
            })
    return result or [{"code": "hi", "name": "Hindi", "native_name": "हिन्दी"}]


def _match_languages(match: dict) -> list[str]:
    """Get match language codes."""
    langs = match.get("languages") or ["hi"]
    if isinstance(langs, str):
        langs = [langs]
    return [l["code"] if isinstance(l, dict) else l for l in langs]


async def export_matches(base_path: str) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # Tell GitHub Pages not to process with Jekyll (preserves leading-dot files)
    (DOCS_DIR / ".nojekyll").touch()

    # Only export generated matches
    matches = await list_matches(status="generated")
    if not matches:
        print("No generated matches to export.")
        return

    data_dir = DOCS_DIR / "data"
    data_dir.mkdir(exist_ok=True)

    # Write match list
    matches_json = json.dumps(matches, indent=2, default=str)
    (data_dir / "matches.json").write_text(matches_json, encoding="utf-8")
    print(f"Exported {len(matches)} matches to data/matches.json")

    for match in matches:
        match_id = match["match_id"]
        print(f"  Match {match_id}: {match.get('title', '')[:50]}...")

        # Match detail with enriched languages
        full_match = await get_match(match_id)
        if not full_match:
            continue
        full_match["languages"] = _enrich_languages(full_match.get("languages") or [])
        match_path = data_dir / "matches" / str(match_id)
        match_path.mkdir(parents=True, exist_ok=True)
        (match_path / "match.json").write_text(
            json.dumps(full_match, indent=2, default=str),
            encoding="utf-8",
        )

        # Commentaries per language (incl. language-independent events)
        langs = _match_languages(full_match)
        commentaries_dir = match_path / "commentaries"
        commentaries_dir.mkdir(exist_ok=True)

        for lang in langs:
            commentaries = await get_commentaries_after(match_id, -1, language=lang)
            for c in commentaries:
                if c.get("text"):
                    c["text"] = strip_audio_tags(c["text"])
            (commentaries_dir / f"{lang}.json").write_text(
                json.dumps(commentaries, indent=2, default=str),
                encoding="utf-8",
            )
        print(f"    Commentaries: {langs}")

        # Copy audio files
        src_audio = STATIC_DIR / "audio" / str(match_id)
        dst_audio = DOCS_DIR / "audio" / str(match_id)
        if src_audio.exists():
            dst_audio.parent.mkdir(parents=True, exist_ok=True)
            if dst_audio.exists():
                shutil.rmtree(dst_audio)
            shutil.copytree(src_audio, dst_audio)
            count = len(list(dst_audio.glob("*.mp3")))
            print(f"    Audio: {count} files")
        else:
            print(f"    Audio: (no files)")

    # Copy frontend assets
    shutil.copy(STATIC_DIR / "app.js", DOCS_DIR / "app.js")
    shutil.copy(STATIC_DIR / "style.css", DOCS_DIR / "style.css")

    # Copy and modify index.html
    index_src = STATIC_DIR / "index.html"
    index_dst = DOCS_DIR / "index.html"
    html = index_src.read_text(encoding="utf-8")

    # Inject static mode script before </head>
    inject = (
        f'    <script>window.CRICVOX_STATIC = true; window.CRICVOX_BASE_PATH = "{base_path}";</script>\n'
    )
    html = html.replace("</head>", inject + "</head>")

    # Fix asset paths for static mode (same directory)
    html = html.replace('href="/static/style.css"', 'href="style.css"')
    html = html.replace('src="/static/app.js"', 'src="app.js"')

    index_dst.write_text(html, encoding="utf-8")
    print("Copied index.html, app.js, style.css")


def main():
    parser = argparse.ArgumentParser(description="Export static site for GitHub Pages")
    parser.add_argument(
        "--base-path",
        default="",
        help="Base path for project sites, e.g. /ai-commentator (default: '' for user pages)",
    )
    args = parser.parse_args()
    base_path = args.base_path.rstrip("/")

    async def run():
        await init_db()
        try:
            await export_matches(base_path)
        finally:
            await close_db()

    asyncio.run(run())
    print("Done.")


if __name__ == "__main__":
    main()
