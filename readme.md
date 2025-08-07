# Scene Media Organizer (SMO)

A Python-based file monitoring and media organization system that automatically processes video files and archives,
organizing them into structured directories based on media metadata.

## How does it work?
In a nutshell, it monitors a folder, and when a file is added, it identifies and copies it to the appropriate folder.

With a bit more detail:
1. The app listens for filesystem events  to detect new files in the watch folder.
2. When a new file is detected, it is added to a queue.
3. This queue pre-processes the file, figuring out some basic metadata and saving it to the database (just metadata, and filename, not the actual file).
4. In a timed fashion, we pull batches of files to process from the database and work on them.
5. For each file in the batch, we wait for the file to become stable (i.e. not being modified by another process), and then we identify and process it.
6. If the file is an archive, we extract it, making its content part of the next batch.
7. Then we organize that file, copying it to a destination, according to the metadata.
   - Movies: `Movies/Title--Year/` (if the year is available, otherwise it i just `Movies/Title/`)
   - TV Series: `Series/Title/SeasonXX/`

## Requirements

The project requires Python 3.13.1 and the following dependencies:

- `watchdog` - Filesystem monitoring
- `requests` - HTTP requests for metadata APIs
- `psycopg2` - Postgres database connectivity
- `rarfile` - RAR archive extraction
- `py7zr` - 7Z archive extraction
- `pillow` - Image processing
- `python-dotenv` - Environment variable management
- `simple-log-factory` - Logging utilities

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