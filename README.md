# AI Cricket Commentary Engine

An automated commentary system that consumes a ball-by-ball JSON feed and generates real-time, context-aware, emotionally resonant narrative commentary with Text-to-Speech audio.

## Features

- **Real-time T20 commentary** from a mock ball-by-ball feed
- **Context-aware narrative branches**: routine, boundary, wicket, pressure, over transition, extras
- **Pivot detection** for high-leverage moments
- **OpenAI GPT-4o-mini** for concise, vivid commentary generation
- **ElevenLabs TTS** with emotion-mapped voice settings
- **Live web dashboard** with scoreboard, commentary feed, and audio playback via SSE

## Setup

1. **Install dependencies:**

```bash
pip install -r requirements.txt
```

2. **Configure API keys:**

```bash
cp .env.example .env
# Edit .env with your OpenAI and ElevenLabs API keys
```

3. **Run the server:**

```bash
uvicorn app.main:app --reload
```

4. **Open the dashboard:**

Navigate to [http://localhost:8000](http://localhost:8000) in your browser.

## Architecture

```
Mock Feed (JSON) → State Manager → Logic Engine → OpenAI LLM → ElevenLabs TTS → SSE → Web Dashboard
```

## Configuration

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `ELEVENLABS_API_KEY` | ElevenLabs API key |
| `ELEVENLABS_VOICE_ID` | ElevenLabs voice ID to use |
| `BALL_DELAY_SECONDS` | Delay between balls in seconds (default: 20) |
