#!/usr/bin/env bash

omegaflow_cleanup_tutorial_environment() {
  if [[ "${TUTORIAL_DESKTOP_PID:-}" =~ ^[0-9]+$ ]]; then
    kill "$TUTORIAL_DESKTOP_PID" 2>/dev/null || true
    wait "$TUTORIAL_DESKTOP_PID" 2>/dev/null || true
  fi
  if [[ -n "${TUTORIAL_DESKTOP_PID_FILE:-}" ]]; then
    rm -f -- "$TUTORIAL_DESKTOP_PID_FILE"
  fi

  local watch_pid_file="$OMEGAFLOW_RUN_DIR/tutorial-watch.pid"
  if [[ -f "$watch_pid_file" ]]; then
    local watch_pid
    watch_pid="$(<"$watch_pid_file")"
    if [[ "$watch_pid" =~ ^[0-9]+$ ]]; then
      kill "$watch_pid" 2>/dev/null || true
      for _attempt in {1..50}; do
        kill -0 "$watch_pid" 2>/dev/null || break
        sleep 0.1
      done
    fi
  fi

  local workspace="${TUTORIAL_WORKSPACE:-}"
  case "$workspace" in
    /tmp/omegaflow-tutorial.*)
      cd / || return
      rm -rf -- "$workspace"
      ;;
    "") ;;
    *)
      echo "refusing to remove unexpected tutorial workspace: $workspace" >&2
      return 1
      ;;
  esac
}

omegaflow_cleanup_tutorial_environment
