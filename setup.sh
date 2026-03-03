#!/bin/bash
# AI Daily Digest - First Time Setup
# Run once: bash setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "AI Daily Digest - Setup"
echo "======================="

# Create venv
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

# Activate and install
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Create directories
mkdir -p output cache credentials

# Check for API key
if [ -z "$ANTHROPIC_API_KEY" ] && [ ! -f .env ]; then
    echo ""
    echo "IMPORTANT: Set your Anthropic API key:"
    echo "  echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env"
    echo ""
fi

echo ""
echo "Setup complete. Run: bash run.sh"
