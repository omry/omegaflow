#!/usr/bin/env bash
set -euo pipefail

: "${TMPDIR:?TMPDIR is required}"

export HOMEPAGE_PLAYER_ROOT="$(mktemp -d "$TMPDIR/omegaflow-homepage-player.XXXXXX")"
server_log="${HOMEPAGE_PLAYER_ROOT}.log"
cp website/static/cast-player.html "$HOMEPAGE_PLAYER_ROOT/cast-player.html"
cp website/static/cast-player-core.js "$HOMEPAGE_PLAYER_ROOT/cast-player-core.js"

python -m http.server 18474 \
  --bind 127.0.0.1 \
  --directory "$HOMEPAGE_PLAYER_ROOT" \
  >"$server_log" 2>&1 &
export HOMEPAGE_PLAYER_SERVER_PID=$!

ready=false
for _attempt in 1 2 3 4 5 6 7 8 9 10; do
  if python -c 'import socket; sock = socket.create_connection(("127.0.0.1", 18474), 0.2); sock.close()' 2>/dev/null; then
    ready=true
    break
  fi
  sleep 0.1
done

kill -0 "$HOMEPAGE_PLAYER_SERVER_PID"
test "$ready" = true
