#!/usr/bin/env bash
set -euo pipefail

demo_root="${HOMEPAGE_DEMO_ROOT:-}"
temp_root="${TMPDIR:-/tmp}"
temp_root="${temp_root%/}"
case "$demo_root" in
  "$temp_root"/omegaflow-quickstart-demo.*)
    cd /
    rm -rf -- "$demo_root"
    ;;
  "") ;;
  *)
    echo "refusing to remove unexpected demo root: $demo_root" >&2
    exit 1
    ;;
esac
