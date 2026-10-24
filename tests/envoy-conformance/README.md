# Local Part B conformance

The corpus in `tests/fixtures/envoy-protocol-v1/cases.jsonl` defines the B-owned
inventory. This runner keeps C/D ownership unchanged. Its catalog joins exact
case IDs to the existing proof maps and explicit source locators. A locator is
not an executed proof. Each catalog row remains unverified until final evidence
closes that case.

Inspect the obligations without claiming acceptance:

```sh
python3 tests/envoy-conformance/run.py --audit --output NEW_CATALOG.json
```

Run the new actual-actor assembly matrix:

```sh
GOCACHE=/tmp/omegaflow-go-cache python3 tests/envoy-conformance/run.py \
  --native-assembly --qualification GENUINE_QUALIFICATION --output NEW_DIRECTORY
```

The matrix requires all three genuine Linux amd64 Bash rows. It reproduces the
qualified Awsh hash before staging. Each isolated target uses the real Envoy
coordinator, manifested Awsh, selected Bash, terminal, private protocol, process
census and cleanup. It exercises source/status preservation, source rejection,
ten crossed start/cancel phases in each execution shape, all 120 existing
fatal reserved-state mutations, and final-state inspection paths through the
actual restricted worker (variables, HOME, named user, literals and physical
cwd). Reserved-state cases inspect the actual stopped
helper, its repeated stops, Bash's non-returning fallback, unchanged traps and
builtins, controller loss, actor reap and descendant disposal.

The pre-write cancellation test waits for actual acceptance while the writer is
held. A started-operation test waits for Bash to enter authored builtins before
checking cancellation. This establishes a live foreground target after the PS0
helper returns. The tests require the intended cancellation status/range and
shell reuse. Existing lifecycle suites retain the separate fatal proof-failure
cases. Reserved-state failure tests require actual disposal and no terminal
operation result.

The observation overlay only holds existing boundaries. The one-shot wrapper
prevents a repeated real partial write from closing the same test channel twice.
It does not replace the write, frame decoder, signal, process proof or Wait.
Qualified runtime assets remain unchanged. All observations and transcripts
remain in the new evidence directory.

Run the fixed full execution selection from an environment with project Python
dependencies, Go, Docker and the preserved genuine qualification inputs:

```sh
GOCACHE=/tmp/omegaflow-go-cache .venv/bin/python tests/envoy-conformance/run.py \
  --run --qualification GENUINE_QUALIFICATION --output NEW_DIRECTORY
```

The selection includes the full Go race suite, Python tests, both existing
inspection fault/worker runners and their omission controls, and existing native
startup, operation, signal, gate, split, process, inspection, failure, terminal
and captured/private-failure runners. Three independent commands run at a time.
The three complete private-failure targets run separately, with a mandatory
controls job. The full selection requires all three targets and every control.
Completed commands are recorded as they finish.
Each command owns isolated actors and overlays. The report retains command wall
intervals; these overlap and must not be added as exclusive elapsed time.

`execution.json` records execution separately from per-case closure. A green
execution report alone does not advertise the complete B1–B8 gate. Component
oracles, modeled negative controls, compiler decisions, qualified adapter tests
and actual actor crossings retain their distinct evidence boundaries. A host
test skipped for missing native inputs cannot satisfy a runtime obligation.
No peer-only result closes the final actual-actor gate. Missing evidence remains
unfinished. An implementation defect requires its owning correction successor.

Verify current execution evidence and produce the final case report:

```sh
.venv/bin/python tests/envoy-conformance/run.py \
  --verify NEW_DIRECTORY/execution.json --output FINAL_REPORT.json
```

Place the final report outside its execution directory. Verification retains a
new adjacent `.checks` directory and reruns the existing B8.3 cumulative checker.
It requires the exact complete command plan, all genuine targets, the selected
qualified adapter cases, current source bytes/modes, actual owning test execution
the required actor crossings, and actual execution of named owning oracles.
The five historical isolated live-inspection locators retain their explicit
full-actor replacement; they are not relabelled as executed adapter tests. It retains the original proof locators and
states whether closure uses mapped native scenarios or an owning oracle paired
with actual actor integration. Synthetic B1 boundary inputs stay explicit;
actual startup expectations use each qualified entry's measured bytes. A zero
startup measurement is not relabelled as an observed synthetic maximum.

The actor is the actual Envoy session/relay/coordinator module in dedicated test
assembly. The manifested Awsh and selected Bash are their actual qualified
executables. Production entry and host packaging remain later delivery owners.
The report binds every retained execution artifact and records the tool versions.

Evidence binds runtime, compiler, test, fixture, design and runner input bytes
and modes. Output directories cannot overwrite retained attempts. The assembly
checker rejects stale inputs, missing/repeated native markers, skipped cases,
failed commands, mixed Awsh identities and missing/substituted qualification
targets. Checker tests use explicitly synthetic receipts; they cannot qualify
a build.

Only genuinely measured targets can be claimed. ARM64 compilation alone does
not supply qualification. This runner does not upgrade Reploy or implement the
later C/D integration boundaries.
