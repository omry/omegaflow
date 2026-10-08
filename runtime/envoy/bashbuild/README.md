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

The generator emits one shared Go table, separate Envoy/Awsh lookup functions,
and the Python host consumer. Lookup hashes the resolved regular executable and
rejects unknown digests, full target-tuple/path mismatches, a present system rc,
and changed adapter/trusted-input identities. The caller supplies the current
manifest-verified target tuple (`os`, `arch`, `os_release_id`, and `version_id`)
and asset hashes; later host/runtime integration owns verifying those values.
No consumer can retain support after changing those bytes or target fields
without matching qualification evidence. Launch, manifest validation and
runtime assembly belong to later leaves; these functions do not launch a
partial path.
