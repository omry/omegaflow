# B7.1 stable workload inspection

This leaf implements Envoy's filesystem algorithm and proves that actual Awsh
completion state resolves paths into it. Restricted worker execution belongs to
B7.2; complete session/lifecycle integration belongs to B7.3. These tests do not
claim the B8 actual-actor conformance gate.

From the repository root, use a fresh ignored output directory for each run:

```sh
go -C runtime/envoy test -race -count=1 -cover ./inspection ./awsh/launch ./protocol
python3 tests/envoy-inspection/run.py --output temp/b7-inspection-faults
python3 tests/envoy-bash-completion/run.py \
  --acquisition /absolute/verified-acquisition.json \
  --case live-inspection --output temp/b7-inspection-live
```

The acquisition uses the existing launch runner's immutable image and executable
checks. Run all three existing genuine Linux amd64 Bash targets. Each native
log must contain eight `ACTUAL B7.1 INSPECTION` lines and a passing
`TestRealCandidateCompletion/live-inspection` result. This proves 24 completion
transactions, each with eight ordered results. It grants no ARM64 qualification
or production session-worker acceptance. The completion test uses USR2 while
the completion helper is held: final cwd, HOME and OUT differ from their earlier
values. The actual algorithm consumes the resulting lexical plans, including a
symlink followed by `..`, without adding a controller probe command.

The Go tests consume the unchanged approved inspection fixture and execute the
actual native runner's `path_hash` function for compatibility. File digests
agree. The native directory tag remains `directory`; new directory results use
`directory-v2`. An explicit old delimiter ambiguity yields equal native hashes
but distinct new hashes. Sorting covers `a-thing` before `a/child`.

The fault runner uses explicit Go test overlays; production source has no hook,
selector or test environment. It injects ten real mutations after hashing,
before final component validation, or after a file read. It requires bounded
`inspection-unstable` failures with no result array. Four removal controls
remove the snapshot, component-chain or read-level metadata check, or change
the directory framing tag, and must make
the corresponding test fail. A separate readlink-error overlay proves typed
`inspection-read` failure with no partial results for selected and descendant
symlinks. A fifth removal control removes that error classification and must
fail both cases. The read-level control isolates that guarantee;
the outer selection check intentionally still detects the changed object.
An open-error overlay injects `EIO` at the selected file or directory, or at a
specific descendant file or directory, after the earlier path selection has
succeeded. All four cases require `inspection-read` and no partial results.
A sixth removal control restores the old error collapse and must fail all four
cases. Two additional cases inject an I/O error when opening a descendant's
metadata descriptor after its directory entry was enumerated. A seventh
removal control must fail both cases. Ordinary tests also use real mode-000
files and directories under an
unprivileged process. Missing entries and symlink replacements remain unstable.
Overlay fixtures, exact commands, timestamps, logs and hashes are retained.

Additional tests cover special entries, broken/looping symlinks, malformed plan
paths, short/long reads, unavailable identity metadata, canonical expansion
past the existing 4096-byte path bound, and descriptor disposal. Quota arithmetic
crosses exactly 100000 entries and 16GiB; small test-local budgets prove actual
traversal/read and multi-plan aggregation. Synthetic counters are not evidence
of hashing a 16GiB file. Complete snapshots count independently across plans.

`case-map.json` maps B7.1 corpus obligations to these tests. Worker isolation,
blocked-worker cancellation, cleanup/output barriers and both lifecycle winners
remain pending under their existing owners. No file contents enter telemetry.
