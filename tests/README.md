# Tests

## Running tests

```bash
./env/bin/pytest tests/ -v
```

Run a specific file:

```bash
./env/bin/pytest tests/test_database.py -v
```

Run a single test:

```bash
./env/bin/pytest tests/test_engine.py::test_state_manager_basic -v
```

## Test files

| File | Tests | What it covers |
|---|---|---|
| `test_database.py` | 20 | CRUD for all tables — matches, deliveries, commentaries, innings batters/bowlers, fall of wickets, innings, partnerships, match players |
| `test_api.py` | 17 | FastAPI endpoint integration — match lifecycle, delivery insert (single + bulk), innings stats, commentaries, languages, 404s, full match export, player IDs |
| `test_engine.py` | 11 | StateManager (score/wickets/overs/partnerships/extras), LogicEngine (branch classification), precompute (single ball + full match) |
| `test_real_data.py` | 11 | End-to-end with real IND vs SA T20 WC 2024 Final JSON — loads match, verifies all tables, checks context structure and cross-table consistency |

## How it works

### Fixtures (`conftest.py`)

- **`_init_test_db`** (autouse) — creates a fresh SQLite DB in a temp directory before each test, tears it down after. Every test gets a clean slate.
- **`client`** — `httpx.AsyncClient` wired to the FastAPI app via `ASGITransport`. No real server needed.
- **`seeded_match`** — pre-creates a match with 12 deliveries across 2 innings (dots, singles, fours, sixes, wides, wickets). Used by tests that need existing data.

### No external services

Tests never call OpenAI, ElevenLabs, or any TTS provider. They only exercise the database, API routing, and engine logic. Commentary generation and audio are tested separately via manual runs.

### Real data test

`test_real_data.py` requires the sample match file at `data/sample/match_1/match_1.json`. It loads the full match through the same pipeline as `scripts/load_match.py` and validates every table against known values from the actual match.
