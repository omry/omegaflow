#!/usr/bin/env bash

# OmegaFlow evaluates run_file contents in its persistent capture shell. Keep
# setup in a function so failures return to that shell, allowing the configured
# cleanup step to run. The capture working directory is the repository root.
omegaflow_prepare_quickstart_environment() {
  local repo_root="$PWD"
  local repo_python="$repo_root/.venv/bin/python"
  if [[ ! -x "$repo_python" ]]; then
    echo "repository environment is missing: $repo_python" >&2
    return 1
  fi

  local temp_root="${TMPDIR:-/tmp}"
  temp_root="${temp_root%/}"
  local environment_root
  environment_root="$(mktemp -d "$temp_root/omegaflow-quickstart-env.XXXXXX")" \
    || return
  export HOMEPAGE_DEMO_ENV_ROOT="$environment_root"
  export HOMEPAGE_DEMO_VENV="$HOMEPAGE_DEMO_ENV_ROOT/venv"

  "$repo_python" -m virtualenv --no-download "$HOMEPAGE_DEMO_VENV" \
    || return
  local demo_site
  demo_site="$("$HOMEPAGE_DEMO_VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')" \
    || return

  # Seed only the local build backend and OmegaFlow runtime dependencies. This
  # keeps the demo offline and ensures OmegaFlow itself is absent until the
  # recorded installation command runs.
  "$repo_python" - "$demo_site" <<'PY'
from __future__ import annotations

import importlib.metadata
import shutil
import sys
from pathlib import Path


destination_root = Path(sys.argv[1])
for distribution_name in (
    "hatchling",
    "hydra-core",
    "omegaconf",
    "packaging",
    "pathspec",
    "pluggy",
    "PyYAML",
    "trove-classifiers",
):
    distribution = importlib.metadata.distribution(distribution_name)
    for installed_path in distribution.files or ():
        if ".." in installed_path.parts or installed_path.is_absolute():
            continue
        source = Path(distribution.locate_file(installed_path))
        destination = destination_root / installed_path
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        elif source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
PY
  local seed_status=$?
  if [[ "$seed_status" -ne 0 ]]; then
    return "$seed_status"
  fi

  if "$HOMEPAGE_DEMO_VENV/bin/python" -c 'import omegaflow' 2>/dev/null; then
    echo "OmegaFlow must not be installed before the recorded install step" >&2
    return 1
  fi
}

omegaflow_prepare_quickstart_environment
