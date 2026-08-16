# OmegaFlow workload Envoy

This Go module owns the workload-side implementation of the internal
[Envoy protocol v1](../../docs/design/envoy-protocol-v1.md). It contains the
dependency-free protocol package, the production Linux Envoy command, and Go
test infrastructure. It does not contain the Python controller integration or
Reploy blueprint materialization from later slices.

The packaged `/omegaflow-runtime/bin/envoy` command binds one terminal listener
and one telemetry listener,
accepts one connection on each, closes the listeners, creates one PTY, and
starts the fixed `/omegaflow-runtime/bin/awsh` adapter. Its explicit invocation
surface is:

```text
/omegaflow-runtime/bin/envoy \
  --terminal-listen 0.0.0.0:47001 \
  --telemetry-listen 0.0.0.0:47002 \
  --columns 80 \
  --rows 24
```

The process exits zero only after structured shutdown, final PTY drain,
terminal EOF, final telemetry, and process-group cleanup. Invalid command-line
usage exits 2. Runtime failures use these stable outer failure classes:

| Code | Class |
| ---: | --- |
| 10 | connection |
| 11 | handshake |
| 12 | protocol |
| 13 | PTY |
| 14 | `awsh` |
| 15 | shell |
| 16 | resize |
| 17 | cancellation |
| 18 | drain |
| 19 | cleanup |

Protocol decoders and lifecycle state are deliberately unsynchronized because
one session owner serializes their use. A rejected frame or transition is a
terminal session failure; callers close the session rather than reuse that
decoder or state. When the Envoy sends the private awsh shutdown request, it
must call `MarkShutdownRequested` on the result decoder before treating result
EOF as clean; the request decoder records a decoded shutdown itself.

The default listeners are intended only for Reploy's lease-private workload
network. Do not publish them on an untrusted or shared network; protocol v1
relies on Reploy's endpoint grant and does not add transport authentication.

Run the current checks with Go 1.25 or newer. The integration tests bind
loopback TCP sockets and exercise a real PTY and the reviewed Bash prototype:

```text
go test ./...
go test -race ./...
go vet ./...
```

The locked production build is Linux-only, has `CGO_ENABLED=0`, trims source
paths and VCS metadata, and clears the linker build ID. Platform wheel builds
compile the matching `linux/amd64` or `linux/arm64` Envoy and package it with
`awsh`, the Bash driver, and an exact versioned manifest. The source build
target remains `./cmd/omegaflow-envoy`.

Host OmegaFlow validates the installed manifest, rejects missing, additional,
linked, special, mode-changed, or digest-changed payloads, and copies the exact
installed files into a fresh non-writable tree. Reploy receives that tree only
as a read-only executable bind at `/omegaflow-runtime`; it does not receive the
source checkout and the workload does not need Python or OmegaFlow installed.
