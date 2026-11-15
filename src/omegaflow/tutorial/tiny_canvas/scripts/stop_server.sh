#!/usr/bin/env bash

omegaflow_stop_tiny_canvas() {
  if [[ "${TINY_CANVAS_PID:-}" =~ ^[0-9]+$ ]]; then
    kill "$TINY_CANVAS_PID" 2>/dev/null || true
    wait "$TINY_CANVAS_PID" 2>/dev/null || true
  fi
  if [[ -n "${TINY_CANVAS_PID_FILE:-}" ]]; then
    rm -f -- "$TINY_CANVAS_PID_FILE"
  fi
}

omegaflow_stop_tiny_canvas
