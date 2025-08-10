# Scene Media Organizer (SMO)

A Python-based file monitoring and media organization system that automatically processes video files and archives,
organizing them into structured directories based on media metadata.

### Request flow
The chart below shows a simplified, happy-path request flow.
![Data flow chart](data-flow.png)

## How does it work?
We have four major elements in this application:
1. File system monitor: Triggered whenever a file/folder is created.
2. Memory Queue: To temporarily hold the files that we receive in the events
3. Activity Tracker: Logs all the activity happening in the system, so it can be closely monitored.
4. MQTT Queue: To decouple the file processing from the notification system.

### File Processing
- Files created under `WATCH_FOLDER` are detected by a watchdog observer (`main.py`).
- Each file event is enqueued in memory and normalized (`src/queue_worker.py`):
  - Directories are ignored.
  - Archives are detected, and only the main/first volume is considered (multipart volumes are ignored: we just need the main file to decompress it).
  - Files named like “sample” or executables are ignored.
  - Remaining items are persisted to the Postgres-backed work queue as `PENDING` via `WorkQueueManager`.
- The batch processor (`src/batch_processor.py`) continuously fetches the next batch and processes each item:
  - Wait until the file is stable (size unchanged for a short period).
  - Identify media via the Media Identifier API (`API_URL`). Items without valid metadata are marked `FAILED_ID`.
  - If the item is an archive, it is decompressed in place (supports 7z/rar/zip/tar/gz/bz2/xz); then the item is marked `DONE`. The new file will be processed in the next batch automatically.
  - If it is a video file, the destination is resolved from metadata:
    - Movies → `MOVIES_BASE_FOLDER/<Title>--<Year>` (year optional)
    - TV → `SERIES_BASE_FOLDER/<Title>/SeasonXX`
  - The file is copied to the destination; on success the item is marked `DONE`, otherwise it will be retried once.
  - At the end of the batch, any straggling `WORKING` items are moved back to `PENDING`, so we can give it one more try, the batch is closed, and a verification step compares source/destination (size and SHA-256) for `DONE` items.
  - A completion payload (items, verification result and details) is published to MQTT for notifications.

### Notification System
- A background consumer (`src/notification_receiver.py`) subscribes to `MQTT_BASE_TOPIC` using `NotificationRepository` (MQTT).
- On batch-complete messages it:
  - Deserializes the payload and computes insights (totals, per-status counts, archives, destination set count, failures, unique filenames) and a summary.
  - Renders a concise, HTML-formatted report, including verification results (size/hash checks).
  - Splits long messages to respect Telegram limits and sends them via the Telegram Bot API (`TELEGRAM_*` vars) using `send_telegram_message`.

## Requirements

### External dependencies
- Postgres database
- [media identifier api](https://github.com/brenordv/media-identifier-api)
- Rar command line tool

#### Linux dependencies
```bash
sudo apt-get update
sudo apt-get install libpq-dev python3-dev build-essential unrar
```

## Configuration

The application uses environment variables for configuration:

- `WATCH_FOLDER` - Directory to monitor for new files
- `MOVIES_BASE_FOLDER` - Base directory for organizing movies
- `SERIES_BASE_FOLDER` - Base directory for organizing TV series
- `POSTGRES_HOST` - Postgres host
- `POSTGRES_PORT` - Postgres port
- `POSTGRES_USER` - Postgres user
- `POSTGRES_PASSWORD` - Postgres password
- `API_URL` - URL for the media identifier API
- `MQTT_HOST` - MQTT host
- `MQTT_PORT` - MQTT port
- `MQTT_BASE_TOPIC`: Base topic for MQTT messages
- `MQTT_TOPIC_ID`: Topic for media identifier API responses
- `TELEGRAM_BOT_TOKEN`: Telegram bot token
- `TELEGRAM_CHAT_ID`: Telegram chat ID
- `TELEGRAM_PARSE_MODE`: Telegram parse mode. Defaults to HTML
- `TELEGRAM_DISABLE_WEB_PREVIEW`: Telegram disable web preview. Defaults to False
- `TELEGRAM_DISABLE_NOTIFICATION`: Telegram disable notification. Defaults to False
- `WATCHDOG_CHANGE_DEST_OWNERSHIP_ON_COPY`: Watchdog change destination ownership on copy. Defaults to False
- `UNRAR_PATH` - Required for Windows executions. On Linux, it defaults to `unrar`.

## Usage

1. Set up your environment variables in a `.env` file or in your system's environment variables.
2. Ensure Postgres is configured for queue management
3. Run the main application:
   ```bash
   python main.py
   ```
The application will start monitoring the specified folder and automatically process any new media files or
archives that are added.

### Convenience scripts
There are two convenience scripts that can start this application:
- On Linux: `start.sh`
- On Windows: `start.bat`

Both of those will:
1. Create the virtual environment if it doesn't exist
2. Install the dependencies
3. Start the application

#### Linux
Because of the way bash works, on Linux you need to run the script like this:
```bash
source start.sh
```

or like this:
```bash
. start.sh
```

Otherwise, it will not work as expected.