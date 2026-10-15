#!/usr/bin/env bash

omegaflow_follow_tutorial_watch() {
  local watch_log="$OMEGAFLOW_RUN_DIR/tutorial-watch.log"
  local watch_pid_file="$OMEGAFLOW_RUN_DIR/tutorial-watch.pid"
  local tutorial_workspace
  local watch_pid
  local tail_pid
  local changed_before
  local completed_before
  local failed_before
  local result=1

  tutorial_workspace="$(cat "$OMEGAFLOW_RUN_DIR/tutorial-workspace")" || return 1
  watch_pid="$(cat "$watch_pid_file")" || return 1
  if [[ ! "$watch_pid" =~ ^[0-9]+$ ]] || ! kill -0 "$watch_pid" 2>/dev/null; then
    echo "tutorial watch server is not running" >&2
    return 1
  fi

  changed_before="$(grep -c "recording source changed" "$watch_log" || true)"
  completed_before="$(grep -c "build completed after" "$watch_log" || true)"
  failed_before="$(grep -c "watch rebuild failed" "$watch_log" || true)"

  tail -n 0 -F "$watch_log" &
  tail_pid=$!

  while ! grep -q "equals: Coconut Sunset" \
      "$tutorial_workspace/recordings/sunset-beach/index.md"; do
    if ! kill -0 "$watch_pid" 2>/dev/null; then
      kill "$tail_pid" 2>/dev/null || true
      wait "$tail_pid" 2>/dev/null || true
      return 1
    fi
    sleep 0.1
  done

  while kill -0 "$watch_pid" 2>/dev/null; do
    local changed_now
    local completed_now
    local failed_now
    changed_now="$(grep -c "recording source changed" "$watch_log" || true)"
    completed_now="$(grep -c "build completed after" "$watch_log" || true)"
    failed_now="$(grep -c "watch rebuild failed" "$watch_log" || true)"
    if (( failed_now > failed_before )); then
      break
    fi
    if (( changed_now > changed_before && completed_now > completed_before )); then
      sleep 0.2
      result=0
      break
    fi
    sleep 0.1
  done

  kill "$tail_pid" 2>/dev/null || true
  wait "$tail_pid" 2>/dev/null || true
  return "$result"
}

omegaflow_follow_tutorial_watch
