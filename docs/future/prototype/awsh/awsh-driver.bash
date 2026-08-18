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

awsh_emit() {
  local awsh_field
  if ! printf '%s\0' "$AWSH_SCHEMA" >&"$awsh_result_fd"; then
    exit "$AWSH_IO_ERROR_STATUS"
  fi
  for awsh_field in "$@"; do
    if ! printf '%s\0' "$awsh_field" >&"$awsh_result_fd"; then
      exit "$AWSH_IO_ERROR_STATUS"
    fi
  done
}

awsh_read_field() {
  local awsh_target=$1
  IFS= read -r -d '' -u "$awsh_request_fd" "$awsh_target"
}

awsh_protocol_error() {
  local awsh_code=$1
  local awsh_message=$2
  awsh_emit protocol_error "$awsh_code" "$awsh_message"
  exit "$AWSH_PROTOCOL_ERROR_STATUS"
}

readonly -f awsh_emit awsh_read_field awsh_protocol_error awsh_usage

awsh_emit ready "$$" "$PWD"

while true; do
  awsh_request_schema=''
  if ! awsh_read_field awsh_request_schema; then
    if [[ -n $awsh_request_schema ]]; then
      awsh_protocol_error truncated_request 'request ended inside its schema field'
    fi
    awsh_emit closed eof "$PWD"
    exit 0
  fi

  awsh_request_kind=''
  if ! awsh_read_field awsh_request_kind; then
    awsh_protocol_error truncated_request 'request ended before its kind field'
  fi
  if [[ $awsh_request_schema != "$AWSH_SCHEMA" ]]; then
    awsh_protocol_error unsupported_schema "unsupported request schema: $awsh_request_schema"
  fi

  case "$awsh_request_kind" in
    execute)
      awsh_operation_id=''
      awsh_operation=''
      if ! awsh_read_field awsh_operation_id || ! awsh_read_field awsh_operation; then
        awsh_protocol_error truncated_execute 'execute request requires id and operation fields'
      fi
      if [[ ! $awsh_operation_id =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
        awsh_protocol_error invalid_operation_id "invalid operation id: $awsh_operation_id"
      fi

      awsh_emit started "$awsh_operation_id"
      # A caught SIGINT is reset to its default disposition in external
      # commands, so the terminal's foreground-process-group signal reaches
      # the active command without killing this driver.  Sourcing gives the
      # trap a return boundary that aborts the rest of the operation while
      # preserving state in this Bash process.
      trap 'return 130' INT
      if source <(printf '%s' "$awsh_operation"); then
        awsh_operation_status=0
      else
        awsh_operation_status=$?
      fi
      trap - INT
      awsh_emit completed "$awsh_operation_id" "$awsh_operation_status" "$PWD"
      ;;
    shutdown)
      awsh_emit closed shutdown "$PWD"
      exit 0
      ;;
    *)
      awsh_protocol_error unsupported_request "unsupported request kind: $awsh_request_kind"
      ;;
  esac
done
