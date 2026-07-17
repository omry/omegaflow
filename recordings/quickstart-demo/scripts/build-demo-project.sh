#!/usr/bin/env bash
set -euo pipefail

omegaflow recording=quickstart force=true

test -f recordings/.omegaflow/videos/quickstart/presentation/recording.presentation.json
test -f recordings/.omegaflow/videos/quickstart/index.html
