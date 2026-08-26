# OmegaFlow workload Envoy

This Go module owns the workload-side implementation of the internal
[Envoy protocol v1](../../docs/design/envoy-protocol-v1.md), including its
pre-release inspection and external-Awsh-supervisor amendments.

The B1 slice contains only the dependency-free protocol package: the bounded
controller/Envoy telemetry contract with sender-stamped output marks, the
terminal-input barrier, gate interruption, and typed workload inspection
results; the private NUL-framed Envoy-to-external-awsh contract, including
`submit`/`started_ack`, source rejection, every cancel/finalize disposition,
explicit `shell_exit`, the resize transaction, and the shell-ended close
handshake; the doubled-source submission-capsule builder and its envelope
bounds; lexical inspection path resolution; the `directory-v2` framed digest
beside the frozen native `directory` encoding; the normative deadline table;
and fail-closed session lifecycle validation. The golden corpus lives under
`tests/fixtures/envoy-protocol-v1` and freezes one complete conformance
session, every private frame shape, and the resolution and digest cases. The
production Envoy and Awsh commands, PTY and process supervision, network
listeners, and controller integration arrive in later reviewed slices; there
is no placeholder executable to mistake for a working runtime.

Protocol decoders and lifecycle state are deliberately unsynchronized because
one session owner serializes their use. A rejected frame or transition is a
terminal session failure; callers close the session rather than reuse that
decoder or state. Private-stream EOF is never a substitute for a result: the
result decoder's `Finish` requires a decoded `closed` result and the request
decoder's requires a decoded `shutdown`.

Run the current checks with Go 1.25 or newer:

```text
go test ./...
go vet ./...
```

Set `UPDATE_ENVOY_FIXTURES=1` to regenerate the golden corpus after an
approved contract change; accepted fixtures are never silently rewritten to
represent a different contract.

The locked production build is Linux-only, has `CGO_ENABLED=0`, trims source
paths and VCS metadata, and clears the linker build ID. Release materialization
will build both `linux/amd64` and `linux/arm64` and record their SHA-256 digests.
