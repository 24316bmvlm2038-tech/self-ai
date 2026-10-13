#!/usr/bin/env bash
# Open Self-AI on the web (not just http://localhost) with a free Cloudflare Tunnel.
#
#   Instant:   just press Enter when asked for an address. You get a random link like
#              https://some-words.trycloudflare.com that changes each time Self-AI restarts.
#              No account or domain needed.
#   Permanent: type your own address, like ai.yourname.dpdns.org. Needs a free domain (e.g. from
#              https://domain.digitalplat.org) added to a free Cloudflare account. See README.
#
# Run it in Termux (or on a computer) from the self-ai folder:  bash setup-url.sh
set -euo pipefail
cd "$(dirname "$0")"
termux=false
[[ "${PREFIX:-}" == *com.termux* ]] && termux=true

if ! command -v cloudflared >/dev/null 2>&1; then
  if $termux; then
    echo "==> Installing cloudflared..."
    pkg install -y cloudflared
  else
    echo "Please install cloudflared first: https://developers.cloudflare.com/tunnel/downloads/"
    exit 1
  fi
fi

[ -d .venv ] || { echo "Run ./start.sh once first."; exit 1; }
# shellcheck disable=SC1091
. .venv/bin/activate
setting() { python -c "from selfai.config import Settings; print(getattr(Settings.from_env(), '$1'))"; }
set_env() {  # set_env NAME VALUE: replace any earlier value in .env
  { grep -v "^$1=" .env || true; } > .env.tmp && mv .env.tmp .env
  printf '\n%s=%s\n' "$1" "$2" >> .env
}

# Anyone with the link can open it, so Self-AI must have a password.
if [ -z "$(setting password)" ]; then
  echo
  echo "Choose a password for the website (you'll type it once per browser; this phone never asks)."
  while true; do
    read -r -s -p "Password: " pw; echo
    read -r -s -p "Same password again: " pw2; echo
    if [ "${#pw}" -lt 8 ]; then echo "Use at least 8 characters."; continue; fi
    [ "$pw" = "$pw2" ] && break
    echo "They didn't match, try again."
  done
  set_env SELFAI_PASSWORD "$pw"
fi

echo
echo "Press Enter for an instant link (it changes each time Self-AI restarts),"
read -r -p "or type your own address (for example ai.yourname.dpdns.org): " host
host="${host#https://}"; host="${host#http://}"; host="${host%%/*}"

if [ -z "$host" ]; then
  set_env SELFAI_PUBLIC_URL quick
  echo
  echo "==> Done! Restart Self-AI: press Ctrl+C in the Self-AI window (if open), then type: selfai"
  echo "    Your link is shown in Termux and at the bottom of the app's menu (☰)."
  exit 0
fi

if [[ ! "$host" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
  echo "That doesn't look like a web address."
  exit 1
fi

mkdir -p "$HOME/.cloudflared"
if [ ! -f "$HOME/.cloudflared/cert.pem" ]; then
  echo
  echo "==> A Cloudflare page opens next (or copy the link it prints into your browser)."
  echo "    Sign in, tap your domain, then tap Authorize. Come back here afterwards."
  cloudflared tunnel login
fi

if ! cloudflared tunnel info selfai >/dev/null 2>&1; then
  echo "==> Creating the tunnel..."
  cloudflared tunnel create selfai
fi
tunnel_id=$(cloudflared tunnel list --output json | python -c "
import json, sys
print(next(t['id'] for t in json.load(sys.stdin) if t['name'] == 'selfai'))")

echo "==> Pointing $host at this device..."
cloudflared tunnel route dns --overwrite-dns selfai "$host"

port=$(setting port)
cat > "$HOME/.cloudflared/selfai.yml" <<CONFIG
tunnel: $tunnel_id
credentials-file: $HOME/.cloudflared/$tunnel_id.json
ingress:
  - hostname: $host
    service: http://localhost:$port
  - service: http_status:404
CONFIG
cloudflared tunnel ingress validate --config "$HOME/.cloudflared/selfai.yml" >/dev/null

set_env SELFAI_PUBLIC_URL "https://$host"
echo
echo "==> Done! Self-AI will be at https://$host whenever it's running."
echo "    Restart it now: press Ctrl+C in the Self-AI window (if open), then type: selfai"
