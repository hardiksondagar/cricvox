# CricVox — The Voice of Cricket

AI-powered cricket commentary engine that replays T20 matches ball-by-ball with LLM-generated commentary and text-to-speech audio.

Built with Python 3.11+, FastAPI, OpenAI GPT-4.1, and multi-vendor TTS (ElevenLabs, Sarvam, OpenAI).

## How It Works

```
Ball-by-ball JSON → State Engine → Logic Engine → LLM Commentary → TTS Audio → Web Player
```

1. **Load** match data from a JSON feed into the database
2. **Precompute** ball-by-ball state (score, batters, bowlers, partnerships, run rates)
3. **Classify** each delivery (routine, boundary momentum, wicket drama, pressure builder, etc.)
4. **Generate** commentary text via GPT-4.1 with selective context per delivery
5. **Synthesize** audio via ElevenLabs / Sarvam / OpenAI TTS
6. **Play** through a web dashboard with live scoreboard, commentary feed, and audio timeline

## Features

- Ball-by-ball commentary with narrative moments (innings start/end, over summaries, milestones, phase changes)
- Multi-language support (configurable via `data/languages.json`)
- Pivot detection for high-leverage match moments
- Interactive timeline with scrubbing, play/pause, and audio queue
- Live mode (real-time generation with polling) and replay mode
- Static site export for GitHub Pages (no backend required)

## Setup

```bash
# Create virtualenv
python3 -m venv env
source ./env/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Edit .env with your keys
```

## Configuration

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key (GPT-4.1 for commentary) |
| `ELEVENLABS_API_KEY` | ElevenLabs API key (TTS) |
| `SARVAM_API_KEY` | Sarvam API key (TTS, optional) |
| `BALL_DELAY_SECONDS` | Delay between deliveries during live generation (default: 20) |
| `COMMENTATOR_PERSONALITY` | LLM prompt style: default, hype_man, storyteller, analyst, entertainer, freestyle |

## Running

```bash
# Start the server
./env/bin/uvicorn app.main:app --reload

# Open the dashboard
open http://localhost:8000
```

### Load a Match

```bash
python scripts/load_match.py data/sample/match_1/match_1.json
```

### Generate Commentary

Via the API or dashboard:

```bash
# Generate all commentary text for a match
curl -X POST http://localhost:8000/api/matches/1/generate_commentaries

# Generate text + audio
curl -X POST "http://localhost:8000/api/matches/1/generate_commentaries?generate_audio=true"
```

## Static Site (GitHub Pages)

The frontend works in two modes: with the full backend (API mode) or as a static site served from pre-exported JSON and audio files.

```bash
# Export generated matches to docs/
./env/bin/python scripts/export_static.py

# For project pages (e.g. user.github.io/ai-commentator)
./env/bin/python scripts/export_static.py --base-path /ai-commentator

# Test locally
cd docs && python -m http.server 8000
```

In your repo settings, set GitHub Pages source to `docs/` folder on `main`.

## Testing

```bash
./env/bin/pytest tests/ -v
```

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Uvicorn (async) |
| LLM | OpenAI GPT-4.1 |
| TTS | ElevenLabs v3, Sarvam Bulbul v3, OpenAI gpt-4o-mini-tts |
| Frontend | Vanilla HTML/CSS/JS + Tailwind CDN |
| Database | SQLite via aiosqlite |
| Testing | pytest + pytest-asyncio |

## Project Structure

```
app/
  main.py              — FastAPI app, REST API endpoints
  config.py            — Settings via pydantic-settings
  generate.py          — LLM + TTS generation pipeline
  engine/              — State tracking, logic classification, precomputation
  commentary/          — Prompt templates, GPT-4.1 commentary generation
  audio/               — TTS providers (ElevenLabs, Sarvam, OpenAI)
  storage/             — SQLite database, audio file storage
scripts/
  load_match.py        — Load JSON match data into DB
  export_static.py     — Export to docs/ for GitHub Pages
static/
  index.html           — SPA dashboard
  app.js               — Frontend client
  style.css            — Dark theme styles
docs/                  — Static site output (GitHub Pages)
tests/                 — pytest test suite
```
