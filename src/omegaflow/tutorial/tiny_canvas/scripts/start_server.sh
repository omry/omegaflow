#!/usr/bin/env bash

omegaflow_start_tiny_canvas() {
  export TINY_CANVAS_LOG="$OMEGAFLOW_RUN_DIR/tiny-canvas.log"
  export TINY_CANVAS_PID_FILE="recordings/.omegaflow/tutorial/sunset-beach/tiny-canvas.pid"
  local stale_pid
  local stale_command

  if [[ -f "$TINY_CANVAS_PID_FILE" ]]; then
    stale_pid="$(<"$TINY_CANVAS_PID_FILE")"
    if [[ "$stale_pid" =~ ^[0-9]+$ && -r "/proc/$stale_pid/cmdline" ]]; then
      stale_command="$(tr '\0' ' ' <"/proc/$stale_pid/cmdline")"
      if [[ "$stale_command" == *"$PWD/recordings/sunset-beach/app/server.py --port 18476"* ]]; then
        kill "$stale_pid" 2>/dev/null || true
        for _attempt in {1..10}; do
          kill -0 "$stale_pid" 2>/dev/null || break
          sleep 0.1
        done
      fi
    fi
  fi

  python recordings/sunset-beach/app/server.py --port 18476 \
    >"$TINY_CANVAS_LOG" 2>&1 &
  export TINY_CANVAS_PID=$!
  printf '%s\n' "$TINY_CANVAS_PID" >"$TINY_CANVAS_PID_FILE" || return 1
  for _attempt in {1..20}; do
    grep -q "Tiny Canvas ready" "$TINY_CANVAS_LOG" && return 0
    sleep 0.1
  done
  cat "$TINY_CANVAS_LOG" >&2
  return 1
}

omegaflow_start_tiny_canvas
