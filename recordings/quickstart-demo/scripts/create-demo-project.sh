#!/usr/bin/env bash
set -euo pipefail

# The demo runs from the current checkout, where parent directories may contain
# another OmegaFlow workspace. Create an isolated project in the platform temp
# directory and tell project discovery to stop there. This script is evaluated
# by the recording's persistent shell, so the exported root and cd also apply
# to the following build command.
: "${OMEGAFLOW_TIMELINE:?OMEGAFLOW_TIMELINE is required}"
temp_root="${TMPDIR:-/tmp}"
temp_root="${temp_root%/}"
export OMEGAFLOW_PROJECT_ROOT="$(mktemp -d "$temp_root/omegaflow-quickstart-demo.XXXXXX")"
printf '%s\n' "$OMEGAFLOW_PROJECT_ROOT" > "$OMEGAFLOW_TIMELINE.demo-root"
cd "$OMEGAFLOW_PROJECT_ROOT"

omegaflow action=bootstrap

test -f .omegaflow/config.yaml
test -f recordings/config.yaml
test -f recordings/quickstart/index.md
test -f recordings/quickstart/scripts/hello.sh
