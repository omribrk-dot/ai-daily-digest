#!/bin/bash
# AI Daily Digest - Run
# Usage: bash run.sh [--dry-run] [--no-open] [--verbose] [--discover-senders]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Auto-setup on first run
if [ ! -d ".venv" ]; then
    echo "First run detected. Running setup..."
    bash setup.sh
fi

source .venv/bin/activate

# Load .env if exists
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Run digest
python3 digest.py "$@"
