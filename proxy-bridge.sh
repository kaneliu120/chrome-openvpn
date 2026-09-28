#!/usr/bin/env bash
# proxy-bridge.sh — Chrome-only ISP-proxy bridge.
#
# Sibling of vpn-bridge.sh. Instead of an OpenVPN tunnel, gost chains the
# local SOCKS5 listener straight into one product's upstream proxy, so the
# exit IP IS the proxy itself. No sudo, no tun, no routing changes.
#
# Usage:
#   ./proxy-bridge.sh [profile-name|path/to/profile.proxy]
#   (drop .proxy files into ./profiles/; run with no args for a menu)
#
# Profile format (profiles/<name>.proxy, gitignored):
#   UPSTREAM=http://user:pass@host:port    (or socks5://…)
#   LABEL=free text, COUNTRY=US            (optional, display only)
#
# Optional:
#   PORT=1080            local SOCKS5 listen port

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-1080}"

GOST_BIN="$(command -v gost || true)"
[[ -z "$GOST_BIN" && -x "$DIR/bin/gost" ]] && GOST_BIN="$DIR/bin/gost"
if [[ -z "$GOST_BIN" ]]; then
  echo "error: gost not found. Install with: brew install gost" >&2
  exit 1
fi

# --- locate the .proxy profile ------------------------------------------
PROFILES=()
while IFS= read -r f; do PROFILES+=("$f"); done \
  < <(ls "$DIR"/profiles/*.proxy 2>/dev/null || true)

CONFIG="${1:-}"
if [[ -n "$CONFIG" && ! -f "$CONFIG" ]]; then
  MATCH=""
  for f in ${PROFILES[@]+"${PROFILES[@]}"}; do
    if [[ "$(basename "$f" .proxy)" == *"$CONFIG"* ]]; then MATCH="$f"; break; fi
  done
  if [[ -z "$MATCH" ]]; then
    echo "error: no profile matching '$CONFIG' in $DIR/profiles/" >&2
    for f in ${PROFILES[@]+"${PROFILES[@]}"}; do
      echo "  - $(basename "$f" .proxy)" >&2
    done
    exit 1
  fi
  CONFIG="$MATCH"
elif [[ -z "$CONFIG" ]]; then
  if [[ ${#PROFILES[@]} -eq 0 ]]; then
    echo "error: no .proxy profile found in $DIR/profiles/" >&2
    echo "  Generate them with: ./gen-isp-profiles.py" >&2
    exit 1
  elif [[ ${#PROFILES[@]} -eq 1 ]]; then
    CONFIG="${PROFILES[0]}"
  else
    echo "Available profiles:"
    i=1
    for f in "${PROFILES[@]}"; do
      up="$(grep -m1 '^UPSTREAM=' "$f" | cut -d= -f2- | sed -E 's|//[^@]*@|//***@|')"
      echo "  $i) $(basename "$f" .proxy)  ($up)"
      i=$((i + 1))
    done
    read -rp "Choose profile [1-${#PROFILES[@]}]: " choice
    if ! [[ "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#PROFILES[@]} )); then
      echo "error: invalid choice" >&2
      exit 1
    fi
    CONFIG="${PROFILES[$((choice - 1))]}"
  fi
fi

UPSTREAM="$(grep -m1 '^UPSTREAM=' "$CONFIG" | cut -d= -f2- | tr -d '[:space:]')"
LABEL="$(grep -m1 '^LABEL=' "$CONFIG" | cut -d= -f2- || true)"
if [[ ! "$UPSTREAM" =~ ^(https?|socks5)://.+@.+:[0-9]+$ ]]; then
  echo "error: $CONFIG has no valid UPSTREAM= line" >&2
  exit 1
fi
echo "==> Profile: $(basename "$CONFIG" .proxy)${LABEL:+  — $LABEL}"
echo "    Upstream: $(echo "$UPSTREAM" | sed -E 's|//[^@]*@|//***@|')"

# --- make sure the proxy port is free -----------------------------------
STALE_PID="$(lsof -ti "tcp:${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$STALE_PID" ]]; then
  if ps -o comm= -p "$STALE_PID" 2>/dev/null | grep -qi gost; then
    echo "==> Cleaning up stale proxy from a previous session (pid $STALE_PID)"
    kill "$STALE_PID" 2>/dev/null || true
    sleep 1
    if lsof -ti "tcp:${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
      kill -9 "$STALE_PID" 2>/dev/null || true; sleep 1
    fi
  else
    echo "error: port $PORT is already in use by another process:" >&2
    lsof -i "tcp:${PORT}" -sTCP:LISTEN >&2
    echo "Stop it, or run with a different port: PORT=1081 $0" >&2
    exit 1
  fi
fi

cleanup() {
  echo; echo "==> Shutting down..."
  [[ -n "${GOST_PID:-}" ]] && kill "$GOST_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM HUP

# Hostname resolution happens at the upstream proxy (CONNECT/SOCKS5 carry
# the domain), so no local resolver is needed — same as a real ISP user.
"$GOST_BIN" -L "socks5://127.0.0.1:${PORT}" -F "$UPSTREAM" &
GOST_PID=$!
sleep 1
if ! kill -0 "$GOST_PID" 2>/dev/null; then
  echo "error: gost exited on startup" >&2
  exit 1
fi

echo "==> SOCKS5 on 127.0.0.1:$PORT → upstream proxy. One-shot exit check:"
EXIT_IP="$(curl -s --max-time 15 --socks5-hostname "127.0.0.1:${PORT}" https://api.ipify.org 2>/dev/null || true)"
if [[ -n "$EXIT_IP" ]]; then
  echo "    exit IP: $EXIT_IP"
else
  echo "    (check failed — proxy may be down or credentials wrong; the listener is still up)"
fi
echo
echo "    Toggle the 'OpenVPN Bridge' extension ON in Chrome (port $PORT)."
echo "    Ctrl-C to stop."
wait "$GOST_PID"
