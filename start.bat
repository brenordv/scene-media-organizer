@echo off
setlocal enabledelayedexpansion

echo Starting application setup...

REM 1. Check if virtual environment exists, create if not
if not exist ".venv" (
    echo Virtual environment not found. Creating venv...
    python -m venv .venv
    if !errorlevel! equ 0 (
        echo Virtual environment created successfully.
    ) else (
        echo Failed to create virtual environment.
        exit /b 1
    )
) else (
    echo Virtual environment already exists.
)

REM 2. Activate the virtual environment
echo Activating virtual environment...
call .venv\Scripts\activate.bat

REM Check if activation was successful by looking for the activate script
if exist ".venv\Scripts\activate.bat" (
    echo Virtual environment activated: %CD%\.venv
) else (
    echo Failed to activate virtual environment.
    exit /b 1
)

REM 3. Install requirements
echo Installing requirements...
if exist "requirements.txt" (
    pip install -r requirements.txt
    if !errorlevel! equ 0 (
        echo Requirements installed successfully.
    ) else (
        echo Failed to install requirements.
        exit /b 1
    )
) else (
    echo No requirements.txt found. Skipping package installation.
)

REM 4. Run the main application
echo Starting the application...
if exist "main.py" (
    python main.py
) else (
    echo main.py not found in the project root.
    exit /b 1
)