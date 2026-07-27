#!/bin/bash

# Ensure we are in the project root
cd "$(dirname "$0")"

echo "🚀 Starting Persona local environment..."

# Resolve Python: prefer venv > uv run > system python3
VENV_PYTHON=".venv/bin/python"
if [ -x "$VENV_PYTHON" ]; then
    "$VENV_PYTHON" scripts/dev.py
elif command -v uv &> /dev/null; then
    uv run scripts/dev.py
else
    python3 scripts/dev.py
fi
