# Split execution integration proof (B5.3)

Run the portable integration tests from `runtime/envoy`:

```sh
go test -buildvcs=false -race ./session -run '^(TestSplitCompletion|TestSplitResize)' -count=1
```

Run the native Bash proof from the repository root:

```sh
python3 tests/envoy-split-completion/run.py --qualification QUALIFICATION --output NEW_DIRECTORY
```

The runner verifies the existing complete qualification receipts and exact Awsh
and startup asset hashes. It uses one session test binary on the three pinned
Linux amd64 Bash builds. Logs retain every raw terminal byte, public marks,
logical stdout and stderr, image identity and test binary hash. The runner
removes rebuildable binaries on success and failure. ARM64 compilation does
not qualify an ARM64 target.

Production Start attaches the real FIFO readers before releasing selected
source execution. The existing single terminal writer retains one global
output offset. Resize closes a finite admitted prefix from every source before
its covering mark, ioctl and acknowledgement. Continuous FIFO output does not
extend that prefix. Paused healthy readers resume after the boundary.

The native cases cover stdout-first, stderr-first, empty output, cancellation
and finalization. They reconstruct exact NUL, invalid UTF-8, CR/LF and multibyte
UTF-8 bytes from public marks over the raw terminal log. Known Readline controls
remain separately attributed to PTY output. The portable tests also cover gate
continuation with resize, sequential split-to-PTY operations, shell exit and
lifecycle timeout, independent EOFs, identity drift, a missing EOF, and failure
before acknowledgement. Existing start tests retain original-epoch rollback.

An explicit deterministic peer supplies successful process cleanup in these
isolated tests. Production completion then closes writer keepalives, observes
both independent EOFs, joins and closes readers, and removes owned FIFO paths
before input_closed. The original cleanup deadline also covers adapter return
and the final marked result. Closing a descriptor alone is not EOF evidence.

B6.3 still owns genuine descendant cleanup, census and writer-retaining process
proofs. B7 owns resolved inspection integration. This slice establishes the
split transport and completion barriers with those peers; it does not close
those later cumulative obligations or add a production entrypoint.
