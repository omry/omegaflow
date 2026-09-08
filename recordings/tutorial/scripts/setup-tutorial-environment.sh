#!/usr/bin/env bash

omegaflow_prepare_tutorial_environment() {
  export TUTORIAL_REPO_ROOT="$PWD"
  export TUTORIAL_REPO_PYTHON="$TUTORIAL_REPO_ROOT/.venv/bin/python"
  if [[ ! -x "$TUTORIAL_REPO_PYTHON" ]]; then
    echo "repository environment is missing: $TUTORIAL_REPO_PYTHON" >&2
    return 1
  fi

  local workspace
  workspace="$(mktemp -d /tmp/omegaflow-tutorial.XXXXXX)" || return
  export TUTORIAL_WORKSPACE="$workspace"
  printf '%s\n' "$workspace" > "$OMEGAFLOW_RUN_DIR/tutorial-workspace" || return

  "$TUTORIAL_REPO_PYTHON" -m virtualenv --no-download \
    "$TUTORIAL_WORKSPACE/.venv" >/dev/null || return
  "$TUTORIAL_REPO_PYTHON" \
    "$TUTORIAL_REPO_ROOT/recordings/tutorial/scripts/seed-tutorial-venv.py" \
    "$TUTORIAL_WORKSPACE/.venv" || return
  "$TUTORIAL_WORKSPACE/.venv/bin/python" -m pip install \
    --disable-pip-version-check --no-build-isolation --no-deps --editable \
    "$TUTORIAL_REPO_ROOT[browser]" >/dev/null || return
  source "$TUTORIAL_WORKSPACE/.venv/bin/activate" || return
  omegaflow project_root="$TUTORIAL_WORKSPACE" bootstrap=project \
    >/dev/null || return

  export TUTORIAL_DESKTOP_LOG="$OMEGAFLOW_RUN_DIR/tutorial-desktop.log"
  export TUTORIAL_DESKTOP_PID_FILE="/tmp/omegaflow-tutorial-desktop-43124.pid"
  local stale_pid
  local stale_command
  if [[ -f "$TUTORIAL_DESKTOP_PID_FILE" ]]; then
    stale_pid="$(<"$TUTORIAL_DESKTOP_PID_FILE")"
    if [[ "$stale_pid" =~ ^[0-9]+$ && -r "/proc/$stale_pid/cmdline" ]]; then
      stale_command="$(tr '\0' ' ' <"/proc/$stale_pid/cmdline")"
      if [[ "$stale_command" == *"http.server 43124"* ]]; then
        kill "$stale_pid" 2>/dev/null || true
      fi
    fi
  fi
  "$TUTORIAL_REPO_PYTHON" -m http.server 43124 --bind 127.0.0.1 \
    --directory "$TUTORIAL_REPO_ROOT/recordings/tutorial/assets" \
    >"$TUTORIAL_DESKTOP_LOG" 2>&1 &
  export TUTORIAL_DESKTOP_PID=$!
  printf '%s\n' "$TUTORIAL_DESKTOP_PID" >"$TUTORIAL_DESKTOP_PID_FILE" || return
  for _attempt in {1..30}; do
    if "$TUTORIAL_REPO_PYTHON" -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:43124/desktop.html", timeout=1).read(1)' \
        >/dev/null 2>&1; then
      cd "$TUTORIAL_WORKSPACE" || return
      return 0
    fi
    sleep 0.1
  done
  cat "$TUTORIAL_DESKTOP_LOG" >&2
  return 1
}

omegaflow_prepare_tutorial_environment
