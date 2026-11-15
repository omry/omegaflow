#!/usr/bin/env bash

omegaflow_start_tutorial_watch() {
  local watch_log="$OMEGAFLOW_RUN_DIR/tutorial-watch.log"
  local watch_pid_file="$OMEGAFLOW_RUN_DIR/tutorial-watch.pid"
  local edit_ready_file="$OMEGAFLOW_RUN_DIR/tutorial-watch-edit-ready"
  local stable_pid_file="/tmp/omegaflow-tutorial-watch-43125.pid"
  local tutorial_workspace
  local stale_pid
  local stale_command
  local watch_pid

  tutorial_workspace="$(cat "$OMEGAFLOW_RUN_DIR/tutorial-workspace")" || return 1

  if [[ -f "$stable_pid_file" ]]; then
    stale_pid="$(<"$stable_pid_file")"
    if [[ "$stale_pid" =~ ^[0-9]+$ && -r "/proc/$stale_pid/cmdline" ]]; then
      stale_command="$(tr '\0' ' ' <"/proc/$stale_pid/cmdline")"
      if [[ "$stale_command" == *omegaflow-tutorial-watch-supervisor* ]]; then
        kill "$stale_pid" 2>/dev/null || true
        for _attempt in {1..50}; do
          kill -0 "$stale_pid" 2>/dev/null || break
          sleep 0.1
        done
      fi
    fi
  fi

  cd "$tutorial_workspace" || return 1
  : >"$watch_pid_file" || return 1
  setsid -f bash -c '
  watch_pid_file="$1"
  stable_pid_file="$2"
  watch_pid=""
  timer_pid=""

  stop_watch() {
    trap - EXIT INT TERM
    if [[ "$timer_pid" =~ ^[0-9]+$ ]]; then
      kill "$timer_pid" 2>/dev/null || true
      wait "$timer_pid" 2>/dev/null || true
    fi
    if [[ "$watch_pid" =~ ^[0-9]+$ ]]; then
      kill "$watch_pid" 2>/dev/null || true
      wait "$watch_pid" 2>/dev/null || true
    fi
    if [[ -f "$stable_pid_file" ]] && \
        [[ "$(<"$stable_pid_file")" == "$$" ]]; then
      rm -f -- "$stable_pid_file"
    fi
    exit 0
  }

  trap stop_watch EXIT INT TERM
  exec 3>&- 4>&- 5>&- 6>&- 7>&- 8>&- 9>&-
  omegaflow recording=sunset-beach action=watch open=false autoplay=false \
    watch_port=43125 &
  watch_pid=$!
  printf "%s\n" "$$" >"$watch_pid_file" || exit 1
  printf "%s\n" "$$" >"$stable_pid_file" || exit 1
  sleep 600 &
  timer_pid=$!
  wait -n "$watch_pid" "$timer_pid"
' omegaflow-tutorial-watch-supervisor "$watch_pid_file" "$stable_pid_file" \
    >"$watch_log" 2>&1 </dev/null || return 1

  for _attempt in {1..100}; do
    watch_pid="$(<"$watch_pid_file")"
    if grep -q "serving local watch server" "$watch_log"; then
      : >"$edit_ready_file"
      cat "$watch_log"
      return 0
    fi
    if [[ "$watch_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$watch_pid" 2>/dev/null; then
      cat "$watch_log"
      return 1
    fi
    sleep 0.1
  done

  cat "$watch_log"
  echo "tutorial watch server did not become ready" >&2
  return 1
}

omegaflow_start_tutorial_watch
