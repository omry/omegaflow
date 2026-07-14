#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${HOMEPAGE_PLAYER_SERVER_PID:-}" ]]; then
  kill "$HOMEPAGE_PLAYER_SERVER_PID" 2>/dev/null || true
  wait "$HOMEPAGE_PLAYER_SERVER_PID" 2>/dev/null || true
fi

: "${TMPDIR:?TMPDIR is required}"
server_root="${HOMEPAGE_PLAYER_ROOT:-}"
case "$server_root" in
  "$TMPDIR"/omegaflow-homepage-player.*)
    rm -rf -- "$server_root"
    rm -f -- "${server_root}.log"
    ;;
  "") ;;
  *)
    echo "refusing to remove unexpected demo server root: $server_root" >&2
    exit 1
    ;;
esac
