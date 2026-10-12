#!/data/data/com.termux/files/usr/bin/bash
# Self-AI installer for Android phones. Run it inside the Termux app (from F-Droid):
#
#   pkg install -y git && git clone https://github.com/24316bmvlm2038-tech/self-ai && cd self-ai && bash install-android.sh
#
# It installs Python and Ollama, downloads a free AI model that fits this phone, and starts Self-AI.
# Afterwards, just type `selfai` in Termux to start it again.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${PREFIX:-}" != *com.termux* ]]; then
  echo "This installer is for Termux on Android. On a computer, run ./start.sh instead."
  exit 1
fi

echo "==> Installing Python and Ollama (a few minutes on the first run)..."
pkg update -y
if ! pkg install -y python ollama; then
  echo
  echo "Could not install the packages. Run 'termux-change-repo' to pick another mirror,"
  echo "then 'pkg upgrade', and run this installer again."
  exit 1
fi

# Pick the best model this phone can run. Phones report a bit less RAM than advertised.
ram_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
ram_tenths=$(( ram_kb * 10 / 1024 / 1024 ))
if   [ "$ram_tenths" -lt 45 ]; then model="qwen3.5:0.8b"
elif [ "$ram_tenths" -lt 68 ]; then model="qwen3.5:2b"
else model="qwen3.5:4b"; fi
model="${MODEL:-$model}"
echo "==> This phone has about $(( ram_tenths / 10 )).$(( ram_tenths % 10 )) GB RAM, so Self-AI will use $model."

if [ ! -f .env ]; then
  cp .env.example .env
  printf '\nSELFAI_PROVIDER=ollama\nSELFAI_MODEL=%s\n' "$model" >> .env
fi

if ! ollama list >/dev/null 2>&1; then
  echo "==> Starting Ollama..."
  mkdir -p data
  nohup ollama serve > data/ollama.log 2>&1 &
  for _ in $(seq 1 30); do ollama list >/dev/null 2>&1 && break; sleep 1; done
fi

echo "==> Downloading $model (keep Termux open; this uses Wi-Fi data)..."
ollama pull "$model"

# A `selfai` command to start it again later.
cat > "$PREFIX/bin/selfai" <<LAUNCHER
#!/data/data/com.termux/files/usr/bin/bash
exec "$PWD/start.sh" "\$@"
LAUNCHER
chmod +x "$PREFIX/bin/selfai"

echo
echo "==> Installed! Next time, just open Termux and type: selfai"
echo
exec ./start.sh
