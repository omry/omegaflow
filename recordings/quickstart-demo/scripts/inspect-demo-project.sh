#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd /tmp/omegaflow-quickstart-demo
"$repo_root/.venv/bin/omegaflow" recording=quickstart action=build dry_run=true
