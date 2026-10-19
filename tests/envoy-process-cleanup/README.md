# B6.2 descendant cleanup

The existing process-census owner supplies the lifetime pins and ancestry proof.
Its cleanup engine signals authored descendants through pidfds and reaps only
Envoy-owned adopted non-shell children. It preserves external Awsh, selected
Bash and the exact validated completion helper. The caller supplies the original
cleanup deadline; progress and interrupted syscalls do not create another one.

Exited direct Bash children may remain as descriptor-free wait records until
the existing completion hook clears them. The engine does not publish
`input_closed`, close split streams, drain output or wire lifecycle controls.
Those responsibilities remain B6.3 and B6.5. No complete actual-actor B1–B8
conformance is claimed here.

Run the isolated kernel fixtures:

```sh
GOCACHE=/tmp/omegaflow-go-cache go -C runtime/envoy test -race -buildvcs=false -run '^TestProcessCleanup' -count=1 ./session
```

Each case creates a dedicated Envoy test subprocess. Only that subprocess enables
subreaper behavior. Existing Go fixture roles supply explicit Awsh/selected-child
parentage; they do not implement Awsh/Bash behavior. A sibling outside the
controlled tree must survive. Fixture disposal retains independent lifetime pins,
including when a production-owned pin is deliberately lost.

The qualified matrix adds real Bash-authored background, `disown`, `nohup`,
`setsid`, double-fork and SIGTERM-ignore processes. It verifies their termination,
adopted-child reap, helper survival, real Bash wait/job-table clearance and
outside-tree preservation. The Awsh and Envoy parent roles are still explicit
test fixtures. The helper-executable negative case rejects an authored `sleep`
process as a helper before signalling any authored execution.

```sh
GOCACHE=/tmp/omegaflow-go-cache python3 tests/envoy-process-cleanup/run.py --qualification PATH_TO_GENUINE_THREE_BUILD_QUALIFICATION --output NEW_EVIDENCE_DIRECTORY
```

The runner reuses existing immutable-image and adapter-input qualification
verification. It runs all three Linux amd64 rows and retains exact images,
executable and log hashes. It does not requalify changed adapter inputs or infer
ARM64 support from cross compilation.

Linux references: [pidfd signalling](https://man7.org/linux/man-pages/man2/pidfd_send_signal.2.html)
binds signals to the retained process lifetime; [targeted child waits](https://man7.org/linux/man-pages/man2/waitid.2.html)
retain direct-parent ownership. Cleanup uses a positive exact child PID only
while its unreaped lifetime remains pinned and has no competing Envoy reaper.
It never uses a catch-all child wait or numeric process-group signal.

Explicit test-only syscall overlays also exercise interrupted signal/wait calls,
permission and descriptor errors, lost wait ownership and repeated no-progress
cleanup under the original caller epoch. A signal barrier introduces an actual
late adopted child after the first census and requires its termination/reap.
Production contains no callback, injectable syscall or fault state. A no-op
signal overlay models an unkillable task for the deadline proof; it does not
claim a naturally unkillable kernel task was created. The Awsh-wait case leaves
an actual exited root waitable and proves that only its existing owner reaps it.
