#!/usr/bin/env bash
# Bash-resident half of the awsh prototype. Start through ./awsh.

set +e
shopt -s expand_aliases

readonly AWSH_SCHEMA="awsh-v1"
readonly AWSH_PROTOCOL_ERROR_STATUS=64
readonly AWSH_IO_ERROR_STATUS=74
readonly AWSH_SOURCE_FD=22

awsh_request_fd=20
awsh_result_fd=21

awsh_usage() {
  printf 'usage: awsh [--request-fd FD] [--result-fd FD]\n' >&2
}

while (( $# > 0 )); do
  case "$1" in
    --request-fd)
      if (( $# < 2 )); then
        awsh_usage
        exit "$AWSH_PROTOCOL_ERROR_STATUS"
      fi
      awsh_request_fd=$2
      shift 2
      ;;
    --result-fd)
      if (( $# < 2 )); then
        awsh_usage
        exit "$AWSH_PROTOCOL_ERROR_STATUS"
      fi
      awsh_result_fd=$2
      shift 2
      ;;
    --help)
      awsh_usage
      exit 0
      ;;
    *)
      awsh_usage
      exit "$AWSH_PROTOCOL_ERROR_STATUS"
      ;;
  esac
done

# Accept one canonical decimal spelling. Arithmetic expressions interpret a
# leading zero as octal while descriptor operations interpret the text as
# decimal, so spellings such as 022 could otherwise evade the reserved-FD check.
if [[ ! $awsh_request_fd =~ ^[1-9][0-9]*$ || ! $awsh_result_fd =~ ^[1-9][0-9]*$ ]]; then
  awsh_usage
  exit "$AWSH_PROTOCOL_ERROR_STATUS"
fi
if ((
  awsh_request_fd < 3 ||
  awsh_result_fd < 3 ||
  awsh_request_fd == awsh_result_fd ||
  awsh_request_fd == AWSH_SOURCE_FD ||
  awsh_result_fd == AWSH_SOURCE_FD
)); then
  awsh_usage
  exit "$AWSH_PROTOCOL_ERROR_STATUS"
fi

readonly awsh_request_fd awsh_result_fd

awsh_emit() {
  if ! builtin printf '%s\0' "$AWSH_SCHEMA" "$@" >&"$awsh_result_fd"; then
    exit "$AWSH_IO_ERROR_STATUS"
  fi
}

awsh_read_field() {
  # Expand the caller's target directly instead of introducing a dynamically
  # scoped helper name that a preceding workload could make readonly.
  IFS= read -r -d '' -u "$awsh_request_fd" "$1"
}

awsh_protocol_error() {
  # Keep protocol-error fields in positional parameters. Workload globals
  # persist across requests and may be readonly, so named locals here could
  # inherit forged values or fail to initialize.
  awsh_emit protocol_error "$1" "$2"
  exit "$AWSH_PROTOCOL_ERROR_STATUS"
}

# Operation source is evaluated inside a function so that a top-level `break`
# or `continue` cannot reach the driver's own request loop. Sourced directly
# from the loop, `break` exited the driver without a `completed` result and
# `continue` skipped that result and left the client waiting; inside a function
# each statement instead reports Bash's ordinary "only meaningful in a loop"
# error and the operation fails normally. The body still runs in this shell, so
# cwd and variable state persist across operations as before.
awsh_run_operation() {
  # Materialize the source on a reserved descriptor before clearing this
  # function's positional parameters. This keeps the source out of the
  # workload's `$@` without parking it in a shell variable that the preceding
  # operation could make readonly.
  exec 22< <(builtin printf '%s' "$1")
  set --
  source /dev/fd/22
  # `source` runs in this function so top-level loop control cannot escape into
  # the request loop. That also makes top-level `declare` and `typeset` local to
  # this frame. Capture exactly those still-live local declarations after the
  # source returns and promote them globally; locals from workload-defined
  # nested functions have already left scope and are not included.
  set -- "$?" "$(builtin local -p)"
  exec 22<&-
  if [[ -n $2 ]]; then
    set -- "$1" "${2/#declare /declare -g }"
    set -- "$1" "${2//$'\n'declare /$'\n'declare -g }"
    builtin eval "$2"
  fi
  return "$1"
}

# The operation id and source stay in positional parameters for the whole
# operation. Sourced code shares this shell's variable namespace, and Bash's
# dynamic scoping means even a caller's `local` is reachable by name, so an
# operation that assigned `awsh_operation_id` would change the id the completion
# reports and leave the client waiting on an id that never completes. Positional
# parameters cannot be reached by assignment.
awsh_execute_operation() {
  awsh_emit started "$1"
  # A caught SIGINT is reset to its default disposition in external commands, so
  # the terminal's foreground-process-group signal reaches the active command
  # without killing this driver, and the evaluating function gives the trap a
  # return boundary that aborts the rest of the operation while preserving state
  # in this Bash process.
  trap 'return 130' INT
  # Testing the call rather than running it bare keeps `errexit` enabled by the
  # operation from exiting this shell before a result is reported, and the
  # status is parked in a positional parameter rather than a local: Bash's
  # dynamic scoping would expose any local to the sourced code, where an
  # ordinary `status=...` would be silently discarded and a `readonly status`
  # would make the bookkeeping assignment fail and kill the driver.
  if awsh_run_operation "$2"; then
    set -- "$1" 0
  else
    set -- "$1" "$?"
  fi
  # Ignore rather than default between operations: while idle the driver is
  # still the PTY foreground process, so a default disposition let a stray
  # Ctrl-C kill it and close the result stream with no structured result. An
  # ignored signal is inherited as ignored, but the caught trap above is
  # re-armed before the next operation runs, so its children still see default.
  trap '' INT
  # Workload shell state persists, including nounset and changes to PWD. Ask
  # Bash for its cwd instead of expanding a variable the workload may unset.
  awsh_emit completed "$1" "$2" "$(builtin pwd -L)"
}

# Parse one request in a short-lived function frame. Workload source can reach
# caller locals through Bash's dynamic scoping and may mark these names
# readonly, but this frame is discarded before the outer loop parses the next
# request. A fresh frame therefore receives writable parser state every time.
awsh_process_request() {
  local awsh_request_schema=''
  local awsh_request_kind=''
  local awsh_operation_id=''
  local awsh_operation=''

  if ! awsh_read_field awsh_request_schema; then
    if [[ -n $awsh_request_schema ]]; then
      awsh_protocol_error truncated_request 'request ended inside its schema field'
    fi
    awsh_emit closed eof "$(builtin pwd -L)"
    exit 0
  fi

  if ! awsh_read_field awsh_request_kind; then
    awsh_protocol_error truncated_request 'request ended before its kind field'
  fi
  if [[ $awsh_request_schema != "$AWSH_SCHEMA" ]]; then
    awsh_protocol_error unsupported_schema "unsupported request schema: $awsh_request_schema"
  fi

  case "$awsh_request_kind" in
    execute)
      if ! awsh_read_field awsh_operation_id || ! awsh_read_field awsh_operation; then
        awsh_protocol_error truncated_execute 'execute request requires id and operation fields'
      fi
      if [[ ! $awsh_operation_id =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
        awsh_protocol_error invalid_operation_id "invalid operation id: $awsh_operation_id"
      fi

      awsh_execute_operation "$awsh_operation_id" "$awsh_operation"
      ;;
    shutdown)
      awsh_emit closed shutdown "$(builtin pwd -L)"
      exit 0
      ;;
    *)
      awsh_protocol_error unsupported_request "unsupported request kind: $awsh_request_kind"
      ;;
  esac
}

readonly -f \
  awsh_emit \
  awsh_execute_operation \
  awsh_process_request \
  awsh_protocol_error \
  awsh_read_field \
  awsh_run_operation \
  awsh_usage

# The driver is the PTY foreground process while idle, so a stray Ctrl-C before
# the first operation must not kill it.
trap '' INT

awsh_emit ready "$$" "$PWD"

while true; do
  awsh_process_request
done
