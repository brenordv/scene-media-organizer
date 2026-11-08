# Changelog

## 3.0.0

Settings move to Infisical, the database schema is managed by migrations at startup, and the client follows media identifier 2.0.

### Upgrading from 2.x
1. Stop the 2.x service and run `sql/grant_existing_database.sql` (see readme, "Database"). The 3.0.0 migrations alter the existing tables, which needs a role that owns them.
2. Create the `MEDIA_ORGANIZER_*` secrets in Infisical and a machine identity that can read them; pass its `INFISICAL_*` bootstrap variables to the container.
3. Point `MEDIA_ORGANIZER_MEDIA_IDENTIFIER_URL` at a media identifier 2.0 (its base URL, without `/api/guess`).
4. Rotate the Telegram bot token: 2.x could write it to the `activity_tracker` table, OTEL logs and traces when sending failed.
5. Optionally run `on_demand.py recheck` to find media filed from identifier 1.x answers that 2.0 would file elsewhere.

### Breaking changes
- Requires Python 3.14 (`.python-version`); the image is built on `python:3.14-slim`.
- Postgres, media identifier, MQTT host/port/login and Telegram settings come from Infisical (`MEDIA_ORGANIZER_*` secrets). The `POSTGRES_*`, `API_URL`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` variables are ignored, and their command-line flags are gone (passing one stops the run with exit code 2).
- The media identifier URL is the service's base URL; the client adds `/api/guess`.
- The container health check reads a heartbeat file the service writes every minute instead of opening TCP connections to Postgres and MQTT; its start period is 120 s.
- `TELEGRAM_API_BASE` must be an https URL.
- Requires PostgreSQL 13 or newer.

### Added
- Startup database migrations (`migrations/`, ledger table `schema_migrations`, advisory lock so concurrent starts apply each file once). A fresh database is set up on the first start; existing tables are adopted unchanged.
- `sql/create_app_role.sql` (new role and database) and `sql/grant_existing_database.sql` (dedicated role for an existing install).
- `on_demand.py recheck`: re-identifies filed media and reports files the identifier would now file elsewhere; read-only.
- Startup catch-up scan: the service queues files that arrived in the watch folder while it was down.
- `ACTIVITY_DB_LOG_LEVEL` sets the lowest level written to the `activity_tracker` table (default `DEBUG`, as before).
- Indexes on `work_queue(status)`, `work_queue(filename)`, `batch_control(batch_id, work_queue_id)` and open batches.
- `.env.example`, a QA workflow for pull requests to master (`.github/workflows/qa.yml`), and unit tests for every task, repository and the startup steps.

### Changed
- Media identifier 2.0 answers: 204, 400, 404 and 422 are final (`FAILED_ID`); 502, 503, 504 and network errors are retried with backoff and then left queued without using up an attempt (one warning per batch); a 500 or any other unexpected answer uses up an attempt, so the 5-attempt cap ends it instead of retrying it forever.
- MQTT client moved to paho-mqtt's callback API version 2 (version 1 is deprecated).
- 7-Zip in the image is 26.05 (fixes CVE-2026-14266, an XZ decompression heap overflow, and the 26.01 archive-handler CVEs).
- Dependencies at their latest releases, including watchdog 7, Pillow 13, raccoontools 3.7, raccoontools-db 1.1, simple-log-factory-ext-otel 2.0 and infisicalsdk 1.3.
- Lint, format and type checks run on ruff, ruff format and mypy (black and isort removed).
- Work-queue queries name their columns; updates reject fields that are not columns.
- `FAILED_MAX_ATTEMPTS` is logged at error level.

### Fixed
- The Telegram bot token no longer reaches logs, the activity table or traces: failures log the exception class only and the Telegram API is excluded from HTTP tracing.
- `rsync` and `7zz` run with a minimal environment, so no credential in the process environment reaches them.
- A title with no character usable in a folder name ends as `FAILED_ID` instead of crashing the item.
- QoS 1 MQTT messages published before the broker acknowledged the connection are no longer logged as failures (paho sends them once connected).

### Removed
- The application no longer creates tables or the `uuid-ossp` extension on every import; migrations own the schema.
- `.github/workflows/ci.yml`, replaced by `qa.yml`.

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