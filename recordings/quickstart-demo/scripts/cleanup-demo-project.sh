#!/usr/bin/env bash
set -euo pipefail

root_file="$OMEGAFLOW_TIMELINE.demo-root"
if [[ ! -f "$root_file" ]]; then
  exit 0
fi

demo_root="$(cat "$root_file")"
temp_root="${TMPDIR:-/tmp}"
temp_root="${temp_root%/}"
case "$demo_root" in
  "$temp_root"/omegaflow-quickstart-demo.*)
    cd /
    rm -rf -- "$demo_root"
    rm -f -- "$root_file"
    ;;
  *)
    echo "refusing to remove unexpected demo root: $demo_root" >&2
    exit 1
    ;;
esac
