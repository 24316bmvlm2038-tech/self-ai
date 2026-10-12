#!/usr/bin/env bash
# Give Self-AI a real web address (like https://ai.yourname.dpdns.org) with a free Cloudflare Tunnel.
#
# Before running this you need (both free, see README "Get a web address"):
#   1. a domain, e.g. from https://domain.digitalplat.org
#   2. that domain added to a free Cloudflare account (https://dash.cloudflare.com)
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

echo
read -r -p "Web address to use (for example ai.yourname.dpdns.org): " host
host="${host#https://}"; host="${host#http://}"; host="${host%%/*}"
if [[ ! "$host" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
  echo "That doesn't look like a web address."
  exit 1
fi

# Anyone on the internet can open the address, so Self-AI must have a password.
if [ -z "$(setting password)" ]; then
  echo
  echo "Choose a password for the website (you'll type it once per browser)."
  while true; do
    read -r -s -p "Password: " pw; echo
    read -r -s -p "Same password again: " pw2; echo
    if [ "${#pw}" -lt 8 ]; then echo "Use at least 8 characters."; continue; fi
    [ "$pw" = "$pw2" ] && break
    echo "They didn't match, try again."
  done
  printf '\nSELFAI_PASSWORD=%s\n' "$pw" >> .env
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

{ grep -v '^SELFAI_PUBLIC_URL=' .env || true; } > .env.tmp && mv .env.tmp .env
printf '\nSELFAI_PUBLIC_URL=https://%s\n' "$host" >> .env

echo
echo "==> Done! Self-AI will be at https://$host whenever it's running."
echo "    Restart it now: press Ctrl+C in the Self-AI window (if open), then type: selfai"
