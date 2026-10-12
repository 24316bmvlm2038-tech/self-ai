#!/usr/bin/env bash
# One-command launcher: sets up a virtual environment, installs dependencies and starts Self-AI.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  echo "Installing dependencies..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  touch .venv/.installed
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Get a DeepSeek API key at https://platform.deepseek.com/api_keys"
  read -r -p "Paste your DeepSeek API key (or press Enter to add it to .env later): " key
  if [ -n "$key" ]; then
    printf '\nDEEPSEEK_API_KEY=%s\n' "$key" >> .env
  fi
fi

exec python -m selfai "$@"
