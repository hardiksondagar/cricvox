# Project Memory

> Canonical memory for the **AI Cricket Commentary Engine (CricVox)**.
> Every agent session reads this first and updates it with key findings.

---

## Current Focus

- Project rebranded to **CricVox**
- Major schema refactoring complete — `match_balls` → `deliveries`, `batsman` → `batter` (gender-neutral)
- Normalized database with dedicated tables: `deliveries`, `innings`, `partnerships`, `innings_batters`, `innings_bowlers`, `fall_of_wickets`
- Comprehensive REST API for all operations (matches, deliveries, commentaries, innings stats, partnerships)
- Full test suite: 38 tests across 3 files (database, API, engine)
- Multi-language support active (Hindi via `data/languages.json`, extensible)
- Three TTS vendors integrated: ElevenLabs, Sarvam (Indian languages), OpenAI
- Pipeline: precompute deterministic state → generate LLM commentary + TTS in parallel per language
- Frontend uses polling (not SSE) with sequential audio playback
- Timeline progress bar feature complete
- Match data: T20 World Cup 2024 Final (IND vs SA), loaded via `scripts/load_match.py`

---

## Key Decisions

<!-- Newest first. Format: - YYYY-MM-DD | Decision and rationale -->

- 2026-02-15 | Test suite added — pytest + pytest-asyncio, 38 tests: 14 database CRUD, 13 API integration, 11 engine unit tests; per-test isolated SQLite via tmp_path
- 2026-02-15 | Full match API — `GET /api/matches/{id}/full` returns everything (match, innings with batters/bowlers/FOW/partnerships, deliveries, commentaries, summary) using `asyncio.gather` for parallel DB queries
- 2026-02-15 | Gender-neutral terminology — `batsman` → `batter` across entire codebase (schema, models, API, prompts, frontend)
- 2026-02-15 | `balls` → `deliveries` — table/API/code rename for cricket terminology consistency
- 2026-02-15 | Database normalization — promoted key fields from JSON `data` column to dedicated columns on `deliveries`; added snapshot columns (total_runs, total_wickets, crr, rrr, etc.); moved batter/bowler/FOW stats to dedicated tables
- 2026-02-15 | New tables: `innings` (per-innings summary), `partnerships` (partnership records), `innings_batters`/`innings_bowlers` (player stats), `fall_of_wickets` — all populated during precompute
- 2026-02-15 | New match columns: `venue`, `format`, `team1`, `team2`, `match_date`
- 2026-02-15 | `seed_matches()` removed — replaced by `scripts/load_match.py` which uses API endpoints
- 2026-02-15 | `mock_feed.py` removed — replaced by `scripts/load_match.py`
- 2026-02-15 | `precompute.py` moved to `app/engine/precompute.py`
- 2026-02-15 | Context auto-computed on delivery insertion — no separate precompute API needed for single balls
- 2026-02-15 | Innings summary auto-generated when last ball of innings is added
- 2026-02-15 | Rebranded CricketAI → CricVox
- 2026-02-15 | Timeline progress bar — fixed bottom bar with ball-by-ball progress, badge markers, scrubbing, live mode
- 2026-02-15 | Precompute/Generate split — deterministic state precomputed and stored in `deliveries.context`, then LLM + TTS generation runs separately
- 2026-02-15 | Multi-vendor TTS — ElevenLabs, Sarvam (Bulbul v3), OpenAI (`gpt-4o-mini-tts`)
- 2026-02-15 | Polling over SSE — frontend polls `/api/matches/{id}/commentaries?after_seq=N` every 3 seconds
- 2026-02-15 | Content-hashed audio caching — SHA256 of (text + vendor + voice_id + language) for deduplication
- 2026-02-15 | SQLite for persistence — `aiosqlite` with normalized tables; lightweight, no external DB needed
- 2026-02-15 | LLM gets bare facts only — no shot/delivery/fielding descriptions in prompts; prevents hallucination

---

## Architecture Notes

### Pipeline Flow
```
JSON feed → scripts/load_match.py → API endpoints → SQLite
  → precompute (state + logic + narratives) on insertion
  → generate (LLM commentary × N languages + TTS × N languages)
  → store commentaries in DB → frontend polls + plays audio
```

### File Structure (current)
```
app/
  main.py              — FastAPI app, REST API, all endpoints
  config.py            — Settings via pydantic-settings (.env)
  models.py            — All Pydantic models, enums, SUPPORTED_LANGUAGES
  generate.py          — Offline generation pipeline (LLM + TTS per language)
  engine/
    state_manager.py   — Ball-by-ball state tracking (score, batters, bowlers, partnerships, FOW)
    logic_engine.py    — NarrativeBranch classification, selective context building
    pivot_detector.py  — High-leverage moment detection, equation shift calculation
    precompute.py      — Deterministic state precomputation (state + logic + narratives)
  commentary/
    prompts.py         — All prompt templates (system, user, narrative), audio tag instructions
    generator.py       — OpenAI GPT-4.1 commentary + narrative generation
  audio/
    tts.py             — TTS facade — routes to provider based on language config
    elevenlabs.py      — ElevenLabs v3 provider
    sarvam.py          — Sarvam Bulbul v3 provider
    openai_tts.py      — OpenAI gpt-4o-mini-tts provider
    phonetics.py       — Player name → phonetic spelling map
  storage/
    database.py        — SQLite schema, CRUD, migrations (normalized tables)
    audio.py           — Content-hashed MP3 file storage + deduplication
scripts/
  load_match.py        — Loads match JSON data via API endpoints (replaces seed_matches)
data/
  languages.json       — Language configs (code, name, tts_vendor, voice_id, model, llm_instruction)
  matches.db           — SQLite database (gitignored, created at runtime)
  sample/
    ind_vs_sa_final.json — T20 World Cup 2024 Final ball-by-ball data
tests/
  conftest.py          — Shared fixtures (per-test SQLite, AsyncClient, seed data)
  test_database.py     — 14 database CRUD unit tests
  test_api.py          — 13 API integration tests
  test_engine.py       — 11 engine unit tests (StateManager, LogicEngine, precompute)
static/
  index.html           — SPA dashboard (Tailwind CSS, dark theme)
  app.js               — Polling client, audio playback, scoreboard, timeline
  style.css            — Dark theme styles
  audio/               — Generated MP3 files per match (gitignored)
```

### Database Schema
- **matches**: match_id, title, status, match_info (JSON), languages (JSON), venue, format, team1, team2, match_date, created_at
- **deliveries**: id, match_id, innings, ball_index, over, ball, batter, bowler, non_batter, batter_id, non_batter_id, bowler_id, runs, extras, extras_type, is_wicket, is_boundary, is_six, total_runs, total_wickets, overs_completed, balls_in_over, crr, rrr, runs_needed, balls_remaining, match_phase, data (JSON), context (JSON)
- **match_commentaries**: id, match_id, ball_id (FK), seq, event_type, language, text, audio_url, data (JSON), created_at
- **innings_batters**: id, match_id, innings, name, position, runs, balls_faced, fours, sixes, dots, is_out, strike_rate, out_status, dismissal_info
- **innings_bowlers**: id, match_id, innings, name, balls_bowled, runs_conceded, wickets, maidens, dots, fours_conceded, sixes_conceded, wides, noballs, economy, overs_bowled
- **fall_of_wickets**: id, match_id, innings, wicket_number, batter, batter_runs, team_score, overs, bowler, how
- **innings**: id, match_id, innings_number, batting_team, bowling_team, total_runs, total_wickets, total_overs, extras_total
- **partnerships**: id, match_id, innings, wicket_number, batter1, batter2, runs, balls

### API Endpoints
**Matches:**
- `POST /api/matches` — create match (201)
- `GET /api/matches` — list (optional `?status=` filter)
- `GET /api/matches/{id}` — detail
- `PATCH /api/matches/{id}` — update fields
- `DELETE /api/matches/{id}` — cascade delete all related data
- `GET /api/matches/{id}/full` — everything in one call (match + innings enriched with stats + deliveries + commentaries + summary)

**Deliveries:**
- `POST /api/matches/{id}/deliveries` — single delivery (auto-computes context + innings summary)
- `POST /api/matches/{id}/deliveries/bulk` — bulk insert for an innings (auto-precomputes all context)
- `GET /api/matches/{id}/deliveries` — list (optional `?innings=` filter)
- `GET /api/deliveries/{id}` — single delivery with context

**Innings Stats:**
- `GET /api/matches/{id}/innings/{inn}/summary` — computed from deliveries via StateManager replay
- `GET /api/matches/{id}/innings/{inn}/batters` — from innings_batters table
- `GET /api/matches/{id}/innings/{inn}/bowlers` — from innings_bowlers table
- `GET /api/matches/{id}/innings/{inn}/fall-of-wickets` — from fall_of_wickets table
- `GET /api/matches/{id}/innings/{inn}/partnerships` — from partnerships table
- `GET /api/matches/{id}/innings` — innings records

**Commentaries:**
- `GET /api/matches/{id}/commentaries?after_seq=N&language=hi` — poll
- `GET /api/commentaries/{id}` — single with joined delivery data
- `DELETE /api/matches/{id}/commentaries` — delete all for re-generation

**Generation:**
- `POST /api/matches/{id}/commentaries/generate` — LLM text (background)
- `POST /api/deliveries/{id}/commentaries/generate` — single delivery LLM text
- `POST /api/matches/{id}/commentaries/generate-audio` — TTS (background)
- `POST /api/commentaries/{id}/generate-audio` — single TTS

**Other:**
- `GET /api/matches/{id}/timeline` — all deliveries grouped by innings (frontend progress bar)
- `GET /api/languages` — supported languages list

### Match State (StateManager tracks)
- Score: total_runs, wickets, overs, balls, CRR, RRR, runs_needed, balls_remaining
- Batters: runs, balls, 4s, 6s, dots, dot%, strike_rate, position, milestone detection
- Bowlers: balls, runs, wickets, maidens, dots, 4s/6s conceded, wides, noballs, economy
- Partnership: runs, balls, partnership_number (resets on wicket)
- Fall of wickets: batter, score, team total, overs, how
- Over history: over_runs_history → phase runs (PP/middle/death), run rate windows
- Momentum: last_6_balls, consecutive_dots, scoring_momentum
- Drought/collapse: balls_since_last_boundary (drought at 18+), collapse (3+ in 18 balls)
- Transitions: is_new_bowler, is_new_over, is_strike_change, is_new_batter

### NarrativeBranch Classification (priority order)
1. WICKET_DRAMA — any wicket (top priority)
2. EXTRA_GIFT — wides/noballs in tight situations
3. BOUNDARY_MOMENTUM — 4s and 6s
4. PRESSURE_BUILDER — 3+ consecutive dots or high RRR with low scoring
5. OVER_TRANSITION — end of over (6th legal delivery)
6. ROUTINE — everything else

### Narrative Moments (8 types)
- `first_innings_start` — scene-setting (start_over=1 only)
- `first_innings_end` — innings summary with top performers
- `second_innings_start` — chase begins, target set
- `end_of_over` — bowler figures, over summary
- `new_batter` — after every wicket, situation context
- `phase_change` — powerplay → middle → death (replaces end_of_over)
- `milestone` — batter reaches 50 or 100
- `match_result` — final result, highlights

---

## Testing

### Test Configuration
- `pyproject.toml` — `asyncio_mode = "auto"`, `testpaths = ["tests"]`
- `requirements.txt` — `pytest>=8.0.0`, `pytest-asyncio>=0.24.0`

### Test Fixtures (`tests/conftest.py`)
- `_init_test_db` (autouse) — per-test isolated SQLite via tmp_path, calls init_db/close_db
- `client` — `httpx.AsyncClient` via `ASGITransport` (no real server needed)
- `seeded_match` — creates match + 13 deliveries for innings 1 and 2

### Test Files
- `test_database.py` (14 tests) — all CRUD: matches, deliveries, context/snapshot updates, innings batters/bowlers, FOW, innings, partnerships, commentaries, cascade delete, max_seq
- `test_api.py` (13 tests) — match lifecycle, single/bulk delivery, innings filter, summary, stats endpoints (via precompute), timeline, commentary CRUD, languages, 404s, full match
- `test_engine.py` (11 tests) — StateManager (basic, extras, wicket, over transition, partnership, innings summary), LogicEngine (routine/wicket/boundary), precompute (single + match)

### Running Tests
```bash
python -m pytest tests/ -v
```

---

## Feature Log

<!-- Newest first. Format: - YYYY-MM-DD | Feature description -->

- 2026-02-15 | Full match data API — `GET /api/matches/{id}/full` returns everything in one call with parallel DB queries
- 2026-02-15 | Test suite — 38 tests (14 DB + 13 API + 11 engine) with per-test isolated SQLite
- 2026-02-15 | Gender-neutral terminology — `batsman` → `batter` across entire codebase
- 2026-02-15 | `balls` → `deliveries` rename — table, API paths, code throughout
- 2026-02-15 | Database normalization — promoted fields from JSON, added snapshot columns, new stats tables
- 2026-02-15 | New tables — `innings`, `partnerships`, `innings_batters`, `innings_bowlers`, `fall_of_wickets`
- 2026-02-15 | Match delete API — cascade deletes all related data
- 2026-02-15 | `scripts/load_match.py` — replaces `seed_matches()`, uses API endpoints
- 2026-02-15 | Comprehensive REST API — full CRUD for all entities
- 2026-02-15 | Multi-language commentary — parallel LLM generation, language-specific TTS, frontend language switcher
- 2026-02-15 | Precomputation pipeline — deterministic state stored in `deliveries.context`
- 2026-02-15 | Multi-vendor TTS facade — ElevenLabs/Sarvam/OpenAI based on language config
- 2026-02-15 | Content-hashed audio storage — SHA256 deduplication
- 2026-02-15 | Timeline progress bar — fixed bottom bar with scrubbing, live mode
- 2026-02-15 | SPA frontend with polling — 3s interval, sequential audio playback

---

## Gotchas & Lessons Learned

<!-- Things that wasted time or were non-obvious -->

- Indic scripts use 2-3x more tokens per word — token budgets doubled for non-English
- ElevenLabs v3 speed parameter must only be sent when ≠ 1.0
- Sarvam API returns base64-encoded audio in JSON `audios` array, not raw bytes
- Wides and noballs don't count as legal deliveries — affects ball counting, over detection, maiden tracking
- Run-out wickets don't count for bowler stats
- Non-striker must be inferred from active batters dict when not in ball data
- Commentary history (last 6 lines) is runtime-only — cannot be precomputed since it includes LLM text
- Language-independent events have `language=NULL` — polling must include `language IS NULL` rows
- Frontend resets all state on language switch (re-fetches from seq=0)
- Browser autoplay policy — must not auto-play on page load
- `scripts/load_match.py` maps `batsman` → `batter` in raw JSON before posting to API
- Frontend `app.js` checks for both `inn.deliveries` and `inn.balls` (backward compat)
- `asyncio_mode = "auto"` in pyproject.toml avoids needing `@pytest.mark.asyncio` on every test

---

## Dependencies & Config

### Python Dependencies (`requirements.txt`)
- fastapi>=0.115.0, uvicorn>=0.32.0, openai>=1.50.0, httpx>=0.27.0
- python-dotenv>=1.0.0, pydantic>=2.9.0, pydantic-settings>=2.5.0, aiosqlite>=0.20.0
- pytest>=8.0.0, pytest-asyncio>=0.24.0

### Environment Variables (`.env`)
- `OPENAI_API_KEY` — required for LLM (GPT-4.1) + OpenAI TTS
- `ELEVENLABS_API_KEY` — required for ElevenLabs TTS
- `SARVAM_API_KEY` — required for Sarvam TTS
- `BALL_DELAY_SECONDS` — delay between balls in replay mode (default: 20)

### Language Config (`data/languages.json`)
- Array of objects: `code`, `name`, `native_name`, `tts_vendor`, `tts_voice_id`, `tts_model`, `sarvam_language_code`, `llm_instruction`
- Currently: Hindi (`hi`) with Hinglish code-mixing

### Runtime Data
- `data/matches.db` — SQLite database (created on startup)
- `static/audio/{match_id}/` — generated MP3 files (content-hashed)
- `data/sample/ind_vs_sa_final.json` — T20 World Cup 2024 Final ball-by-ball data

---

## Patterns & Conventions

- **Terminology**: `batter` (not batsman), `deliveries` (not balls) — gender-neutral, cricket-standard
- **Models**: all in `app/models.py` (single file, Pydantic v2)
- **Prompts**: all in `app/commentary/prompts.py`
- **TTS providers**: each in `app/audio/{provider}.py`
- **Frontend**: vanilla HTML/CSS/JS + Tailwind CDN — no build step
- **Config**: pydantic-settings with `.env` file
- **Database**: all queries in `app/storage/database.py`, normalized tables, UTC timestamps
- **Testing**: pytest + pytest-asyncio, per-test isolated SQLite, AsyncClient via ASGITransport
- **Error handling**: TTS/LLM failures return None/fallback, never crash pipeline
- **Async throughout**: FastAPI async, aiosqlite, httpx.AsyncClient, asyncio.gather
- **LLM model**: GPT-4.1, temperature 0.9
- **Commentary word limits**: enforced by prompt
