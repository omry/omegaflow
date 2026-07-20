#!/usr/bin/env bash

# Like setup, cleanup is evaluated in OmegaFlow's persistent capture shell.
# Return failures without enabling shell-wide options or exiting that shell.
omegaflow_cleanup_quickstart_environment() {
  local demo_root="${HOMEPAGE_DEMO_ROOT:-}"
  local demo_env_root="${HOMEPAGE_DEMO_ENV_ROOT:-}"
  local temp_root="${TMPDIR:-/tmp}"
  temp_root="${temp_root%/}"
  case "$demo_root" in
    "$temp_root"/omegaflow-quickstart-demo.*)
      cd / || return
      rm -rf -- "$demo_root" || return
      ;;
    "") ;;
    *)
      echo "refusing to remove unexpected demo root: $demo_root" >&2
      return 1
      ;;
  esac

  case "$demo_env_root" in
    "$temp_root"/omegaflow-quickstart-env.*)
      rm -rf -- "$demo_env_root" || return
      ;;
    "") ;;
    *)
      echo "refusing to remove unexpected demo environment: $demo_env_root" >&2
      return 1
      ;;
  esac
}

omegaflow_cleanup_quickstart_environment
