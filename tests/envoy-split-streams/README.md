# Split stream transport proof (B5.2)

Run the portable isolated transport tests:

```sh
cd runtime/envoy
go test -buildvcs=false -race ./session -run '^TestSplitRelay' -count=1
```

Run actual Bash stream proofs from the repository root:

```sh
python3 tests/envoy-split-streams/run.py --qualification QUALIFICATION --output NEW_DIRECTORY
```

The qualifier must contain genuine complete receipts for the three pinned
Linux amd64 targets. The runner checks exact Awsh and startup asset hashes,
stages the same session test binary in each immutable target, and retains
image identities, binary hashes, commands and native logs. Rebuildable binaries
are removed on success and failure. ARM64 compilation is not qualification.

Each stream uses its own real FIFO and one bounded reader. Both feed the
existing sole terminal writer. Public output marks over the single global raw
offset reconstruct exact logical stdout and stderr. Tests compare binary,
invalid UTF-8, NUL, CR/LF and multibyte UTF-8 without normalization. Only the
known Readline controls in the separate PTY stream receive a diagnostic check;
the raw PTY bytes remain in the native log.

Tests cover stdout-first and stderr-first output, concurrent large streams,
PTY interleaving, empty boundaries, independent EOF facts, reader identity and
close-on-exec isolation, joined disposal, source failure, bounded admission,
interrupted writes and exact attributed suffix recovery. An EOF fact alone does
not constitute an operation result or a cleanup receipt.

The native test uses an explicit test-only attachment of the real setup and
relay primitives. It observes actual source return, then closes the test-owned
writer keepalives and joins the transport. It does not send input_closed or
claim completed split operation semantics. B5.3 owns production start/resize/
completion integration and the cleanup-before-keepalive, dual-EOF/removal/result
barriers. B6 owns real descendant cleanup. The case map preserves these
cumulative owners; no approved corpus file is rewritten here.
