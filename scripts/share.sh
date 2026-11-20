#!/usr/bin/env bash
# Let other people play from this computer, with no domain and no cloud account.
#
#   ./scripts/share.sh           build the game, start it and open a public link to it
#   ./scripts/share.sh update    put your latest changes behind the link that is already open
#   ./scripts/share.sh status    is a link open, and what is its address?
#   ./scripts/share.sh stop      close the link (the same as Ctrl-C in its window)
#
# The link is made with ngrok (https://ngrok.com – free). Share the printed address. The computer
# has to stay on while people play, so it is kept awake for as long as the link is open.
#
# The address stays the same for as long as the link stays open: `update` swaps the game behind
# it without closing it, and anyone in the middle of a game carries on after a pause of a few
# seconds. Closing the link and opening it again gives a new address – unless it has a fixed one.
# ngrok gives a free account one fixed address (https://dashboard.ngrok.com/domains): put it in
# backend/.env as NGROK_DOMAIN=your-name.ngrok-free.app and the link is the same every time.
#
# Another port:  PORT=8300 ./scripts/share.sh   (use the same PORT= with update, status and stop)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"
ME="./scripts/share.sh"

PORT="${PORT:-8200}"
STATE="$ROOT/backend/var/share/$PORT"   # this link's own things: its copy of the web app, and notes for `update`
PY="$ROOT/backend/.venv/bin/python"
HEALTH="http://127.0.0.1:$PORT/api/health"

DOMAIN="${NGROK_DOMAIN:-}"
if [ -z "$DOMAIN" ] && [ -f backend/.env ]; then
  DOMAIN="$(sed -n 's/^[[:space:]]*NGROK_DOMAIN[[:space:]]*=[[:space:]]*//p' backend/.env | tail -n 1 | tr -d "\"'[:space:]")"
fi
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN%/}"

URL=""      # the public address
NGROK=""    # the program holding the link open
SERVER=""   # the game itself
AWAKE=""    # keeps the computer from dozing off while the link is open
ROUND=0     # how many times the game has been started behind this link
FAILS=0     # how many times in a row it would not start

say() { printf '%s\n' "$*"; }
die() { printf '%s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------ looking around

# The script that is holding a link open on this port, if there is one.
link_pid() {
  local pid
  pid="$(cat "$STATE/pid" 2>/dev/null)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o command= 2>/dev/null | grep -Eq 'share[^ /]*\.sh' || return 1
  printf '%s\n' "$pid"
}

healthy() { [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$HEALTH" 2>/dev/null)" = "200" ]; }

listening_on() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u; }

# Is this process a copy of this very game? (Another project may also run "uvicorn main:app".)
our_game() {
  case "$(ps -p "$1" -o command= 2>/dev/null)" in
    *"uvicorn main:app"*) [ "$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)" = "$ROOT/backend" ] ;;
    *) return 1 ;;
  esac
}

# Somebody else's program on our port, if there is one.
stranger_on_port() {
  local pid
  for pid in $(listening_on "$PORT"); do
    our_game "$pid" || { ps -p "$pid" -o command= 2>/dev/null | cut -c1-70; return 0; }
  done
  return 1
}

# An old copy of the game still on our port (its link was closed without tidying up) is stopped.
clear_port() {
  local pid tries=0
  for pid in $(listening_on "$PORT"); do
    our_game "$pid" && kill "$pid" 2>/dev/null
  done
  while [ -n "$(listening_on "$PORT")" ] && [ "$tries" -lt 50 ]; do
    sleep 0.2
    tries=$((tries + 1))
  done
  for pid in $(listening_on "$PORT"); do
    our_game "$pid" && kill -9 "$pid" 2>/dev/null
  done
  return 0
}

# "<port of ngrok's own status page> <public address>" for an ngrok link that leads to our game.
find_tunnel() {
  local api
  for api in 4040 4041 4042 4043 4044; do
    curl -s --max-time 2 "http://127.0.0.1:$api/api/tunnels" 2>/dev/null | "$PY" -c '
import json, sys
port, api = sys.argv[1], sys.argv[2]
try:
    tunnels = json.load(sys.stdin)["tunnels"]
except Exception:
    sys.exit(1)
for tunnel in tunnels:
    target = str(tunnel.get("config", {}).get("addr", "")).rstrip("/")
    if str(tunnel.get("public_url", "")).startswith("https://") and target.rsplit(":", 1)[-1] == port:
        print(api, tunnel["public_url"])
        sys.exit(0)
sys.exit(1)' "$PORT" "$api" && return 0
  done
  return 1
}

# ------------------------------------------------------------------ the new version, made ready

# Builds the web app into a folder of this link's own, so the game that is running is never
# handed half-built pages. Nothing is swapped here.
build_web() {
  rm -rf "$STATE/web.next"
  if ! (cd "$ROOT/web" && npm run --silent typecheck && npx vite build --outDir "$STATE/web.next" --emptyOutDir) >"$STATE/build.log" 2>&1 \
      || [ ! -f "$STATE/web.next/index.html" ]; then
    say "The web app did not build, so nothing was changed. What went wrong:"
    tail -n 15 "$STATE/build.log" | sed 's/^/    /'
    rm -rf "$STATE/web.next"
    return 1
  fi
}

# A mistake that stops the game's own code from loading is caught here, before anything is swapped.
check_backend() {
  if ! (cd "$ROOT/backend" && "$PY" -c "import main") >"$STATE/backend.log" 2>&1; then
    say "The game's own code does not load, so nothing was changed. What went wrong:"
    tail -n 12 "$STATE/backend.log" | sed 's/^/    /'
    rm -rf "$STATE/web.next"
    return 1
  fi
}

# Only ever called while no game is running.
swap_web() {
  [ -d "$STATE/web.next" ] || return 0
  rm -rf "$STATE/web.old"
  [ -d "$STATE/web" ] && mv "$STATE/web" "$STATE/web.old"
  mv "$STATE/web.next" "$STATE/web"
  rm -rf "$STATE/web.old"
}

# ------------------------------------------------------------------ the game

start_game() {
  local tries=0
  (cd "$ROOT/backend" && PUBLIC_BASE_URL="$URL" WEB_BASE_URL="$URL" CORS_ORIGINS="" WEB_DIST_DIR="$STATE/web" \
    exec "$PY" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" --proxy-headers --forwarded-allow-ips=127.0.0.1) &
  SERVER=$!
  while [ "$tries" -lt 150 ]; do    # up to half a minute
    if ! kill -0 "$SERVER" 2>/dev/null; then
      wait "$SERVER" 2>/dev/null
      SERVER=""
      return 1
    fi
    if healthy; then
      ROUND=$((ROUND + 1))
      printf '%s %s\n' "$ROUND" "$SERVER" >"$STATE/server"
      return 0
    fi
    sleep 0.2
    tries=$((tries + 1))
  done
  return 1
}

stop_game() {
  local tries=0
  [ -n "$SERVER" ] || return 0
  kill "$SERVER" 2>/dev/null
  while kill -0 "$SERVER" 2>/dev/null && [ "$tries" -lt 75 ]; do    # 15 seconds to finish what it is doing
    sleep 0.2
    tries=$((tries + 1))
  done
  kill -9 "$SERVER" 2>/dev/null
  wait "$SERVER" 2>/dev/null
  SERVER=""
}

# ------------------------------------------------------------------ the link

open_link() {
  local found="" tries=0
  if found="$(find_tunnel)"; then
    URL="${found#* }"
    NGROK="$(listening_on "${found%% *}" | head -n 1)"
    [ -n "$NGROK" ] || NGROK="$(pgrep -f "ngrok http $PORT" 2>/dev/null | head -n 1)"
    say "A link to this game is already open – keeping its address."
    return 0
  fi
  if [ -n "$DOMAIN" ]; then
    ngrok http "$PORT" --url "https://$DOMAIN" --log=stdout >"$STATE/ngrok.log" 2>&1 &
  else
    ngrok http "$PORT" --log=stdout >"$STATE/ngrok.log" 2>&1 &
  fi
  NGROK=$!
  while [ "$tries" -lt 40 ]; do
    found="$(find_tunnel)" && break
    kill -0 "$NGROK" 2>/dev/null || break
    sleep 0.5
    tries=$((tries + 1))
  done
  if [ -z "$found" ]; then
    say "ngrok did not give a link. What it said:"
    grep -m 3 -E "^ERROR:|ERR_NGROK" "$STATE/ngrok.log" | cut -c1-300 | sed 's/^/    /'
    say "  (all of it: $STATE/ngrok.log)"
    kill "$NGROK" 2>/dev/null
    NGROK=""
    return 1
  fi
  URL="${found#* }"
}

link_is_up() {
  if [ -n "$NGROK" ]; then kill -0 "$NGROK" 2>/dev/null; else find_tunnel >/dev/null; fi
}

# ngrok itself stopped (it reconnects on its own after a network hiccup; this is for when it is gone).
reopen_link() {
  local was="$URL"
  say
  say "The link dropped. Opening it again…"
  NGROK=""
  if ! open_link; then
    say "It would not open. Trying again shortly."
    return 1
  fi
  printf '%s\n' "$URL" >"$STATE/url"
  if [ "$URL" = "$was" ]; then
    say "It is back, at the same address: $URL"
    return 0
  fi
  stop_game    # the game hands out its own address (play links, QR codes), so it has to learn the new one
  start_game
  say
  say "  THE ADDRESS HAS CHANGED. The old one no longer works; send people the new one:"
  say "    Admin:    $URL/admin"
  say
}

close_link() {
  trap - EXIT INT TERM HUP
  exec 2>/dev/null    # the shell's own "Terminated" notes about what is stopped here would only be noise
  stop_game
  [ -n "$NGROK" ] && kill "$NGROK" 2>/dev/null
  [ -n "$AWAKE" ] && kill "$AWAKE" 2>/dev/null
  if [ "$(cat "$STATE/pid" 2>/dev/null)" = "$$" ]; then
    rm -f "$STATE/pid" "$STATE/url" "$STATE/server" "$STATE/request" "$STATE/result"
  fi
  say
  say "The link is closed."
}

# ------------------------------------------------------------------ keeping it going

# An update that `update` has built and asked for.
swap_in() {
  say
  say "Putting the new version in… (anyone in a game carries on after a short pause)"
  stop_game
  swap_web
  if start_game; then
    FAILS=0
    printf 'ok %s\n' "$ROUND" >"$STATE/result"
    say "Updated at $(date '+%H:%M'). The link has not changed: $URL"
  else
    printf 'failed\n' >"$STATE/result"
    say "The new version did not start – the reason is just above. Fix it, then:  $ME update"
  fi
}

watch() {
  local beat=0 pause
  while :; do
    sleep 1
    if [ -f "$STATE/request" ]; then
      rm -f "$STATE/request"
      swap_in
      continue
    fi
    if [ -z "$SERVER" ] || ! kill -0 "$SERVER" 2>/dev/null; then
      if [ -n "$SERVER" ]; then
        wait "$SERVER" 2>/dev/null
        say "The game stopped by itself (code $?). Starting it again…"
        SERVER=""
      fi
      if start_game; then
        FAILS=0
        say "The game is running again. Same link: $URL"
      else
        FAILS=$((FAILS + 1))
        pause=$((FAILS * 3))
        [ "$pause" -gt 30 ] && pause=30
        say "It did not start. Trying again in $pause seconds."
        sleep "$pause"
      fi
      continue
    fi
    beat=$((beat + 1))
    if [ $((beat % 20)) -eq 0 ] && ! link_is_up; then
      reopen_link
    fi
  done
}

# ------------------------------------------------------------------ what you can ask for

open_command() {
  local other stranger
  command -v ngrok >/dev/null 2>&1 || die "ngrok is not installed. Get it from https://ngrok.com/download"
  [ -x "$PY" ] || die "Set the backend up first (see README: Run it on your computer)."
  [ -d "$ROOT/web/node_modules" ] || die "Set the web app up first:  cd web && npm install"
  mkdir -p "$STATE" || die "Cannot write to $STATE"
  other="$(link_pid)"
  if [ -n "$other" ]; then
    say "A link is already open: $(cat "$STATE/url" 2>/dev/null)"
    say "To put your latest changes behind it:  $ME update"
    exit 1
  fi
  if stranger="$(stranger_on_port)"; then
    die "Port $PORT is being used by another program ($stranger). Close that, or use another port:  PORT=8300 $ME"
  fi

  say "Building the web app…"
  build_web || exit 1
  check_backend || exit 1

  printf '%s\n' "$$" >"$STATE/pid"
  rm -f "$STATE/url" "$STATE/server" "$STATE/request" "$STATE/result"
  trap close_link EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP

  open_link || exit 1
  printf '%s\n' "$URL" >"$STATE/url"
  clear_port
  swap_web
  if command -v caffeinate >/dev/null 2>&1; then
    caffeinate -i -w $$ &
    AWAKE=$!
  fi

  say
  say "  Your link (works on any phone, anywhere):"
  say "    Admin:    $URL/admin"
  say "    Players:  open a quiz in the admin and copy its play link"
  say
  say "  Leave this window open. Ctrl-C stops the game and closes the link."
  say "  Changed something? In another window:  $ME update   (same link, nobody is thrown out)"
  say

  if ! start_game; then
    say "The game did not start – the reason is just above."
    exit 1
  fi
  watch
}

update_command() {
  local other result waited=0
  if [ -z "$(link_pid)" ]; then
    if find_tunnel >/dev/null; then
      die "A link is open, but an older version of this script started it, and that one cannot be updated in place. Close it (Ctrl-C in its window) and run $ME again."
    fi
    die "No link is open. Start one with:  $ME"
  fi
  [ -f "$STATE/server" ] || die "The link is still starting. Try again in a moment."
  if ! mkdir "$STATE/updating" 2>/dev/null; then
    other="$(cat "$STATE/updating/pid" 2>/dev/null)"
    if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then
      die "Another update is already on its way."
    fi
    rm -rf "$STATE/updating"
    mkdir "$STATE/updating" || die "Cannot write to $STATE"
  fi
  printf '%s\n' "$$" >"$STATE/updating/pid"
  trap 'rm -rf "$STATE/updating"' EXIT

  say "Building your latest changes…"
  build_web || exit 1
  check_backend || exit 1
  rm -f "$STATE/result"
  : >"$STATE/request"
  say "Swapping it in…"
  while [ ! -f "$STATE/result" ] && [ "$waited" -lt 240 ]; do    # two minutes at most
    [ -n "$(link_pid)" ] || die "The link was closed while it was being updated."
    sleep 0.5
    waited=$((waited + 1))
  done
  result="$(cat "$STATE/result" 2>/dev/null)"
  case "$result" in
    ok*) say "Done. The link has not changed: $(cat "$STATE/url" 2>/dev/null)" ;;
    *) die "The new version did not start, so the game is down until that is fixed. The link's own window shows why." ;;
  esac
}

status_command() {
  local found
  if [ -n "$(link_pid)" ]; then
    say "A link is open: $(cat "$STATE/url" 2>/dev/null)"
    if healthy; then say "The game behind it is answering."; else say "The game behind it is not answering right now."; fi
  elif found="$(find_tunnel)"; then
    say "A link is open (${found#* }), but this script is not looking after it."
  else
    say "No link is open."
  fi
}

stop_command() {
  local pid tries=0
  pid="$(link_pid)"
  [ -n "$pid" ] || die "No link is open."
  kill "$pid" 2>/dev/null
  while kill -0 "$pid" 2>/dev/null && [ "$tries" -lt 150 ]; do
    sleep 0.2
    tries=$((tries + 1))
  done
  kill -0 "$pid" 2>/dev/null && die "It did not close. Press Ctrl-C in its window."
  say "The link is closed."
}

case "${1:-open}" in
  open | start) open_command ;;
  update) update_command ;;
  status) status_command ;;
  stop) stop_command ;;
  help | -h | --help) sed -n '2,/^set /p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' ;;
  *) die "There is no '$1'. Try:  $ME help" ;;
esac
