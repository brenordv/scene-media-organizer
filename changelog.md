# Changelog

## 2.0.0

Major release. The maintenance CLI (`on_demand.py`) now survives interruptions, and the build moved to a `uv`/`pyproject.toml` setup with `psycopg` (v3) replacing `psycopg2`.

### Added
- Resumable maintenance runs. Interrupting `on_demand.py` (Ctrl+C, a crash, or a kill) leaves the batch open; re-running the same command resumes it: the file that was mid-processing is retried, items already finished are skipped, and one final report covers the whole batch. Ctrl+C at the confirmation prompt, before any work starts, aborts without touching the database.
- Interrupted pickups refund the attempt on the `on_demand` path, so a resume does not burn an item's retry budget. The watchdog service still enforces the strict 5-attempt cap.
- Unified configuration (`src/config.py`) with a fixed precedence: CLI flags first, then process environment, then the `.env` file. Every setting has a matching flag, `--env-file PATH` points the fallback at another file, and a missing required value fails fast with a message naming what is missing. `on_demand.py` gained `-y`/`--yes` to skip the confirmation prompt.
- `pyproject.toml` and a committed `uv.lock`; `uv` builds the environment and runs the app.
- `.dockerignore`, and unit tests for the config loader and the pure helpers (`tests/test_config.py`, `tests/test_helpers.py`).

### Changed
- Database driver moved from `psycopg2` to `psycopg` (v3). The connection pool is created once at startup (`src/data/db.py`), proves connectivity with a `SELECT 1` probe under retry, and closes on shutdown. `psycopg[binary]` bundles libpq, so building the environment no longer needs a system `libpq` or a C toolchain.
- Bumped `simple-log-factory-ext-otel` from `1.5.0rc1` to `1.5.0` (with the `psycopg` extra) and adopted `raccoontools` 3.6.0 and `raccoontools-db` 1.0.1.
- Requires Python 3.13 or newer.
- `start.sh` and `start.bat` now check for `uv`, run `uv sync`, and launch `main.py`. They no longer need to be sourced.
- Extracted pure, dependency-free logic into `src/helpers.py` so it can be unit-tested on its own, and reworked the Dockerfile around the `uv` build.

### Removed
- `requirements.txt`, replaced by `pyproject.toml` and `uv.lock`.

## 1.0.0
- Initial release.