#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd /tmp/omegaflow-hello
"$repo_root/.venv/bin/studio" recording=hello action=play
