# Bash build evidence and consumers

B2.1 adds qualification infrastructure. The canonical `../bash-builds.json`
ships no supported build. Synthetic fixtures exercise admission and consumers;
they are not measurements of any Bash executable. Actual adapter execution and
verified Reploy candidates arrive in B2.3–B2.7; B2.8 owns complete qualification
and support admission.

From the repository root:

```
python3 tools/bash_builds.py validate runtime/envoy/bash-builds.json
python3 tools/bash_builds.py generate
python3 tools/bash_builds.py generate --check
python3 tools/bash_builds.py qualify candidate.json probe-plan.json receipt.json
```

One lowercase executable SHA-256 key identifies each candidate. Candidate
metadata records the release, Linux architecture and distribution ID/release,
immutable artifact/definition/lock
digests, resolved regular `/bin/bash` target, and compiled system rc path or
`none`. A `qualification: null` entry can never appear in a generated consumer.
Admitting support is an explicit reviewed edit adding a validated receipt;
running the harness never edits the table.

The probe plan has exactly `inputs`, `assets`, `probe`, and `timeout_seconds`.
`inputs` maps canonical local absolute regular-file paths to SHA-256 digests,
including the resolved Bash, probe executable/code and adapter/trusted inputs.
`assets` maps runtime paths under `/omegaflow-runtime` to those bound local paths.
It includes the Awsh binary, fixed rcfile/inputrc and terminal/locale trees.
`probe` is an explicit argv array for trusted local qualification code; it must
not be supplied by a workload. The isolated local asset tree need not be mounted
at the production path. Runtime packaging and manifest completeness remain C4/C5.

The probe executes twice on the candidate's genuine target with one deadline
per run and a combined 2 MiB stdout/stderr limit. The local architecture and
OS-release ID/version must match the complete candidate target. `/bin/bash`
may resolve through merged `/bin` placement to the recorded regular target;
the tool export and consumer must retain the same verified executable identity.
Both runs must exit zero, emit
no stderr, and produce the same bounded JSON object. Its `measurements` contains
startup PTY bytes (canonical base64, at most 4096 bytes), deterministic exported
environment removals/assignments, catchable signal names/numbers and measured
Readline behavior. Its `cases` maps every B2-owned case ID from the B1 inventory
to `true`. The probe owns executing those cases; static expectations never
establish their success. Failed, omitted, extra or nonrepeatable cases are
rejected. Input hashes, Bash placement and system-rc absence are rechecked after
execution. The runner kills the probe process group on every return path.

Receipts retain candidate, target, proof command, inputs, asset identities and
the measured result digest. They are compatibility records produced by trusted
qualification work, not security attestations against same-identity authors.
Do not hand-author passing receipts for real support.

The generator emits a separate qualified runtime payload at
`../etc/bash-builds.json`, data-independent Go schema and Envoy/Awsh lookup
functions, and the Python host consumer with the same qualified values and
admission grammar. Runtime Go output contains neither qualification data nor
its digest, including in generated comments. Regenerating only qualification
data leaves every runtime Go output unchanged. The generation check verifies
schema and payload freshness separately.

Runtime lookup opens only `/omegaflow-runtime/etc/bash-builds.json`, rejects
symlinks and nonregular files without blocking, bounds bytes at 2 MiB and
verifies the independent manifest digest before strict schema decoding. It
rejects invalid Unicode, duplicate or unknown fields, trailing data, candidate
entries without receipts, incomplete case evidence and stale candidate/result
digests. A qualification receipt cannot list the output table as an adapter
input. The table is independently manifest-bound instead of qualifying itself.

The existing lookup arguments retain their meaning: the caller supplies the
manifest-verified full target tuple and current adapter/trusted-input hashes.
For runtime lookup, that hash map also includes the fixed table path and its
manifest digest. Lookup removes only that output key before comparing the
remaining complete map to qualification inputs. Host lookup uses the generated
qualified values and the adapter input map directly. Both hash the resolved
regular Bash executable and reject unknown builds, target/path mismatches, a
present system rc and changed adapter inputs. There is no configurable table
path or fallback. Later runtime assembly owns manifest verification; this
correction exposes no production supervisor and advertises no build support.
