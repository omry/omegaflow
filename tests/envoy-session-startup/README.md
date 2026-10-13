# B3.2 startup proofs

B3.2.1 owns the actual fixed Awsh entry through private readiness and idle
shutdown. Run `private.py --qualification <fresh-qualification-directory>
--output <new-output-directory>` after the existing complete native B2
qualifier has measured the exact current Awsh binary on every selected target.
The runner validates receipt and image identities, installs each actual measured
entry, materializes qualified terminfo alias bytes as regular files, and writes
an isolated manifest with exact payload sizes, modes and hashes. Its Envoy peer
is an explicitly test-only Go binary. Host runtime staging remains C4.

The genuine non-root cases verify independent runtime and qualified-Bash
admission, exact one-exec descriptor arguments, selected-shell parent/group/
session topology, strict private ready, idle shutdown followed by private EOF
and Awsh reap, control EOF, pre-ready EOF, malformed private traffic, unexpected
idle release signals, selected-shell reap and helper-path removal. Startup
failure cases fill the result pipe, preserve a pre-existing helper directory,
and verify the deadline-free diagnostic write returns at the shared five-second
bound or promptly on control EOF. The process deadline belongs to the peer;
the production Awsh entry adds no lifecycle timer.

Two additional read-only fault images per genuine target verify pre-session
runtime-manifest and qualification rejection. The qualification image retains
a valid exact manifest while supplying an empty admission table. Both cases
require one bounded `shell-launch` result before private EOF and nonzero Awsh
exit, with no helper state created. These fault images are refusal proofs;
they do not add supported entries or production overrides.

Host Go tests additionally reject changed, missing, extra, symlinked, writable
and incorrectly executable payload files, invalid manifest identities, escaping
or duplicate paths, wrong schemas/toolchain/target and caller cancellation.
Existing complete-write and startup suites retain their original epochs and
native failure causes after transport reuse.

Qualification binds the actual changed Awsh binary and every adapter input.
Historical receipts cannot admit later bytes. Only targets with genuine fresh
receipts may remain advertised. ARM64 build checks do not qualify ARM64.

B3.2.2 owns the complete Envoy/Awsh/Bash startup transaction. Run
`public.py --qualification <qualification-directory> --output <new-directory>`
on the same genuine targets. Both runners use one isolated manifest producer
in `staging.py`; it verifies the retained receipt/entry/image identities and
compares the newly built Awsh and fixed startup assets with their measured
hashes. Changed bound bytes require fresh qualification, never a copied claim.
The session test binary is an isolated assembly of the actual Envoy module;
the production Envoy entry and host payload staging remain later owners.

Native public cases verify the exact measured startup string and private/public
PID/cwd agreement, complete public ready before startup terminal bytes,
immediate controller response, disposal of the completed launch context,
public/private descriptor isolation, idle shutdown with the controller reason,
private EOF and zero-status direct Awsh reap, terminal drain/half-close and
exact final output offset. Separate fixed-input fault images fail before child
allocation. A pre-existing session directory is refused without removing foreign state.
Repeated selected-channel loss exercises owned partial-launch cleanup.
An isolated owned-child fixture delays the real Wait publication to prove that
SIGKILL alone cannot authorize session-path removal. Expired cleanup remains
fatal and bounded; a later caller can join the retained reap under its own
context before path removal, without clearing the original failure. This
fixture is separate from genuine selected-Bash measurements.
Failed launch retains the unfinished cleanup owner on the existing Channels
object even though it returns no usable workload. The cleanup-only accessor
does not restore readiness, authorize another launch, or reset the failed epoch;
the caller still owns the original deadline and Reploy termination decision.

Real PTY host fixtures separately cover 0/4096-byte exact buffers, overflow,
mismatches, late expected bytes, startup EOF/EIO, the original deadline,
cancellation callback joins and final partial-output offsets. These boundary
fixtures are not genuine startup measurements. Envoy filters delegated launch
values and installs the protocol's fixed terminal/history/locale values before
Awsh exec. Failure cleanup and idle shutdown each retain their single existing
final-drain epoch; no phase resets or Awsh lifecycle timer are added.

The original cumulative B3.2 acceptance requires both private and public
proofs. Relay, operation start and ordinary return remain B3.3/B3.4; no B4
behavior or B6 descendant census is included.
