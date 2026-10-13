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

B3.2.2 retains the original complete Envoy/Awsh/Bash startup gate: PTY ownership,
exact 0–4096-byte startup comparison, complete public ready before startup
terminal bytes, immediate-response readiness, original Envoy launch epoch and
bounded partial-launch cleanup. Passing these private proofs does not claim
public startup or complete B3.2 delivery. Relay, operation start and ordinary
return remain B3.3/B3.4; no B4 behavior is included.
