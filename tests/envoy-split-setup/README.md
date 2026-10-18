# Split setup and rollback proofs (B5.1)

`SplitResources` creates one operation's exact stdout/stderr FIFOs below
`/run/omegaflow/session/split`. Each private parent is mode 0700; each FIFO is
mode 0600. The owner opens distinct nonblocking readers and private writer
keepalives, with close-on-exec on all descriptors. It uses descriptor-relative
opens without symlink traversal. The existing Coordinator supplies the original
operation-start context and joins setup before rollback.

Rollback closes every acquired descriptor and removes only matching created
entries. A changed identity, close/removal error or expired epoch prevents an
ordinary result. Foreign resources survive. A created entry whose identity
cannot be retained also prevents successful rollback; session teardown remains
necessary. There is no new actor, timer, production path override or entrypoint.

Run the focused real-filesystem and Coordinator tests:

```sh
cd runtime/envoy
go test -buildvcs=false -race ./session -run '^TestSplitResources'
```

Run genuine fixed-path native proofs from the repository root:

```sh
python3 tests/envoy-split-setup/run.py \
  --qualification /absolute/path/to/qualification \
  --output /absolute/path/to/new-evidence
```

The runner reuses the established isolated staging helper. It verifies the
qualified Awsh executable and fixed startup assets, then runs under workload
UID 1000 in each pinned amd64 image. The actual public/private channels and
selected Bash prove source rejection, pre-write cancellation and partial setup
failure, real non-root unlink refusal, and symlink/mode ancestor refusal. Recoverable rejection/cancellation proves FIFO disposal before the
public empty-range result and subsequent PTY shell reuse. Fatal setup failure
claims no ordinary result. Compiled proof executables are removed on success
and failure; logs and executable hashes remain.

`case-map.json` maps the approved B1 inventory to concrete B5.1 proofs. Its
C034 rows are bounded resource/start proofs. Cumulative lifecycle acceptance
remains with B6.3. B5.2 owns stream pumps, byte ranges, output marks and ordering.
B5.3 owns dual EOF, keepalive close, result/resize integration and final drain.
These tests do not prove descendant census or termination, writer-retaining
children, complete split output, inspection, or ARM64 qualification.
