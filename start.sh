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

read -r port public_url has_password data_dir < <(python -c "
from selfai.config import Settings
s = Settings.from_env()
print(s.port, s.public_url or '-', 'yes' if s.password else 'no', s.data_dir)")
open_app() {
  if $termux && [ -z "${SELFAI_NO_BROWSER:-}" ] && command -v termux-open-url >/dev/null 2>&1; then
    termux-open-url "http://localhost:$port"
  fi
}

if $termux; then
  # Home-screen shortcuts: the Termux:Widget add-on shows ~/.shortcuts as a one-tap "Self-AI"
  # button, and the Termux:Boot add-on runs ~/.termux/boot at phone start-up. Without those
  # add-ons these files are simply unused.
  mkdir -p "$HOME/.shortcuts" "$HOME/.termux/boot"
  printf '#!/data/data/com.termux/files/usr/bin/bash\nexec "%s/start.sh"\n' "$PWD" > "$HOME/.shortcuts/Self-AI"
  printf '#!/data/data/com.termux/files/usr/bin/bash\ntermux-wake-lock\ncd "%s" && mkdir -p data && SELFAI_NO_BROWSER=1 nohup ./start.sh > data/selfai.log 2>&1 &\n' \
    "$PWD" > "$HOME/.termux/boot/self-ai"
  chmod +x "$HOME/.shortcuts/Self-AI" "$HOME/.termux/boot/self-ai"
fi

# Web address (set up with setup-url.sh): keep the Cloudflare tunnel running.
tunnel_config="$HOME/.cloudflared/selfai.yml"
if [ "$public_url" != "-" ] && command -v cloudflared >/dev/null 2>&1; then
  mkdir -p "$data_dir"
  if [ "$has_password" != "yes" ]; then
    echo "Not opening the web address: no password is set. Run: bash setup-url.sh"
  elif ! { [ -f "$data_dir/tunnel.pid" ] && kill -0 "$(cat "$data_dir/tunnel.pid")" 2>/dev/null; }; then
    if [ "$public_url" = "quick" ]; then
      # Instant link: Cloudflare picks a random https://...trycloudflare.com address.
      rm -f "$data_dir/public_url.txt"
      nohup cloudflared tunnel --no-autoupdate --url "http://localhost:$port" > "$data_dir/tunnel.log" 2>&1 &
      echo $! > "$data_dir/tunnel.pid"
      echo "Getting your web link..."
      for _ in $(seq 1 30); do
        link=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$data_dir/tunnel.log" 2>/dev/null | head -n 1 || true)
        [ -n "$link" ] && { echo "$link" > "$data_dir/public_url.txt"; break; }
        sleep 1
      done
      [ -z "${link:-}" ] && echo "No web link yet; see $data_dir/tunnel.log if it doesn't appear in the app."
    elif [ -f "$tunnel_config" ]; then
      nohup cloudflared tunnel --no-autoupdate --config "$tunnel_config" run > "$data_dir/tunnel.log" 2>&1 &
      echo $! > "$data_dir/tunnel.pid"
    fi
  fi
  if [ "$public_url" = "quick" ]; then
    [ -f "$data_dir/public_url.txt" ] && echo "Web address: $(cat "$data_dir/public_url.txt")"
  elif [ -f "$tunnel_config" ]; then
    echo "Web address: $public_url"
  fi
fi

# Already running (e.g. started at boot, or the widget was tapped twice)? Just open the app.
answers() {  # any HTTP answer (even "password required") means Self-AI is running
  python -c "
import sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=2)
except urllib.error.HTTPError:
    pass" "$1" >/dev/null 2>&1
}
if answers "http://127.0.0.1:$port/api/status"; then
  echo "Self-AI is already running at http://localhost:$port"
  open_app
  exit 0
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

if $termux; then
  # Keep running (and finishing background tasks) while the screen is off.
  command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
  (sleep 4; open_app) &

  # Android sometimes kills background processes to free memory. Start Self-AI again
  # when that happens (Ctrl+C still quits; a crash right at startup is not retried).
  trap 'exit 0' INT TERM
  while true; do
    started=$SECONDS
    set +e
    python -m selfai "$@"
    code=$?
    set -e
    if [ "$code" -eq 0 ] || [ $(( SECONDS - started )) -lt 15 ]; then
      exit "$code"
    fi
    echo
    echo "Self-AI was stopped (exit code $code), probably by Android to free memory. Restarting..."
    echo "If this keeps happening, pick a smaller model in the Models panel."
    sleep 2
  done
fi

exec python -m selfai "$@"
