#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
demo_dir=/tmp/omegaflow-quickstart-demo

rm -rf "$demo_dir"
mkdir -p "$demo_dir"
cd "$demo_dir"

"$repo_root/.venv/bin/omegaflow" action=bootstrap
