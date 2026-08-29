#!/bin/bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

uv sync
uv run python main.py
