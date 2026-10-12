#!/usr/bin/env bash
# One-command launcher (computers and Android/Termux): sets up a virtual environment,
# installs dependencies, starts Ollama if needed, and starts Self-AI.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python
termux=false
[[ "${PREFIX:-}" == *com.termux* ]] && termux=true

if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  echo "Installing dependencies..."
  pip install --quiet -r requirements.txt
  touch .venv/.installed
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Self-AI can run a free AI model on this device, or use the DeepSeek API (paid, faster)."
  read -r -p "Paste a DeepSeek API key, or just press Enter to use a free local model: " key
  if [ -n "$key" ]; then
    printf '\nSELFAI_PROVIDER=deepseek\nDEEPSEEK_API_KEY=%s\n' "$key" >> .env
  else
    printf '\nSELFAI_PROVIDER=ollama\n' >> .env
  fi
fi

# Make sure Ollama is running when using local models.
read -r provider ollama_url < <(python -c "from selfai.config import Settings; s = Settings.from_env(); print(s.provider, s.ollama_url)")
ollama_up() { python -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1] + '/api/version', timeout=2)" "$ollama_url" >/dev/null 2>&1; }
if [ "$provider" = "ollama" ] && ! ollama_up; then
  if [[ "$ollama_url" =~ ^http://(127\.0\.0\.1|localhost)(:|/|$) ]] && command -v ollama >/dev/null 2>&1; then
    echo "Starting Ollama..."
    mkdir -p data
    nohup ollama serve > data/ollama.log 2>&1 &
    for _ in $(seq 1 30); do ollama_up && break; sleep 1; done
  else
    echo
    if $termux; then
      echo "Ollama is not installed. Run: bash install-android.sh"
    else
      echo "Ollama is not running. Install it from https://ollama.com/download and start it,"
      echo "then download a model in the Models panel."
    fi
    echo
  fi
fi

port=$(python -c "from selfai.config import Settings; print(Settings.from_env().port)")
if $termux; then
  # Keep running (and finishing background tasks) while the screen is off.
  command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
  command -v termux-open-url >/dev/null 2>&1 && (sleep 4; termux-open-url "http://localhost:$port") &
fi

exec python -m selfai "$@"
