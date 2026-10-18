# B6.1 Linux process identity and census

The session package owns native subreaper setup, pidfd lifetime pins, repeated
controlled-tree census and complete foreground-group membership. Tests use real
Linux processes in a dedicated test subprocess. Its session leader and selected
child are explicit test fixtures; they do not implement Awsh or Bash behavior.
The production lifecycle assembly remains in B6.3/B6.5, and the complete actual
Envoy/Awsh/qualified Bash conformance gate remains in B8.4.

Run the focused suite from the repository root:

```sh
GOCACHE=/tmp/omegaflow-go-cache go -C runtime/envoy test -race -buildvcs=false -run '^TestProcess' -count=1 ./session
```

Cases cover pinned live identity, unreaped and reaped lifetimes, numeric PID
substitution, CLOEXEC and descriptor disposal, missing/expired caller epochs,
exact roots and parent/session topology, ordinary descendants, a current
foreground sample, 24 rapid orphan adoptions, double fork with `setsid`,
complete live group membership, a stale empty group, foreign-group rejection,
outside-tree preservation and dead roots. Foreground membership uses the
complete `/proc` observation that updates the controlled tree, so a child
created after the earlier refresh is classified from its current lifetime.
The lifetime regression models a stale numeric map entry with an actually
reaped pidfd and verifies that a live sampled member fails explicitly; it does
not claim that the kernel recycled a PID in the test. A separate native case
verifies that a dead selected root keeps its original lifetime pin during
foreground proof failure.
An unreaped adopted zombie cannot authorize idle. Concurrent census also races
48 double-fork/`setsid` adoptions. Interrupted pidfd polling retries under the
unchanged caller deadline; explicit test-only overlays cover one interruption,
repeated interruption through expiry, and a fatal non-interruption error.
A joined disposal error remains fatal even when another error is an ordinary
exit/reap race. Removing a required lifetime pin cannot omit that process.

Each native test enables subreaper status only inside the dedicated Envoy
fixture. The shared test process retains its original adoption behavior. The
selected fixture terminates and reaps its direct children. The dedicated
fixture terminates and reaps the explicitly owned adopted children. The modeled
stale-lifetime and dead-root cases also run inside that dedicated fixture, so the shared test
process keeps its original subreaper state. The outside service runs as a
sibling of the dedicated fixture and remains untouched.

For coverage, pass `-coverprofile` and set `OMEGAFLOW_PROCESS_COVER_DIR` to an
existing evidence directory. The eleven dedicated fixture runs retain separate
`native-<case>.cover` profiles. Combine block counts with the parent profile;
the parent's profile alone omits native subprocess behavior. The race runtime's
one-second exit sleep is disabled only inside test fixtures; race detection and
its nonzero failure status remain enabled.

The same caller-owned context covers each complete observation and convergence.
Two successive matching observations establish a current census, not a promise
that a process cannot fork afterward. Later owners must repeat this proof across
termination, EOF and the final output barrier. No new actor, timer, process
admission ceiling, lifecycle signal or cleanup engine is supplied here.

Kernel references: [pidfd fdinfo and exit readiness](https://github.com/torvalds/linux/blob/v6.18/fs/pidfs.c)
explain the lifetime binding, including `Pid: -1` after reap. The
[proc filesystem reference](https://docs.kernel.org/7.0/filesystems/proc.html)
defines process stat fields. A numeric PID and start-time value alone do not
replace the retained pidfd.
