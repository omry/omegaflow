# B2.6 isolated Bash start proof

Run `python3 tests/envoy-bash-start/run.py --acquisition PATH --output DIR --mutations`.
The acquisition manifest must contain the retained Reploy image and executable
identities. The runner reuses B2.3.1's input validation, resolves immutable image
IDs before creating evidence, and rehashes `/bin/bash` in each test container.
It never admits a supported build; ARM64 evidence remains pending B2.8.

The actual Session, selected Bash, fixed rcfile, source helper and prepared helper
run against deterministic private pipes and a real PTY. Candidate start supervisors
run as UID 1000 with root-owned `/run` and a UID-1000 `/run/omegaflow` subtree;
the parent test process prepares the fixtures before dropping the supervisor child.
The only injected
production dependency is the already-checked source admission boundary inside
an isolated test binary. The public admission method still requires B2.5's
qualified checker. The cumulative B2.5 suite separately runs that actual checker
on the same candidates; the fixture cannot qualify a candidate or expose an
executable supervise command.

The start cases prove reservation and recoverable rejection, source delivery,
withheld preparation through `start_prepared` and `started`, matching controls,
full fresh workload terminal restoration under the original context, and one
post-PS0 release signal. Normal and initially raw terminal states are exercised.
Wrong IDs, crossed/duplicate helpers, duplicate/early controls and signals,
missing phases, missing signal and failed signal queuing fail closed. Full raw
PTY streams remain in test logs. Assertions allow only the observed Readline
terminal controls and CR/LF before release; source, frame and PS0 text is absent.

Split fixtures validate fixed paths, ownership, full permission bits, type and
symlink rejection. The matrix includes workload-owned acceptance plus wrong-owner,
wrong-mode, wrong-type, wrong-derivation, and reserved-subtree symlink negatives.
Open readers/keepalives are test fixtures. Ordinary authored
status 1 is observed at the next prompt with independent stdout/stderr bytes;
failed stdout or stderr opens instead reach the existing `INNER_ENTERED`
fail-stop sentinel. Actual Envoy split setup and cleanup remain B5.3/B6.

The PS0 fault cases in `cases.json` reuse the real-Bash submission peer to inject
missing, partial, duplicated, malformed and trailing-LF markers, nonzero exit,
helper termination and broken wire replies. These cases require the substitution
to remain blocked in the manifested fail-stop, including after SIGCONT, with no
authored source execution. They test the wrapper, not private actor ordering.

B1-C031 maps to source/helper/start ordering, identity and redisplay cases;
B1-C032 maps to split entry versus authored status; B1-C033 reuses strict helper
framing and EOF tests; B1-C035 maps to the start phases. Real Envoy publication,
terminal forwarding and queued cancellation are B3/B4/B6 responsibilities.
Completion validation and making a released session reusable remain B2.7.
The B1.3.1 successor owns the B1-C022 query-corpus correction before B2.8;
approved PR45 remains immutable.

Mutations use temporary Go overlays and read-only test bind mounts. They first
run the original `/run` ownership predicate against the non-root acceptance case
to prove the regression, then remove restoration, release-ID matching, release
arming and the split-entry sentinel; each must fail its selected case for the
expected reason. Production files and configuration are never changed by a
mutation run.

Go coverage includes both the parent tests and isolated supervisor processes.
A failed run remains failed evidence, even when a later corrected run passes.
EOF and test disposal are not completion or descendant-cleanup acceptance.
