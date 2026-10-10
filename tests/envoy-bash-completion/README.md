# B2.7 ordinary completion and persistent Bash

This isolated suite launches each verified candidate as the actual selected
interactive Bash, runs the fixed source/PS0/start transaction, and exercises the
production completion methods against a deterministic Envoy peer. Real Envoy
descendant census, adopted-child cleanup, split stream lifecycle and filesystem
inspection remain with their later owners. No supported admission is claimed.

Run from the repository root:

```sh
GOCACHE=/tmp/omegaflow-go-cache python3 tests/envoy-bash-completion/run.py \
  --acquisition /absolute/path/candidate-acquisition.json \
  --output /absolute/path/new-completion-proof --mutations
```

Acquisition and exact source-image checks are reused from the launch proof. The
driver builds current Awsh and a test-only supervisor, stages immutable terminal
and locale assets, and uses exact derived image IDs without network access.
Bash and the supervisor run as UID 1000 with hostile PATH/HOME. Production keeps
qualified-only admission with no candidate override or supervision CLI.

The proof covers consecutive operations in one Bash, saved zero/nonzero source
status, history and vi restoration, exported and unexported variables, functions,
aliases and positional state, raw workload termios, disabled ordinary builtins,
and background wait-record removal. The simulated Envoy peer kills the authored
background job before acknowledging input closure; it does not prove the
production Envoy census implementation.

The held prompt_state peer is Bash's direct child with the manifested executable
and no child. Procfs verifies inherited SIGINT ignore, and the test actually
sends SIGINT while the helper is held. The start context is canceled before
completion; the original cleanup context governs both helper exchanges and the
terminal handoff. Missing acknowledgment expires that same context.

Every terminal field is compared with a fresh state deliberately changed during
cleanup, then ICANON and ECHO must both be clear at actual Readline entry. The
allowed-trap case delivers a non-reserved signal after `prompt_state`; its trap
changes cwd and an exported path input, and final inspection plans must use that
live state. Final inspection plans retain lexical dot and slash components and
carry no filesystem result. Resolver tests cover undefined and malformed
variables, home lookup, literal shell syntax, Unicode and bounds.

Faults cover reserved trap/builtin and command-name damage, changed bindings and
prompts, invalid environment, wrong peer, control type/ID/duplicates, expired
cleanup context, terminal identity, unavailable final helper, wrong initial
helper phase, duplicate final state, altered final status, premature finalization,
an unexpected release signal and closing the session with a held helper. Close
must dispose the completion pidfd and cancellation callback. Fatal paths must
publish no completed frame. Five controlled mutations detect missing direct exec,
stale terminal state, unchecked control ID, incorrect source status and
resolution from the pre-cleanup state.

Retain inputs.json, results.json, exact image IDs, coverage and per-case logs.
The input manifest hashes acquisition, runner, shared validation rules, binaries
and fixed shell assets. Linux arm64 compile success is not target qualification;
B2.8 owns cumulative supported admission.
