# OmegaFlow workload Envoy

This Go module owns the workload-side implementation of the internal
[Envoy protocol v1](../../docs/design/envoy-protocol-v1.md).

Slice 1 contains the dependency-free protocol package, its lifecycle state
validator, golden-fixture conformance tests, and a small Go controller harness
under `internal/protocoltest`. The harness is test infrastructure, not a
production controller client. The production Envoy command, PTY/process
supervision, network listeners, and Python controller integration arrive in
later reviewed slices; there is no placeholder executable to mistake for a
working runtime.

Protocol decoders and lifecycle state are deliberately unsynchronized because
one session owner serializes their use. A rejected frame or transition is a
terminal session failure; callers close the session rather than reuse that
decoder or state. When the Envoy sends the private awsh shutdown request, it
must call `MarkShutdownRequested` on the result decoder before treating result
EOF as clean; the request decoder records a decoded shutdown itself.

Run the current checks with Go 1.25 or newer:

```text
go test ./...
go vet ./...
```

The locked production build is Linux-only, has `CGO_ENABLED=0`, trims source
paths and VCS metadata, and clears the linker build ID. Release materialization
will build both `linux/amd64` and `linux/arm64` and record their SHA-256 digests.
