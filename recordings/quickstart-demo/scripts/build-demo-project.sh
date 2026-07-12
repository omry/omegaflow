#!/usr/bin/env bash
set -euo pipefail

omegaflow recording=quickstart force=true

test -f recordings/.omegaflow/videos/quickstart/recording.retimed.cast
test -f recordings/.omegaflow/videos/quickstart/index.html
