#!/bin/bash

# Ensure we are in the project root
cd "$(dirname "$0")"

echo "🚀 Starting social-automation local environment..."

# Use uv run to execute the dev script if uv is available, otherwise use python3
if command -v uv &> /dev/null; then
    uv run scripts/dev.py
else
    python3 scripts/dev.py
fi
