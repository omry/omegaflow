#!/usr/bin/env bash
set -euo pipefail

# The demo runs from the current checkout, where parent directories may contain
# another OmegaFlow workspace. Create an isolated project in the platform temp
# directory. This script is evaluated by the recording's persistent shell, so
# the exported root and cd also apply to the following build command.
temp_root="${TMPDIR:-/tmp}"
temp_root="${temp_root%/}"
export HOMEPAGE_DEMO_ROOT="$(mktemp -d "$temp_root/omegaflow-quickstart-demo.XXXXXX")"
cd "$HOMEPAGE_DEMO_ROOT"

omegaflow project_root="$HOMEPAGE_DEMO_ROOT" action=bootstrap

test -f .omegaflow/config.yaml
test -f recordings/config.yaml
test -f recordings/quickstart/index.md
test -f recordings/quickstart/scripts/hello.sh
