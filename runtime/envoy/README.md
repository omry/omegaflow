# OmegaFlow workload Envoy

This Go module owns the workload-side implementation of the internal
[Envoy protocol v1](../../docs/design/envoy-protocol-v1.md).

Slice 1 contains only the dependency-free protocol package and its shared
golden-fixture conformance tests. The production Envoy command, PTY/process
supervision, and network listeners arrive in later reviewed slices; there is no
placeholder executable to mistake for a working runtime.

Run the current checks with Go 1.25 or newer:

```text
go test ./...
go vet ./...
```

The locked production build is Linux-only, has `CGO_ENABLED=0`, trims source
paths and VCS metadata, and clears the linker build ID. Release materialization
will build both `linux/amd64` and `linux/arm64` and record their SHA-256 digests.
