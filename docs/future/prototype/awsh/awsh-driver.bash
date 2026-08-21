#!/usr/bin/env bash
# Bash-resident half of the awsh prototype. Start through ./awsh.

set +e
shopt -s expand_aliases

readonly AWSH_SCHEMA="awsh-v1"
readonly AWSH_PROTOCOL_ERROR_STATUS=64
readonly AWSH_IO_ERROR_STATUS=74

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

if [[ ! $awsh_request_fd =~ ^[0-9]+$ || ! $awsh_result_fd =~ ^[0-9]+$ ]]; then
  awsh_usage
  exit "$AWSH_PROTOCOL_ERROR_STATUS"
fi
if (( awsh_request_fd < 3 || awsh_result_fd < 3 || awsh_request_fd == awsh_result_fd )); then
  awsh_usage
  exit "$AWSH_PROTOCOL_ERROR_STATUS"
fi

readonly awsh_request_fd awsh_result_fd

# The broker is the only process that retains the Envoy-facing descriptors.
# Bash marks named-coproc pipe descriptors close-on-exec, so driver functions
# can use them while ordinary operation children cannot inherit them.
awsh_broker_read_field() {
  local target=$1
  local descriptor=${2-}
  local awsh_broker_status

  while true; do
    if [[ -n $descriptor ]]; then
      IFS= read -r -d '' -u "$descriptor" "$target"
    else
      IFS= read -r -d '' "$target"
    fi
    awsh_broker_status=$?
    if (( awsh_broker_status == 0 )); then
      return 0
    fi
    if (( awsh_broker_status <= 128 )); then
      return "$awsh_broker_status"
    fi
  done
}

awsh_control_broker() {
  local request_fd=$1
  local result_fd=$2
  local command count field
  local -a fields
  local index

  # The broker shares the terminal foreground process group but does not own
  # terminal interruption. Ignore SIGINT so it cannot terminate the broker or
  # turn the live request descriptor into synthetic EOF. The read helper still
  # retries other interrupted reads.
  trap '' INT

  while true; do
    command=''
    if ! awsh_broker_read_field command; then
      return 0
    fi
    case "$command" in
      read)
        field=''
        if awsh_broker_read_field field "$request_fd"; then
          printf 'field\0%s\0' "$field" || return "$AWSH_IO_ERROR_STATUS"
        else
          printf 'eof\0%s\0' "$field" || return "$AWSH_IO_ERROR_STATUS"
        fi
        ;;
      emit)
        count=''
        if ! awsh_broker_read_field count || [[ ! $count =~ ^[1-9][0-9]*$ ]] || (( count > 16 )); then
          return "$AWSH_PROTOCOL_ERROR_STATUS"
        fi
        fields=()
        for (( index = 0; index < count; index++ )); do
          field=''
          if ! awsh_broker_read_field field; then
            return "$AWSH_PROTOCOL_ERROR_STATUS"
          fi
          fields+=("$field")
        done
        if printf '%s\0' "${fields[@]}" >&"$result_fd"; then
          printf 'ok\0' || return "$AWSH_IO_ERROR_STATUS"
        else
          printf 'io-error\0' || true
          return "$AWSH_IO_ERROR_STATUS"
        fi
        ;;
      close)
        return 0
        ;;
      *)
        return "$AWSH_PROTOCOL_ERROR_STATUS"
        ;;
    esac
  done
}

readonly -f awsh_broker_read_field awsh_control_broker awsh_usage

coproc AWSH_CONTROL {
  awsh_control_broker "$awsh_request_fd" "$awsh_result_fd"
}
awsh_control_read_fd=${AWSH_CONTROL[0]}
awsh_control_write_fd=${AWSH_CONTROL[1]}
awsh_control_pid=$AWSH_CONTROL_PID
readonly awsh_control_read_fd awsh_control_write_fd awsh_control_pid
disown "$awsh_control_pid" 2>/dev/null || true

# The broker now owns these. Closing the originals here is what prevents fixed
# request/result descriptors from reaching operation children.
exec {awsh_request_fd}<&-
exec {awsh_result_fd}>&-

awsh_control_read_field() {
  local target=$1
  IFS= read -r -d '' -u "$awsh_control_read_fd" "$target"
}

awsh_read_field() {
  local target=$1
  local awsh_broker_status value
  if ! printf 'read\0' >&"$awsh_control_write_fd"; then
    exit "$AWSH_IO_ERROR_STATUS"
  fi
  awsh_broker_status=''
  value=''
  if ! awsh_control_read_field awsh_broker_status || ! awsh_control_read_field value; then
    exit "$AWSH_IO_ERROR_STATUS"
  fi
  printf -v "$target" '%s' "$value"
  [[ $awsh_broker_status == field ]]
}

awsh_emit() {
  local field_count=$(( $# + 1 ))
  local awsh_broker_status
  if ! printf '%s\0' emit "$field_count" "$AWSH_SCHEMA" "$@" >&"$awsh_control_write_fd"; then
    exit "$AWSH_IO_ERROR_STATUS"
  fi
  awsh_broker_status=''
  if ! awsh_control_read_field awsh_broker_status || [[ $awsh_broker_status != ok ]]; then
    exit "$AWSH_IO_ERROR_STATUS"
  fi
}

awsh_close_control() {
  printf 'close\0' >&"$awsh_control_write_fd" || true
  exec {awsh_control_write_fd}>&-
  exec {awsh_control_read_fd}<&-
}

awsh_protocol_error() {
  local code=$1
  local message=$2
  awsh_emit protocol_error "$code" "$message"
  exit "$AWSH_PROTOCOL_ERROR_STATUS"
}

awsh_read_request_header() {
  local schema_target=$1
  local kind_target=$2
  local schema kind

  schema=''
  if ! awsh_read_field schema; then
    if [[ -n $schema ]]; then
      awsh_protocol_error truncated-request 'request ended inside its schema field'
    fi
    awsh_protocol_error unexpected-eof 'request stream ended before shutdown'
  fi
  kind=''
  if ! awsh_read_field kind; then
    awsh_protocol_error truncated-request 'request ended before its kind field'
  fi
  if [[ $schema != "$AWSH_SCHEMA" ]]; then
    awsh_protocol_error unsupported-schema "unsupported request schema: $schema"
  fi
  printf -v "$schema_target" '%s' "$schema"
  printf -v "$kind_target" '%s' "$kind"
}

awsh_active_operation_id=''
awsh_cancel_reason=''
declare -A awsh_used_gate_ids=()

awsh_gate() {
  local gate_id=${1-}
  local request_schema request_kind operation_id request_gate_id reason

  if (( $# != 1 )) || [[ ! $gate_id =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
    awsh_protocol_error invalid-gate-id "invalid gate id: $gate_id"
  fi
  if [[ -z $awsh_active_operation_id ]]; then
    awsh_protocol_error gate-outside-operation 'gate requested without an active operation'
  fi
  if [[ -v awsh_used_gate_ids[$gate_id] ]]; then
    awsh_protocol_error reused-gate "gate id was already used: $gate_id"
  fi
  awsh_used_gate_ids[$gate_id]=1

  # A terminal interrupt is translated by the Envoy/controller into the
  # structured cancel request below. Do not let the same SIGINT interrupt the
  # private broker exchange before that request arrives.
  trap '' INT
  awsh_emit gate_ready "$awsh_active_operation_id" "$gate_id"

  awsh_read_request_header request_schema request_kind
  case "$request_kind" in
    continue)
      operation_id=''
      request_gate_id=''
      if ! awsh_read_field operation_id || ! awsh_read_field request_gate_id; then
        awsh_protocol_error truncated-continue 'continue request requires operation and gate ids'
      fi
      if [[ $operation_id != "$awsh_active_operation_id" || $request_gate_id != "$gate_id" ]]; then
        awsh_protocol_error wrong-gate 'continue request does not match the active gate'
      fi
      awsh_emit gate_continued "$awsh_active_operation_id" "$gate_id"
      trap 'return 130' INT
      return 0
      ;;
    cancel)
      operation_id=''
      reason=''
      if ! awsh_read_field operation_id || ! awsh_read_field reason; then
        awsh_protocol_error truncated-cancel 'cancel request requires operation id and reason'
      fi
      if [[ $operation_id != "$awsh_active_operation_id" ]]; then
        awsh_protocol_error wrong-operation 'cancel request does not match the active operation'
      fi
      awsh_cancel_reason=$reason
      trap 'return 130' INT
      return 130
      ;;
    *)
      awsh_protocol_error out-of-state-request "request is invalid while gated: $request_kind"
      ;;
  esac
}

# Operation source is evaluated inside a function so that a top-level `break`
# or `continue` cannot reach the driver's own request loop. Sourced directly
# from the loop, `break` exited the driver without a `completed` result and
# `continue` skipped that result and left the client waiting; inside a function
# each statement instead reports Bash's ordinary "only meaningful in a loop"
# error and the operation fails normally. The body still runs in this shell, so
# cwd and variable state persist across operations as before.
awsh_run_operation() {
  # Clear this function's positional parameters and take the source from a
  # variable instead of an argument. Passing it as `$1` made the sourced
  # workload see `$# == 1` and `$1` equal to its own text, so an operation that
  # inspected `$@` observed an argument nobody passed it. The variable is
  # expanded before the source runs, so sourced code cannot alter what is read.
  set --
  source <(printf '%s' "$awsh_pending_source")
}

# The operation id and source stay in positional parameters for the whole
# operation. Sourced code shares this shell's variable namespace, and Bash's
# dynamic scoping means even a caller's `local` is reachable by name, so an
# operation that assigned `awsh_operation_id` would change the id the completion
# reports and leave the client waiting on an id that never completes. Positional
# parameters cannot be reached by assignment.
awsh_execute_operation() {
  awsh_pending_source=$2
  awsh_active_operation_id=$1
  awsh_cancel_reason=''
  awsh_used_gate_ids=()
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
  # would make the bookkeeping assignment fail and kill the driver. The broker
  # helpers keep their own bookkeeping under `awsh_`-prefixed names for the same
  # reason; the remaining un-prefixed locals in this file are #8's and are noted
  # for its own review.
  if awsh_run_operation; then
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
  awsh_active_operation_id=''
  awsh_emit completed "$1" "$2" "$PWD"
}

readonly -f \
  awsh_close_control \
  awsh_control_read_field \
  awsh_emit \
  awsh_gate \
  awsh_protocol_error \
  awsh_execute_operation \
  awsh_read_field \
  awsh_read_request_header \
  awsh_run_operation

# The driver is the PTY foreground process while idle, so a stray Ctrl-C before
# the first operation must not kill it.
trap '' INT

awsh_emit ready "$$" "$PWD"

while true; do
  awsh_request_schema=''
  awsh_request_kind=''
  awsh_read_request_header awsh_request_schema awsh_request_kind

  case "$awsh_request_kind" in
    execute)
      awsh_operation_id=''
      awsh_operation=''
      if ! awsh_read_field awsh_operation_id || ! awsh_read_field awsh_operation; then
        awsh_protocol_error truncated-execute 'execute request requires id and operation fields'
      fi
      if [[ ! $awsh_operation_id =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
        awsh_protocol_error invalid-operation-id "invalid operation id: $awsh_operation_id"
      fi

      awsh_execute_operation "$awsh_operation_id" "$awsh_operation"
      ;;
    shutdown)
      awsh_emit closed shutdown "$PWD"
      awsh_close_control
      exit 0
      ;;
    continue|cancel)
      awsh_protocol_error out-of-state-request "request is invalid without an active gate: $awsh_request_kind"
      ;;
    *)
      awsh_protocol_error unsupported-request "unsupported request kind: $awsh_request_kind"
      ;;
  esac
done
