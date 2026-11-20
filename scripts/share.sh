#!/usr/bin/env bash
# Let other people play from this computer, with no domain and no cloud account.
#
#   ./scripts/share.sh
#
# Builds the web app, starts the game on its own port and opens a secure public link to it
# with ngrok (https://ngrok.com – free). Share the printed link; stop everything with Ctrl-C.
# The computer has to stay on while people play, so it is kept awake for as long as this runs.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8200}"
command -v ngrok >/dev/null || { echo "ngrok is not installed. Get it from https://ngrok.com/download"; exit 1; }
[ -x backend/.venv/bin/python ] || { echo "Set the backend up first (see README: Run it on your computer)."; exit 1; }

echo "Building the web app…"
(cd web && npm run build >/dev/null)

LOG="$(mktemp -t read-it-or-weep-ngrok)"
ngrok http "$PORT" --log=stdout >"$LOG" 2>&1 &
NGROK=$!
SERVER=""
trap 'kill $NGROK $SERVER 2>/dev/null || true' EXIT

URL=""
for _ in $(seq 1 40); do
  URL="$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c '
import json, sys
try:
    tunnels = json.load(sys.stdin)["tunnels"]
    print(next((t["public_url"] for t in tunnels if t["public_url"].startswith("https")), ""))
except Exception:
    print("")' || true)"
  [ -n "$URL" ] && break
  sleep 0.5
done
[ -n "$URL" ] || { echo "ngrok did not give a link. Its log: $LOG"; exit 1; }

echo
echo "  Your link (works on any phone, anywhere):"
echo "    Admin:    $URL/admin"
echo "    Players:  open a quiz in the admin and copy its play link"
echo
echo "  Leave this window open. Ctrl-C stops the game and closes the link."
echo

cd backend
KEEP_AWAKE=""
command -v caffeinate >/dev/null && KEEP_AWAKE="caffeinate -i"
PUBLIC_BASE_URL="$URL" WEB_BASE_URL="$URL" CORS_ORIGINS="" \
  $KEEP_AWAKE .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port "$PORT" \
  --proxy-headers --forwarded-allow-ips=127.0.0.1 &
SERVER=$!
wait $SERVER
