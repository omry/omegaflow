# B2.2: Awsh helper transport and fail-stop mode

The helper library adds the actual Linux Unix-stream boundary around B1.2's
strict codecs. Each exchange sends one length-prefixed request, half-closes its
write side, and receives one reply through EOF. `ReadRequest` retains the
connection for the owning actor; `Reply` writes the whole reply then closes.
Every read, including the EOF check, uses recvmsg. Ancillary descriptors and
credentials and truncated messages are rejected; received rights are closed.
Positive short writes are retried, and no-progress or terminal failures fail.

The caller owns its existing absolute phase deadline and cleanup/supervision.
The transport changes no timer or signal disposition. A gate helper may wait
for its actor's continuation; the later gate-reply deadline begins at the first
reply byte, not at initial helper connection. This library introduces no timer,
lifecycle decision, listener policy, peer identity, or runtime path override.

The Linux `cmd/awsh` entrypoint currently accepts only argument-free
`bash-fail-stop`: it writes no bytes, opens no socket, and repeatedly SIGSTOPs
itself after SIGCONT. It ends only by a terminating signal. Ordinary helper
CLI assembly, report capture and shell execution arrive with their owning
successors; no incomplete shell/session execution path is exposed here.

Acceptance from `runtime/envoy`: `go test -count=1 -race ./...`, `go vet ./...`,
and Linux amd64/arm64 builds. Repository validation uses `.venv/bin/nox -s ci`.
Tests use real Unix sockets, pipes and subprocesses with deterministic peers.
Small socket buffers exist only in tests. Maximum requests have exactly 1 MiB
of payload; source replies respect the smaller 768 KiB source-field bound.
The subprocess exec test preserves inherited SIGINT ignore while waiting; it
is synthetic evidence, not selected-Bash completion-helper identity evidence.

## Case accounting

All B1 cases indexed to B2.2 are mapped below without changing the approved
static inventory. An isolated test proves the transport boundary only. Unless
noted otherwise, complete real-Bash/actor closure remains pending at B2.8.
Private frame tests prove exact byte writes, not the absent actor's dispatch.
B1-C034 remains pending at B5.3: split dual EOF/FIFO removal is not helper EOF.

| Stable case | Isolated test / disposition | Cumulative closure |
| --- | --- | --- |
| B1-C033-contract | All helper socket tests; phase actor integration pending | B2.8 |
| B1-C034-contract | Pending: real split dual EOF/FIFO removal | B5.3 |
| B1-C033-prefix-every-split | TestFrozenHelperSocketFormsAndFragments | B2.8 |
| B1-C033-payload-every-split | TestFrozenHelperSocketFormsAndFragments | B2.8 |
| B1-C033-short-prefix | TestMalformedHelperSocket | B2.8 |
| B1-C033-short-payload | TestMalformedHelperSocket | B2.8 |
| B1-C033-zero-length | TestMalformedHelperSocket | B2.8 |
| B1-C033-oversized-length | TestMalformedHelperSocket | B2.8 |
| B1-C033-trailing-data | TestMalformedHelperSocket | B2.8 |
| B1-C033-ancillary-data | TestAncillaryRightsRejectedWithoutLeak; TestCredentialsAndTruncationRejected | B2.8 |
| B1-C033-missing-half-close | TestRequestNeedsHalfClose; TestOriginalDeadlineNotExtended | B2.8 |
| B1-C033-premature-EOF | TestMalformedHelperSocket; TestMalformedReplyAndInvalidOutgoingClose | B2.8 |
| B1-C033-small-socket-buffer | TestMaximumPayloadsSmallSocketBuffers | B2.8 |
| B1-C033-max-request | TestMaximumPayloadsSmallSocketBuffers | B2.8 |
| B1-C033-max-reply | TestMaximumPayloadsSmallSocketBuffers | B2.8 |
| B1-C033-positive-short-write | TestExactWriteLoop | B2.8 |
| B1-PACKAGE-B2.2 | All helper transport, fail-stop and fixed-command tests | B2.2 |
| B1-C001-control-write-awsh | TestExactWriteLoop; TestFrozenPrivateWrites; TestOriginalDeadlineNotExtended | B2.8 |
| B1-AUX-private-execute-pty | TestFrozenPrivateWrites/private-execute-pty | B2.8 |
| B1-AUX-private-continue | TestFrozenPrivateWrites/private-continue | B2.8 |
| B1-AUX-private-start-release | TestFrozenPrivateWrites/private-start-release | B2.8 |
| B1-AUX-private-started-ack | TestFrozenPrivateWrites/private-started-ack | B2.8 |
| B1-AUX-private-input-closed | TestFrozenPrivateWrites/private-input-closed | B2.8 |
| B1-AUX-private-shutdown | TestFrozenPrivateWrites/private-shutdown | B2.8 |
| B1-AUX-private-ready | TestFrozenPrivateWrites/private-ready | B2.8 |
| B1-AUX-private-submit | TestFrozenPrivateWrites/private-submit | B2.8 |
| B1-AUX-private-start-prepared | TestFrozenPrivateWrites/private-start-prepared | B2.8 |
| B1-AUX-private-started | TestFrozenPrivateWrites/private-started | B2.8 |
| B1-AUX-private-start-released | TestFrozenPrivateWrites/private-start-released | B2.8 |
| B1-AUX-private-gate-ready | TestFrozenPrivateWrites/private-gate-ready | B2.8 |
| B1-AUX-private-gate-continued | TestFrozenPrivateWrites/private-gate-continued | B2.8 |
| B1-AUX-private-gate-interrupted | TestFrozenPrivateWrites/private-gate-interrupted | B2.8 |
| B1-AUX-private-input-close | TestFrozenPrivateWrites/private-input-close | B2.8 |
| B1-AUX-private-completed | TestFrozenPrivateWrites/private-completed | B2.8 |
| B1-AUX-private-rejected | TestFrozenPrivateWrites/private-rejected | B2.8 |
| B1-AUX-private-shell-exit-active | TestFrozenPrivateWrites/private-shell-exit-active | B2.8 |
| B1-AUX-private-protocol-error | TestFrozenPrivateWrites/private-protocol-error | B2.8 |
| B1-AUX-private-closed | TestFrozenPrivateWrites/private-closed | B2.8 |
| B1-AUX-private-shell-exit-idle | TestFrozenPrivateWrites/private-shell-exit-idle | B2.8 |
| B1-AUX-private-execute-split | TestFrozenPrivateWrites/private-execute-split | B2.8 |
| B1-AUX-helper-startup-state | TestFrozenHelperSocketFormsAndFragments/helper-startup-state | B2.8 |
| B1-AUX-helper-startup-ready | TestFrozenHelperSocketFormsAndFragments/helper-startup-ready | B2.8 |
| B1-AUX-helper-completion-state | TestFrozenHelperSocketFormsAndFragments/helper-completion-state | B2.8 |
| B1-AUX-helper-completion-ready | TestFrozenHelperSocketFormsAndFragments/helper-completion-ready | B2.8 |
| B1-AUX-helper-source-request | TestFrozenHelperSocketFormsAndFragments/helper-source-request | B2.8 |
| B1-AUX-helper-prepared-request | TestFrozenHelperSocketFormsAndFragments/helper-prepared-request | B2.8 |
| B1-AUX-helper-gate-request | TestFrozenHelperSocketFormsAndFragments/helper-gate-request | B2.8 |
| B1-AUX-helper-startup-accepted | TestFrozenHelperSocketFormsAndFragments/helper-startup-accepted | B2.8 |
| B1-AUX-helper-completion-accepted | TestFrozenHelperSocketFormsAndFragments/helper-completion-accepted | B2.8 |
| B1-AUX-helper-prepared-accepted | TestFrozenHelperSocketFormsAndFragments/helper-prepared-accepted | B2.8 |
| B1-AUX-helper-gate-accepted | TestFrozenHelperSocketFormsAndFragments/helper-gate-accepted | B2.8 |
| B1-AUX-helper-source-pty | TestFrozenHelperSocketFormsAndFragments/helper-source-pty | B2.8 |
| B1-AUX-helper-source-split | TestFrozenHelperSocketFormsAndFragments/helper-source-split | B2.8 |

Selected-shell startup/report identity and descriptor topology remain B2.3;
source checker/frame/marker behavior B2.5; start-phase coordination B2.6;
real Bash completion/helper identity and ignored-INT window B2.7; gate outcome
selection B4.3; real split resources B5.3; and final actual-actor isolation B8.4.
No Bash build is acquired, measured, admitted, or advertised by this slice.
