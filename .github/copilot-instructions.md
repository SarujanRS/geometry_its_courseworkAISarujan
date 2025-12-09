# Geometry ITS — Copilot Instructions

## Project Overview
- **Single-file Flask app** (`app.py`) for an area-calculation ITS (Intelligent Tutoring System).
- **Ontology-driven hints**: Loads `geometry_its.owl` (OWL2, via `owlready2`) for context-aware hints; fallback to Python constants if OWL fails.
- **SQLite DB** (`its_geometry.db`, WAL mode): Handles users, pre-assessment, and stage attempts. Schema is created/updated in `init_db()`.

## Key Files & Structure
- `app.py`: All routes, inline HTML templates, DB helpers, question logic, and stage metadata (`STAGE_META`).
- `geometry_its.owl`: Ontology for hints, editable in Protégé. Served at `/geometry_its` and `/geometry_its.owl`.
- `templates_stage_start.html`: Not used by Flask's Jinja loader; most templates are inline in `app.py`.
- `tests/`: Contains smoke tests (e.g., `test_smoke.py`). No full test suite by default.

## Core Patterns & Conventions
- **DB access**: Always use `get_db()` for per-request connection. PRAGMAs (`WAL`, `busy_timeout=30000`) are set automatically.
- **Units required**: All answers must include units (e.g., `24 cm²`). Use `parse_answer_with_unit()` and `convert_to_cm()` for parsing and normalization.
- **Grading**: Use `math.isclose(user_val, expected, rel_tol=1e-6)` for float comparison.
- **Stage progression**: One attempt per stage (`UNIQUE(user_id, stage)`), enforced in DB and UI. Stages are unlocked sequentially.
- **Hints**: Use `get_hint_text(name, default)` to fetch from OWL; fallback to `HINT_*` constants if not found.
- **Inline templates**: Most HTML is rendered via `render_template_string()`.

## Developer Workflows
- **Run locally**:
  ```powershell
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  python app.py
  ```
  App runs at [http://localhost:5000](http://localhost:5000).
- **Reset DB**: Visit `/dev/reset_db` or delete `its_geometry.db*` and restart the app.
- **Debug DB locks**: Avoid Flask reloader; ensure only one process writes to DB.
- **Test routes**: `/register`, `/login`, `/study`, `/practice`, `/pre/start`, `/stage/<n>/start`.

## Extending the System
To add a new shape or question type:
1. Add a branch in `gen_question()` (in `app.py`) to generate the prompt and answer (in cm²), and set `kind`.
2. Add a case to `hint_for_kind()` and a `HINT_*` constant, or add a new hint individual in `geometry_its.owl`.
3. Optionally update `STAGE_META` to lock the shape to a stage.
4. Test via `/practice/shape/<shape>`, `/study/shape/<shape>`, and stage flow.

## Debugging & Gotchas
- **OWL not loaded**: Console logs show errors; app falls back to Python hints.
- **DB locked**: Stop all other processes, remove WAL files, and restart with `python app.py` (no reloader).
- **Security**: `app.secret_key` is `dev` by default—change for production.

## AI Agent Guidance
- Make minimal, self-contained edits (e.g., update `gen_question()` and `hint_for_kind()` together).
- Use `init_db()` for schema changes; migrations are manual.
- If adding features, consider adding a manual test in `tests/`.

---
If any section is unclear or missing, please request clarification or suggest additions for your workflow.
# Geometry ITS — Copilot Instructions (concise)

Short summary
- Single-file Flask app (`app.py`) implementing an ITS for area calculations.
- Uses `owlready2` to load `geometry_its.owl` for hint text; stores data in `its_geometry.db` (SQLite/WAL).

Quickstart (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py  # runs in debug mode at http://localhost:5000
```

Key files
- `app.py` — All routes, templates (inline), DB helpers, and question generation.
- `geometry_its.owl` — Ontology for hints; edited with Protégé and served at `/geometry_its`.
- `its_geometry.db` — SQLite database (WAL enabled). `init_db()` creates tables on start.
- `templates_stage_start.html` — convenience file; app primarily uses inline template variables in `app.py`.

Important patterns & APIs
- DB: use `get_db()` for per-request connection. PRAGMAs: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=30000`.
- Questions: `gen_question(override_shape=None, difficulty='Beginner')` returns dict: `prompt`, `answer` (cm²), `kind`.
- Units: `parse_answer_with_unit(raw)` extracts a float and boolean if unit present; answers must include units and are converted to cm².
- Hints: `get_hint_text(name, default)` reads OWL individuals by name; hint constants (HINT_*) are populated at startup.
- Stages: `STAGE_META` controls stage names, class, difficulty. `UNIQUE(user_id, stage)` enforces single attempt per stage.

How to add a shape (example)
1. Update `gen_question()` — add branch for shape, compute answer in cm² (use `convert_to_cm()`), set `kind`.
2. Add a `hint_for_kind()` case and `HINT_*` constant (or `geometry_its.owl` entry) for the hint text.
3. Update `STAGE_META` if adding a stage with fixed `class`.
4. Test via `/practice/shape/<shape>`, `/study/shape/<shape>` and a stage flow (`/stage/1/start`).

Developer & debugging tips
- Reset DB: visit `/dev/reset_db` or remove `its_geometry.db`, `its_geometry.db-wal`, `its_geometry.db-shm` then restart.
- DB locks: run `python app.py` with `use_reloader=False` and stop other processes; remove WAL files if needed.
- OWL loading: errors are printed at startup; if not loaded, fallbacks are used (HINT_ constants).
- Default `app.secret_key` is `dev` — change for production.

Sanity checks & endpoints to exercise on local dev
- `/register`, `/login`, `/logout`
- `/study`, `/study/shape/<shape>` — check hint text is pulled from OWL or fallback.
- `/practice` & `/practice/shape/<shape>` — verify answers parsed and graded.
- `/pre/start`, `/pre/q/1`, `/pre/result` — pre-assessment flow.
- `/stage/<n>/start`, `/stage/<n>/q/1`, `/stage/<n>/result` — stages flow and unique attempt enforcement.
- `/geometry_its` and `/geometry_its.owl` — ensure OWL is served as `application/rdf+xml`.

Notes for agents (what to look for)
- Prefer small, safe edits: templates in `app.py` are inline; changing `templates_stage_start.html` is fine but the app may not use it directly.
- Use `init_db()` to add schema changes. Tests or a demo seed (sample users/questions) are not currently present — ask to scaffold.
- Keep changes isolated: update `STAGE_META` and `gen_question()` alongside `hint_for_kind()` and any new `HINT_*` constants.

Reach out with what tasks you want added: unit tests, CI for `python -m pytest`, or demo data fixtures.
  - The OWL file remains loaded at startup by `owlready2` using the file path in `app.py`. A convenience route is available for development:
  - `/geometry_its` and `/geometry_its.owl` now both return the OWL file with Content-Type `application/rdf+xml`.
  

# Geometry ITS Copilot Instructions
## Architecture Overview

**Geometry ITS** is a compact Flask-based Intelligent Tutoring System (ITS) that focuses on calculating areas for rectangles and triangles. The project purposefully keeps implementation in a single entry file (`app.py`) to be lightweight for teaching and demo purposes.


### Why (design decisions)

## Quickstart (developer)
Run locally with a Python virtual environment (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```
The app is available at `http://localhost:5000`. Note: `app.secret_key` is set to `dev`—replace for production. `app.run(debug=True, use_reloader=False)` disables the reloader to avoid DB re-opening causing WAL locks.

## Key patterns & examples (project-specific)

```py
def get_db():
    db_uri = f"file:{DB_PATH.as_posix()}?cache=shared"
    g.db = sqlite3.connect(db_uri, uri=True, timeout=30, check_same_thread=False)
    g.db.row_factory = sqlite3.Row
    g.db.execute("PRAGMA journal_mode=WAL;")
    g.db.execute("PRAGMA busy_timeout=30000;")
```


```py
def parse_answer_with_unit(raw: str) -> (float, bool):
    # returns (numeric_val, is_unit_present)
```

### Stage metadata example
`app.py` contains `STAGE_META` (1..10) which maps stage numbers to metadata used by the dashboard and question generation:

```py
STAGE_META = {
  1: {"name": "Shape basics", "class": "shape", "difficulty": "Beginner", "topic": "Shape"},
  2: {"name": "Rectangles", "class": "rectangle", "difficulty": "Beginner", "topic": "Rectangle"},
  3: {"name": "Squares", "class": "square", "difficulty": "Beginner", "topic": "Square"},
  # 4..10 continue
}
```
When a stage `class` maps to a supported shape (rectangle, triangle, square, circle), the stage will generate questions for that shape using `gen_question(class)`.
Responses without units render a warning, example message: `Please include units (e.g. 24 cm²).`.

```py
math.isclose(user_value, expected, rel_tol=1e-6)
```
This is used to decide question correctness (10 points per question), pass threshold 60/100.

## Development & Debugging (common tasks)

```powershell
Remove-Item its_geometry.db, its_geometry.db-wal, its_geometry.db-shm -ErrorAction SilentlyContinue
```
  - Ensure not running multiple instances of the app writing to DB.
  - Confirm `PRAGMA journal_mode` is `wal` and `busy_timeout` is set (default 30000ms).
  - Re-run the app after removing `its_geometry.db-wal` if necessary.

  - The app prints an error if `geometry_its.owl` cannot be loaded; it falls back to default strings in the module.
  - Edit `geometry_its.owl` with Protégé and add/update `Hint_*` named individuals (e.g. `Hint_PerimeterVsArea`, `Hint_TriangleHalf`, `Hint_SquareUnits`). Their `hasText` values are read at startup.

## Code changes & patterns you’ll often edit


  1. Add shape to `gen_question()` and produce `answer` in `cm²` (use `convert_to_cm()` to normalize units).
  2. Add a `hint_for_kind()` clause for the shape to expose a hint from OWL.
  3. Add an OWL `Hint_*` named individual, or a fallback `HINT_*` constant.


## Database schema and important constraints


Use `get_db()` and the SQL strings in `init_db()` to add fields or new tables (wrap in `executescript()` and update `init_db()` if adding schema changes).

## Local testing & manual QA

  1. Register a user (`/register`).
  2. Start the pre-assessment (`/pre/start`) & answer questions (units required) — valid input: `24 cm²`, `24cm²`, `24 cm^2`, `24cm2`, `24 sq cm`.
  3. Check stages flow: complete Stage 1 → Stage 2 unlocks; each stage is single attempt.

## Security & Production notes (non-exhaustive)

## Quick file map (what to edit for common changes)

If something is missing from these instructions, or you'd like me to scaffold tests or a CI job, tell me what you want to validate (e.g., end-to-end stage flow or OWL loading) and I'll extend this file accordingly.

````
# Geometry ITS Copilot Instructions

## Architecture Overview

**Geometry ITS** is an Intelligent Tutoring System (ITS) for teaching area calculations (rectangles & triangles). Built with Flask, it features:

- **Ontology-driven hints** loaded from `geometry_its.owl` (OWL 2 format, editable in Protégé)
- **Staged progression system**: 10 sequential stages, one attempt each; pre-assessment (10 questions, once)
- **SQLite WAL persistence** with pragma tuning for concurrency
- **Per-request DB connections** via Flask's `g` context to avoid threading issues

### Data Flow
1. User logs in → session stores `uid` in Flask session
2. Question generation → `gen_question()` creates random rectangles/triangles with unit conversion
3. Answer parsing → `parse_answer_with_unit()` enforces cm² units; accepts `24 cm²`, `24cm²`, `24cm^2`, `24 sq cm`
4. Scoring → uses `math.isclose(val, expected, rel_tol=1e-6)` for floating-point tolerance (critical for area math)
5. Ontology hints → loaded at startup into `HINT_*` constants; falls back to hardcoded text if OWL load fails

## Key Patterns

### Answer Validation
- **Strict unit requirement**: Students MUST include units. Rejected answers trigger: `flash("Please include units (e.g. 24 cm²).")` and re-render question.
- **Unit normalization**: All answers converted to cm²; 1m = 100cm, so 2m × 3m = 60,000cm² (not 6cm²).
- **Tolerance**: `math.isclose(val, expected, rel_tol=1e-6)` allows ±0.0001% error (e.g., 24.0000001 ≈ 24 ✓).

### Stage Progression
- **Linear unlock**: Stage N only starts if Stage N-1 is completed (`finished_at IS NOT NULL`).
- **Single attempt per stage**: `UNIQUE(user_id, stage)` constraint + redirect to results if already finished.
- **Passing threshold**: Score ≥ 60/100 (6 of 10 questions correct).

### Stage metadata mapping
- Stages now include metadata in `app.py` (`STAGE_META`) with fields: `name`, `class`, `difficulty`, and `topic`.
- The dashboard will show `name` and `difficulty`; if `class` maps to a supported shape (rectangle, triangle, square, circle) `gen_question()` will generate those specific questions for the stage.

### Study & Practice routes
- The Study page (`/study`) lists shapes; each shape links to `/study/shape/<shape>` which includes a short description and a hint. Example: `/study/shape/square`.
- Practice page (`/practice/shape/<shape>`) provides shape-specific practice questions using `gen_question(override_shape=shape, difficulty=<level>)`. The preferred difficulty is read from the session or via query param `?level=`.

### Stage difficulty selection
- Starting a stage (`/stage/<n>/start`) shows a difficulty selection (Beginner/Intermediate/Advanced) if the stage is available. The selected level is saved to `session['preferred_level']` and all 10 stage questions are generated using `gen_question(..., difficulty=level)`.

### Hint Strategy
Hints are **context-aware** based on shape:
- **Rectangle**: `HINT_PERIM + HINT_UNITS` (area vs perimeter confusion, unit conversion)
- **Triangle**: `HINT_TRI + HINT_UNITS` (the ÷2 step)

Hints are **pulled from OWL at startup**; if OWL fails to load, app falls back to Python defaults.

### Database Design
- **Foreign keys enabled**: `PRAGMA foreign_keys=ON`
- **Transactions**: All multi-step updates wrapped in `execute()` + `commit()`
- **Connection pooling**: WAL mode + `busy_timeout=30000` + `synchronous=NORMAL` to handle concurrent requests
- **Row factory**: `sqlite3.Row` for dict-like access (e.g., `row["username"]`)

## Development Workflow

### Running the App
```bash
python app.py
```
Starts Flask dev server at `http://localhost:5000`. Debug mode enabled; auto-reload disabled to prevent DB lock issues.

### Database Operations
- **Full reset**: Visit `/dev/reset_db` to wipe and reinitialize (deletes `its_geometry.db` and WAL files).
- **Schema init**: Runs automatically on app startup via `init_db()` in app context.
- **Accessing DB**: Use `get_db()` function—returns per-request connection with pragmas already configured.

### Modifying Hints
Edit `geometry_its.owl` in Protégé:
- Locate hint individuals: `Hint_PerimeterVsArea`, `Hint_TriangleHalf`, `Hint_SquareUnits`
- Update `hasText` property values
- Save; app reloads hints on restart (via `get_hint_text()` fallback check)

## Common Tasks

### Add a New Question Type
1. Update `gen_question()` to add shape type in `random.choice()`
2. Compute correct answer in cm² (convert units via `convert_to_cm()`)
3. Add hint text in `hint_for_kind()` (e.g., `elif kind == "circle": return HINT_CIRCLE + HINT_UNITS`)
4. Load new hint from OWL in `HINT_*` constants section

### Debug Answer Parsing
- Unit not recognized? Check `parse_answer_with_unit()` suffixes: `cm²`, `cm^2`, `cm2`, `sqcm` (case-insensitive, spaces stripped)
- Value extraction: numeric chars + `.` and `-` only; e.g., `24 cm²` → `24`, `24.5 cm²` → `24.5`
- Test in Python REPL: `parse_answer_with_unit("24 cm²")` should return `(24.0, True)`

### Check Student Progress
Query `stage_attempts` table:
```sql
SELECT user_id, stage, score, passed, finished_at FROM stage_attempts 
WHERE user_id = ? ORDER BY stage;
```
- `passed = 1` → stage passed (score ≥ 60)
- `finished_at IS NULL` → in progress or not started

## Troubleshooting

**"Database is locked" errors**: WAL mode + `busy_timeout` should handle contention. If persists:
- Check no external processes accessing `its_geometry.db`
- Verify `PRAGMA journal_mode` returns `wal`
- Consider increasing `busy_timeout` (currently 30s)

**OWL file not loading**: Check file path is absolute or relative to `APP_ROOT`. If error is silently caught, hints revert to Python defaults—check app startup logs for `"Could not load ontology..."` message.

**Unit mismatch on grading**: Always verify question generation used `convert_to_cm()` correctly. Debug: print question's `answer` field (should be in cm²) vs parsed student answer.
