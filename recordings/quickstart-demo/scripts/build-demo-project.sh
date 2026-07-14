#!/usr/bin/env bash
set -euo pipefail

omegaflow recording=quickstart force=true

test -f recordings/.omegaflow/videos/quickstart/presentation/recording.presentation.json
test -f recordings/.omegaflow/videos/quickstart/index.html

preview_dir="$HOMEPAGE_PLAYER_ROOT/quickstart"
mkdir -m 700 "$preview_dir" "$preview_dir/presentation"
cp -R recordings/.omegaflow/videos/quickstart/presentation/. \
  "$preview_dir/presentation/"
