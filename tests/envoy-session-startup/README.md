# Validation artifact lifetime

All startup proof runners compile executables in runner-owned temporary
directories. A runner reuses its compiled peer across the three qualified
Bash targets. Each Docker build consumes a staged copy; that copy is removed
when the build finishes or raises an error. Compiled peers, Awsh comparison
binaries, and coverage/overlay executables are removed when their owning scope
exits, including build, qualification, and test failures or timeouts.

Evidence directories retain logs, results, coverage profiles, overlay sources,
Dockerfiles, image identities, and `test-binary.json` records of staged binary
hashes and sizes. Rebuilding a retained Dockerfile requires regenerating its
binary from the corresponding inputs. These directories are evidence, not
self-contained Docker build contexts after validation. Abrupt termination
that prevents Python cleanup, such as SIGKILL, can still leave scratch files.
No historical evidence or shared AWD retention policy is changed by cleanup.

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

B3.4.1 extends `private.py` with the actual Awsh private command/return
exchange. It sends the fixed trigger through a real PTY, performs the matching
start release and acknowledgement, holds the completion helper until matching
`input_closed`, and proves helper reap and persistent status, variables and cwd
across two commands. The PTY permits only the existing accept-line ending for
the silent workload; source/frame bytes and diagnostics fail the proof.
Additional cases reject wrong IDs, premature input closure and duplicate
acknowledgement, recover after source syntax rejection, and cancel running or
completion I/O on control EOF, malformed input or termination. Helper death is
fully reaped before its acknowledgement to make the missing-helper boundary
deterministic. A separate early helper-death case closes the control channel
and proves that the Envoy peer can cancel the fail-stop wait.
Fatal cases require nonzero Awsh exit and selected-shell reap. The test Envoy
peer disposes failed helper fixtures; this does not claim B6 cleanup behavior.
The same genuine qualification and manifest checks apply to every target.

Run `python3 tests/envoy-session-startup/coverage.py --qualification
<qualification-directory> --output <new-directory>` for supplemental component
coverage and the deferred-reply regression. It executes the same `Run` and
Session functions in a Go coverage test child on all three genuine targets,
retains each scenario profile and writes their union to `components.cover`.
This supplements the separate actual manifested Awsh proof in `private.py`;
it cannot qualify or replace that entry.

The runner separately compiles test-only source copies with a Go overlay and
requires a compiled canary. FIFO barriers hold the first completed job until
the next command is observed in the pending slot. Releasing the job must run
that command with its persistent state intact. The same test must fail when
the copied pending assignment is changed to discard the command. These fault
copies add no production hooks and their results are kept separate from the
current-source coverage profiles and genuine qualification. The original
source hashes must remain unchanged.

B3.4.2 assembles actual public execution and ordinary shared-PTY return. Run
`ordinary.py --qualification <qualification-directory> --output <new-directory>`
against all three genuinely qualified amd64 targets. The optional `--release`
selects a diagnostic subset; a subset does not establish cumulative acceptance.
The runner uses the existing immutable staging and receipt checks. No new
production entrypoint or qualification override is supplied.

The native cases preserve variables, physical cwd and preceding status across
commands, forward actual controller terminal input, and reuse its exact input
watermark. Each start and completion requires a covering monotonic output
mark. Logs retain every raw PTY byte with its exact public offset range.
Only the qualified Readline controls and its pre-start accept-line ending are
removed when comparing visible output; source/frame text or diagnostics fail.
The same private reader remains in use through all commands and idle shutdown.

A bounded deterministic cleanup peer holds the actual completion helper while
checking that no public completion is emitted. Success must precede helper
release and reap; failure closes the public channels and cannot make the
operation reusable. Host race tests cover blocked-input joins, caller-deadline
retention, invalid IDs/phases, duplicate results, private EOF, cleanup failure,
release/output failure and cancellation during cleanup. Unsupported lifecycle,
split and inspection paths cannot enter ordinary foundation success. B4, B5,
B6 and B7 retain those responsibilities; this peer is not descendant cleanup.

B4.1 adds `resize.py --qualification <qualification-directory> --output
<new-directory>`. It reuses the same actual manifested Awsh and qualified Bash
entries. The actual Bash foreground receives kernel `SIGWINCH`, reports the
new dimensions through `stty size`, accepts terminal input and completes through
the existing bounded cleanup peer. Public marks cover the exact raw prefix
before `resize_applied`; ordinary completion keeps the original operation start
and never regresses that prefix. Native idle shutdown covers an applied resize
before `draining`, and a decoded request superseded by the existing drain with
no resize acknowledgement. All three qualified targets are required for the
cumulative native proof; `--release` runs only a diagnostic subset.

Host tests separately exercise real PTY size readback, exact binary output,
continuous producers, same-frontier sequential resizes, delayed input-watermark
start/rejection/cancellation/timeout crossings, retained private return facts,
partial private frames during an existing output publication, and private fatal
input while resize waits for the output owner. A real non-terminal descriptor
produces `ENOTTY` in the direct ioctl: idle and active failures must emit fatal
`resize-failed`, close both channels, and publish neither an applied resize nor
an operation terminal result. These descriptor fault fixtures do not qualify a
target. B4.2–B4.4 retain lifecycle/gate integration and its complete crossed
control suite; split frontiers remain B5.3, actual census/cleanup remains B6,
and inspection remains B7. No production entrypoint or broader support claim
is supplied by these isolated assemblies.

B4.2 adds `lifecycle.py --qualification <qualification-directory> --output
<new-directory>`. Its isolated Envoy lifecycle core selects cancellation or
finalization after the actual start release. It validates the controlled
foreground through an explicit census peer and sends integer `SIGINT` directly
through the retained PTY master. Actual Bash returns status130 through the
existing A2.5 `input_close` and `completed` facts. The bounded cleanup peer runs
before `input_closed` releases the real helper. All three qualified amd64
targets are required; an optional `--release` is only a diagnostic subset.

Host race tests separately create a test-owned controlling PTY session and
observe real SIGINT delivery. They cover exactly-one signal, cancel crossing
finalize without a new deadline, empty foreground resampling and each winner,
wrong-session and unproved-member rejection, missing or failed census proofs,
direct ioctl failure and cancellation during a proof. Decision tests retain
queued-start ordering, return/cleanup crossings, the original grace and timeout
selection, and late valid input_close consumption without acknowledgement.
No lifecycle public result is published by this isolated core. B4.4 owns its
complete dispatcher, output/result and helper-window integration. Actual pidfd
census/cleanup remains B6.5; split and inspection crossings remain B5.3/B7.3.

# B4.3 gate proof

Run `python3 tests/envoy-session-startup/gate.py --qualification DIR --output DIR`
after fresh qualification of the changed Awsh bytes. The runner requires all
three retained Linux/amd64 qualification receipts and uses their exact images.
It exercises the fixed helper with hostile PATH, distinct gates, a nested launch
path, gated terminal input, covering output marks, terminal Ctrl-C, a late
continue and ordinary return with the persistent shell retained.

Go fault tests cover strict operation-helper intake, malformed/ancillary frames,
short and failed reply writes, the original reply deadline, one continue
admission, pinned helper lifetime cancellation, and unsatisfied input watermarks.
B4.4 owns integrated public/lifecycle dispatch and crossed outcome policy.
These tests do not establish B5 split, B6 descendant cleanup or ARM64 support.

