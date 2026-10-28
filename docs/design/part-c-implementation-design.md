# Part C implementation design and readiness

## Scope and authority

This design details the unimplemented C4.2–C8.2 work. It does not establish
implementation, native qualification, PR approval, or production enablement.
Its requirements come from [Workload Envoy](omegaflow-envoy-design.md),
[Reploy environments](reploy-environments-design.md), and
[Envoy protocol v1](envoy-protocol-v1.md). Those contracts remain authoritative.
The delivery catalogue remains the single list of slice identities and order.
This document supplies implementation detail; it is not a second delivery plan.

The inspected base is C4.1, `27d3da957b4790d3638d745599a99956d4fac3c1`.
C1–C3 and the runtime foundations already exist there. C4.2 and C5–C8 do not.
Existing controller classes are reusable components, not a public Reploy runner.
The review must distinguish a sufficiently specified design from an executable
prerequisite. The owner has selected a separate public Reploy prerequisite,
RP-C6 below; its implementation and release remain pending.

The first real workload remains Linux/amd64 with one of the three qualified
Bash tuples: Debian 12/5.2.15, Debian 13/5.2.37, Ubuntu 26.04/5.3.9.
An ARM64 build or ARM64 controller capability is not workload qualification.
Browser, media, general project discovery, default-path cutover, host-workload
parity, and Part D recording adaptation remain outside these slices.

## Existing mechanisms and required handoffs

| Producer | Output and identity | Consumer | Sole owning slice |
| --- | --- | --- | --- |
| Runtime builder and B2 harness | Qualified runtime directory; complete manifest; exact adapter receipts | Installed package assembly | C4.2 |
| Package build hook | Installed `omegaflow/runtime/linux-amd64` resources containing that directory | Private runtime stager | C5.1 |
| Runtime stager | Fresh read-only tree and manifest identity | Blueprint composition and mount validation | C5.1 |
| Hydra composition | Complete resolved controller/workload YAML and digests | Public Reploy stage/build commands | C6.1 |
| RP-C6 public Reploy handoff | Exact selected-generation environment, executable and mount evidence, bound through admission | Reserved-value and launch checks | Reploy prerequisite; consumed by C6.2/C6.3 |
| Existing compiled recording plan | Bounded run manifest and declared input assets | Internal controller command | C7.1 |
| Input stager | Separate controller-only read-only input mount and command arguments | Prepared controller blueprint | C7.2 |
| Capability checks and public stage/build | Separate prepared deployment directories | Public controlled-session invocation | C8.1 |
| Public Reploy command/client and C2/C3 | Host result, diagnostics, partial output and termination evidence | Caller and later Part D integration | C8.2 |

Use the existing runtime/table schemas, `ControllerSession`, `EnvoyController`,
strict public codecs, recording-plan validator, and Hydra configuration entry.
Do not introduce a second controller state machine, qualification engine,
blueprint merge engine, transport, workflow script, receipt authority or cache.
New Python modules below are ordinary responsibility boundaries, not plugins.

## Cross-slice preparation and growth

Review these handoffs together before implementing a dependent slice. At the
actual base, preparation checks drift, named prerequisites, file ownership and
the estimate; it does not rediscover the architecture or duplicate this design.
The normal sequence remains implement, focused/full validation, local review,
checkpoint, draft publication, exact-head remote review, terminal acceptance.

Slice delivery order is distinct from per-run execution order. At runtime:
allocate private run/input/output paths; validate and stage the installed runtime
and bounded controller input; resolve the application composition and its exact
image through RP-C6; check the image-plus-application environment before reserved
composition; supply the staged paths and reserved entries to Hydra; compose both
final blueprints; validate structural launch policy and retain exact YAML;
stage/build the two Reploy deployments; verify final effective launch facts and
bind exact-generation admission; invoke the public session. No bootstrap starts
before both inputs and all required checks exist. C7 adds declarations to the
same Hydra configuration in source; it never patches retained YAML. RP-C6 must
support the pre-reservation image check and final-generation binding without
requiring a second image-selection path or a build solely to undo composition.

Replanning edits the existing catalogue, dependency edges and proof ownership.
It does not append a second sequence or retain two executable copies of the
same responsibility. Replace an unstarted parent with uniquely numbered
children only when the revised boundaries are reviewed. Retain the parent as
history and map every responsibility and test to exactly one child. Never
restart approved work, discard a required case, or reuse a parent ID for two PRs.
Changes to an approved predecessor require its own correction successor.

The estimates include additions and deletions, executable build/staging tools,
generated production text, integration code and failure handling. Test estimates
include genuine fixtures, fault controls and integration work. Shared suites
are extended at their owner and rerun by dependants; their code is counted once.
Numbers are bottom-up planning estimates, not measured diffs or guarantees.

| Slice | Production components; total | Test/fixture estimate | Approximate paths | Design readiness |
| --- | --- | ---: | ---: | --- |
| C4.2 | Artifact guard 180, shared projection 40, generated text 30; 250 | 650 | 15 | Detailed; native execution pending |
| C5.1 | Installed reader/validation 200, private copy 220, package bridge 180; 600 | 650 | 7 | Package bridge specified below |
| C5.2 | Trusted-asset checks 150, existing-runner integration 100; 250 | 500 | 6 | Uses accepted C5.1 output |
| C6.1 | Generic structural models 250, registration/composition 130, native YAML/templates 120; 500 | 650 | 8 | Model surface must match selected Reploy contract |
| C6.2 | Environment materialization 180, reject/final-value checks 150, composition integration 120; 450 | 400 | 5 | Implementation waits for RP-C6 acceptance |
| C6.3 | Mount checks 180, Bash/asset checks 200, launch evidence integration 170; 550 | 650 | 6 | Implementation waits for RP-C6 acceptance |
| C7.1 | Run schema/plan encoding 250, bounded validation 150, asset inventory 100; 500 | 600 | 5 | Plan round-trip must cover existing variants |
| C7.2 | Private input copy 100, mount/command composition 150, entry validation 100; 350 | 350 | 5 | No recorder adaptation |
| C8.1 | Capability/routing 180, public command adapter 180, deployment assembly 180; 540 | 600 | 6 | Depends on C6 handoff resolution |
| C8.2 | Public client effect executor 200, host invocation/result 180, retention/teardown 170; 550 | 800 | 7 | Actual public Reploy proof required |

These totals do not justify a split by themselves. Each slice has one accepted
output. C5.1 includes the minimal installed-source bridge because staging a
fixture while the actual package has no runtime would not satisfy its contract.
Before coding, revise an estimate if a component is missing or its conservative
upper bound exceeds the existing envelope. The governing 750 target and 1,000
hard production-line limit still apply; do not hide excess work in test tools.
In particular, C6's unresolved handoff has no implementation allowance yet.
It belongs to the separately reviewed RP-C6 work below and must not be absorbed
into C6.2/C6.3 under these estimates.

## C4.2: qualify the exact runtime artifact

Extend `tools/build_envoy_runtime.py`, `tools/bash_builds.py` and the existing
qualification/client runners. Freeze one candidate from C4.1's deterministic
build and verified trusted inputs. Add test-only `--runtime PATH` selection to
the existing runners. They copy the package unchanged and do not rebuild either
binary or substitute each image's own terminal/locale files.

Run the existing 258 B2 cases twice on each genuine target. Measure the exact
16 adapter inputs in the retained candidate, not the historical 59/60-input
qualification. Exclude table/manifest outputs from adapter inputs to avoid
self-reference. Keep every executable and trusted behavioral byte bound.
Admit all three successful entries, regenerate all consumers, then rebuild into
a fresh final directory. Both binaries and behavioral assets must remain equal
to the measured candidate. Changed bound bytes require affected qualification
again. Verify the final table and manifest independently.

Add read-only `--verify-qualified PATH --source-revision REVISION` to the
existing builder. Reject missing/extra/symlinked/changed files, wrong modes,
platform/toolchain/revision, stale generated consumers, an empty or unqualified
table, and receipts whose input map differs from the package. Run all 26 actual
client scenarios on each target with the complete final package unchanged.
Before delivery acceptance rebuild and verify against the exact committed
revision; a development base revision alone does not identify uncommitted bytes.

Owned tests: `tests/test_envoy_runtime_build.py`, `tests/test_bash_builds.py`,
`tests/envoy-bash-qualification`, `tests/envoy-session-client`, and shared startup
staging. Acceptance includes generator check, focused tests, Go race/vet,
Python CI, independent-path reproducibility and native proof. Build tools may
run from source; production staging may not.

## C5.1: install and stage one qualified package

Add `src/omegaflow/runtime_artifact.py` for strict manifest validation and
`src/omegaflow/runtime_staging.py` for the private copy. Reuse C4's schema and
table rules; factor shared Python validation where possible instead of keeping
different build-time and installed interpretations. The installed source is
`importlib.resources.files("omegaflow") / "runtime" / "linux-amd64"`.
No production source-path override, checkout lookup, PATH search or download is
permitted. Missing resources fail before deployment.

Extend the existing Hatch build hook with the build-time-only
`OMEGAFLOW_RUNTIME_ARTIFACT` environment variable, naming one canonical existing
directory containing an already accepted C4.2 artifact. An absent value selects
the unchanged existing package path; an empty, relative, symlinked or invalid
supplied value is an error. No runtime code reads this variable. Require
matching package version, platform, source revision and qualified manifest;
include its complete bytes at the package resource path using Hatch's existing
`build_data["force_include"]` mapping, without rebuilding, writing a staged
runtime into the source checkout, or selecting a different profile. Record its
manifest digest with build evidence.
The ordinary existing wheel path must keep working when no Envoy artifact is
being built; such a wheel cannot pass the isolated runtime capability check.
Do not claim a universal wheel contains an architecture-specific executable or
copy the host executable into an artifact for a different workload architecture.
General multi-platform release automation and clean-install cutover remain E.

The stager allocates a fresh mode-0700 private parent. Enumerate and validate the
source's entire payload against its manifest with strict duplicate-field/path
rejection, normalized relative POSIX paths and fixed roots. Never follow a link
at the root, parent or payload. Open each file safely, bind its type/mode/size,
hash the bytes copied, and reject mutation or replacement. Reject unknown files,
special files and hard-link aliases. Write the verified manifest last; remove
write permissions without removing required executable/read permissions. The
current manifest records an `executable` boolean, not an exact numeric file mode.
Wheel installation may restore write bits on installed resources. Validate their
regular type, bytes and executable intent, then create staged data and manifest
as 0444, executables as 0555 and payload directories as 0555 under the private
0700 parent. Never change the manifest or qualification to describe installer
mode normalization. Reject unexpected executable intent and special mode bits;
compare normalized stage modes, not equality to installed write bits. Verify
the completed tree before exposing its path. On failure remove only the new
owned partial destination; retain diagnostics and leave the source untouched.

Tests in `tests/test_runtime_artifact.py`, `tests/test_runtime_staging.py` and
the existing build-hook suite cover malformed inventories, traversal, duplicate
keys, symlinked ancestors, mutation during copy, wrong target/version, missing
installed resources, package resource inclusion and failure cleanup. Install a
locally built artifact-bearing wheel into a temporary environment, stage outside
the source checkout, and compare all output bytes and modes. A mocked resource
tree alone cannot satisfy installed-source acceptance.

## C5.2: verify the staged trusted assets

Extend the existing native runners to select the C5.1 staged directory without
changing its bytes. Use production Awsh and its literal helper path, fixed
rcfile/inputrc, qualified table and terminal/locale tree. Exercise startup hooks,
reserved bindings, source/start markers, post-PS0 release, split sentinel,
framing and argument-free non-returning fail-stop on all advertised targets.
Fault controls replace each asset only in a disposable isolated copy and prove
rejection. Retain stage identity before and after execution. This closes staged
asset behavior; final composed-image launch remains C6.3 and real bootstrap D3.2.
Do not add another qualifier or count the shared C4 harness implementation again.

## C6: compose first, validate without repair

C6.1 adds `src/omegaflow/reploy_blueprints.py` and typed configuration groups
under `src/omegaflow/conf/reploy`. Register their structural dataclasses through
the existing `studio_config.py` ConfigStore. Cover the complete selected public
Reploy blueprint structure, using Reploy's own typed source when available and
only a generic temporary structural model otherwise. Reploy remains the semantic
validator. Reject unknown structural fields; do not silently drop an application's
valid fields or copy OmegaFlow policy into the generic model.

Hydra composes the application, then reserved mapping entries. Lists obey Hydra
replacement semantics. Preserve Reploy `{{ ... }}` strings. Resolve OmegaConf
interpolation once into complete models and serialize native YAML into private
`reploy/blueprints/{controller,workload}.blueprint.yaml`. Retain bytes and digests;
pass those exact files to Reploy. Never patch models or YAML after composition.
Controller configuration is read-only. Test defaults, explicit overrides,
missing values, list replacement, unknown fields and byte-stable materialization.

C6.2 owns the normative launch-name enumeration in the environment design.
Materialize the effective image-plus-application environment before reserved
values, reject every forbidden exact name/prefix, then compose only the exact
reserved HISTFILE, INPUTRC, TERM, TERMINFO, TERMINFO_DIRS, LC_ALL, LANG and LOCPATH
values from that authority. Re-materialize and check the final environment;
checking only a user mapping is insufficient. There is no fallback filtering
that allows an unsafe bootstrap shell to start first.

C6.3 checks the final read-only executable runtime bind, no overlapping/shadowing
mount, both private Envoy endpoints, non-root runtime identity, and the exact
writable `/run/omegaflow` environment mount plus same-named Docker tmpfs extension.
Require the selected identity can create its private session directory. Verify
the resolved `/bin/bash` bytes against a qualified entry and enforce its exact
system-rc selection. Verify every trusted asset and manifest against the staged
tree. Bind these checks to the exact generation used at invocation; a successful
check of an earlier build or tag does not authorize the current generation.
Tests cover every forbidden family, image-inherited controls, mount ancestry,
read-only/missing tmpfs, identity, unsupported Bash, changed assets and generation
drift. Reuse the generated host table; do not maintain a handwritten allowlist.

### RP-C6: public Reploy inspection and admission prerequisite

The contracts require OmegaFlow to inspect the environment and Bash that Reploy
will actually launch before bootstrap. That requires a supported way to identify
the selected immutable image/generation, effective environment, Bash bytes and
effective mounts, and to prevent a different generation being used afterward.
Neither blueprint-only checks nor Docker inspection of a guessed image satisfies
this requirement.

The inspected installed Reploy is 0.8.0.dev5. Its public `verify` audits a prepared
build, but its help does not promise this consumer handoff. Public `info` reports
`image: built`, not the immutable image identity. The retained C1 corpus comes
from 0.8.0.dev2 at `7dfc956ceaa18638260d380f53d12bc4701dcda2`; it establishes the
controlled-session stream, not this missing preparation API. Those observations
are evidence of an unresolved interface, not proof that Reploy cannot provide it.

The owner approved adding RP-C6 as a separate public Reploy prerequisite on
2026-10-28. This is an external predecessor, not a new OmegaFlow implementation
leaf, replacement campaign, or permission to change/upgrade Reploy in this task.
It owns one coherent behavior: a public consumer can inspect the exact prepared
launch generation and require that same identity at controlled-session admission.
Reploy owns the public contract, implementation, review, native drift tests and
release; OmegaFlow owns only its contract consumption and policy checks.

RP-C6 must specify and deliver these semantics through a documented public API:

1. Inspection selects one prepared deployment and returns an immutable identity
   binding the exact generation, final image digest/platform, resolved blueprint,
   runtime mount declarations and numeric execution identity. A mutable image tag,
   directory name or `image: built` status is not that identity.
2. The public preparation interface exposes the exact selected image and its
   inherited environment before OmegaFlow adds reserved composition entries.
   It then binds that image to the final prepared generation. The report
   distinguishes the selected image's inherited environment from
   application environment and final reserved composition. OmegaFlow must be
   able to evaluate the image-plus-application environment before its reserved
   entries, then check the final effective environment, for the same image.
   Report an uninspectable value as failure; do not silently omit inherited names.
   If final preparation selects a different image, reject and restart the bounded
   preparation from a fresh view; do not apply an earlier environment check.
3. Reploy provides bounded, non-executing inspection/export of resolved `/bin/bash`
   bytes and relevant declared system-rc presence/bytes, with type/path/hash
   evidence. It also reports effective mounts, targets, read/write/tmpfs semantics,
   endpoint bindings and runtime identity needed by C6.3. Inspection must not start
   a bootstrap shell, execute untrusted workload code, disclose a configured
   private environment, or grant the controller Docker access.
4. The public session invocation accepts the inspected expected identity.
   Admission compares it with the actually selected generation under Reploy's
   existing admission ownership, and rejects mismatch before workload/bootstrap
   starts. Rebuild, blueprint/environment/mount changes, executable replacement
   and generation replacement must not race between a successful check and use.
5. Response schema/version, field bounds, path/absence semantics, canonical
   identity, supported platforms and errors are public and testable. Reploy must
   validate an expected identity against its own selected state; accepting a
   caller-written report as launch authority is insufficient.

The separately scoped Reploy design must settle exact command/API spelling,
report encoding, safe file-resolution mechanism and admission locking. Do not
invent those details as though they exist in the installed release. Reuse the
existing preparation/admission machinery rather than a second build, inspection
daemon or image-selection path. If that design changes these semantics, review
the affected OmegaFlow consumer design before implementation.

RP-C6 acceptance requires public-interface tests for nominal inspection/admission,
inherited forbidden environment, resolved Bash and absent/present system rc,
mount/identity evidence, malformed/oversized reports, stale identities, and
controlled rebuild/replacement between inspection and admission. Every stale
case must prove bootstrap did not start. Retain exact release/commit, public
contract identity and native test evidence. No Reploy release is selected here.

Before RP-C6 design review, its owner must provide bottom-up implementation/test
estimates and coherent delivery cuts. Its cost is currently unknown and excluded
from OmegaFlow's slice estimates; it cannot be treated as a free small helper or
hidden in C6.2/C6.3. A large estimate returns to design review before delivery.

C4.2, C5 and C6.1 can proceed under their normal prerequisites. C6.2 and C6.3
require the reviewed RP-C6 contract, delivered compatible release and passing
consumer conformance probes before implementation. C8 launch acceptance also
waits for those proofs. OmegaFlow's implementation consumes the documented report
and expected-identity input exactly; missing/unknown/changed evidence fails closed.
Do not parse private Reploy state/broker protocols, bypass the public handoff,
upgrade Reploy silently, or weaken the pre-bootstrap checks.

## C7: bounded input and internal controller command

C7.1 adds `src/omegaflow/controller_input.py`. The schema remains
`omegaflow-controller-run-v1`: recording identity, compiled recording plan,
initial dimensions, reserved endpoint IDs, selected application endpoint IDs,
artifact policy, and declared assets with relative name, byte size and SHA-256.
The complete manifest is at most 8 MiB. No secret, host output path, endpoint
coordinate, Docker information or private socket appears in it.

Use one strict JSON envelope with required keys `schema`, `recording_id`,
`recording_plan`, `terminal`, `envoy_endpoints`, `application_endpoints`,
`artifact_policy` and `assets`; no other keys or implicit defaults. `schema` is
the literal above. `terminal` contains exactly integer `columns` and `rows` in
1–65535. `envoy_endpoints` contains exactly `terminal` and `telemetry`, whose
values are the fixed reserved endpoint IDs. `application_endpoints` is a unique
array of declared endpoint ID strings, never addresses. `artifact_policy`
contains exactly `retain_raw`, `retain_diagnostics` and `retain_partial`, all
literal true for this internal milestone. Each asset has exactly `path`, `size`
and `sha256`: path is below `assets/`, size is a nonnegative integer excluding
bool, and sha256 is 64 lowercase hex characters. `recording_id` must equal the
compiled plan's id. The byte bound includes the whole encoded envelope.

Use explicit JSON field encoding/decoding for the existing RecordingPlan and its
supported value types; do not pickle, use Python object imports, or invent a
second action language. Reject unknown/duplicate fields, unsupported variants,
nonfinite/bool numeric values, invalid dimensions and references. Validate the
decoded plan with the existing compiler/model invariants before starting a client.
For each compiled dataclass, encode exactly `{"type": CLASS, "fields": FIELDS}`;
CLASS is its existing class name and FIELDS contains all its declared dataclass
fields, including explicit default-valued fields. Do not serialize computed
properties. The reader's fixed terminal-only whitelist is RecordingPlan,
TerminalActionPlan, TerminalCheckPlan, OutputDependencyPlan, StreamRef,
StreamPosition, EventRef, JoinPlan, PaneTitlePlan, PanePlan, PaneLayoutPlan,
PaneTransitionPlan, OuterBeatTransitionPlan, PanePresentationPlan,
TerminalPaneRecordingPlan, PaneBeatPlan, OuterPaneTrackPlan, OuterBeatPlan,
NarrationAnchorPlan, NarrationWaitPlan, TextHighlightTargetPlan,
TextHighlightEffectPlan, PlayerToolbarHighlightPlan, NarrationSegmentPlan,
NarrationTakeAnchorPlan, NarrationTakeWaitPlan, NarrationTakeMemberPlan,
NarrationTakePlan and NarrationStreamPlan.
Class fields and their scalar/optional/container types come only from those
existing definitions in `recording_plan.py`. Encode tuples as arrays, enums as
their existing string values, FrozenMapping as JSON objects, and None as null.
Decode by the expected declared type and fixed whitelist, never by importing a
payload-supplied name. Extra/missing fields and a class at the wrong field reject.
Use explicit codec branches for this model, not a reusable object serializer.
Require terminal pane kinds and no browser execution/config/handoffs. Preserve
the existing plan's presentation/narration metadata and terminal cross-stream
joins; retaining that metadata does not enable audio or media processing in this
milestone. Retain terminal continuations, inspections and output dependencies in
their existing action mappings; apply existing command, reference and whole-plan
bound checks. Do not narrow existing terminal plan semantics to simplify the codec.
Round-trip fixtures cover every plan variant admitted by this terminal-only
entry, including continuations and inspection declarations. Reject unsupported
browser/audio modes before staging rather than silently dropping actions.
Asset names are unique normalized relative paths below `assets`, with no
symlink/escape/special file. Verify all declared assets and reject undeclared
payload. Reject manifest inflation before parsing/allocating unbounded content.

C7.2 stages that input in a fresh private directory using the C5 copy primitives,
without importing C5's runtime file allowlist. Compose its read-only mount only
into the controller at `/omegaflow-input`; prove it is absent from the workload.
The controller command validates `/omegaflow-input/run-manifest.json` completely
before launching `reploy-session-client`. Keep this input separate from the
initially empty controller output directory. Add the internal module command,
not a user-facing CLI or new terminal runner. The later D1 adapter consumes this
same input contract. Unit tests plus blueprint/command integration prove rejected
input never launches the session client and cannot overlap the output mount.

## C8: public deployment and lifecycle execution

C8.1 adds `src/omegaflow/reploy_session.py` as a small public CLI adapter. Use
argument arrays without shell interpolation. Check recording-backend values,
Linux/Docker, controller architecture, one attachment/no reconnect, no configured
private environment, and qualified workload support before preparation. Preserve
the existing presence-aware migration boundary: omitted workload backend stays
on FIFO; explicit host fails for this isolated entry; explicit reploy selects it.
Do not enable the production recorder while Part D remains unfinished.

Use the exact resolved YAML with public `reploy stage FILE --dir DIR` then
`reploy build --dir DIR` for separate fresh controller/workload deployments.
Retain command/result evidence and admitted identities. Never modify a prepared
deployment to repair policy or use an old prepared directory after a failed build.
On failure account for each resource created by this run using public Reploy
recovery controls; retain partial evidence. Recheck all handoff identities before
starting the session. Fake-command tests cover argv, errors, routing, retention
and each capability rejection without requiring Docker for the unit suite.

C8.2 extends this adapter and the C7 internal controller entry to run:
`reploy controlled-session run --controller-dir DIR --workload-dir DIR
--endpoint omegaflow-terminal --endpoint omegaflow-telemetry
--columns N --rows N --output-dir EMPTY_DIR -- CONTROLLER_COMMAND`.
Additional declared application endpoints are explicit grants. Select existing
documented timeout values; do not introduce a second timeout policy or reset a
captured deadline. Reploy's broker attachment deadline remains independent.

Inside the controller, execute C2's effects using only `reploy-session-client
client` and `attach --socket PATH`. Start attach promptly at broker-ready, keep
draining its diagnostic PTY, obtain trusted endpoints only from opened, and type
the fixed Envoy bootstrap only after ready. Connect the fixed selected pair
through C3's transport/handshake; never reconnect or accept input-supplied endpoints.
Serialize C2/C3 decisions through one owner. Transport I/O stays asynchronous;
do not add another lifecycle state machine or infer success from process exit.

Keep one internal async function, `run_controller_session(input, output, driver)`,
as the lifecycle owner. It validates input, opens evidence sinks, executes C2
effects, attaches/bootstraps C3 and gives the driver the existing EnvoyController
only after readiness. The driver submits ordinary operations; it cannot own the
Reploy client, close evidence or bypass lifecycle serialization. On driver return
the owner requests orderly idle shutdown and completes teardown; driver failure
enters the existing failure/termination path. D1 will provide the recording-plan
driver through this same call boundary, with no new transport or plugin registry.
Before D1, C7's internal module command supports validation and rejects attempted
recording execution with a specific not-yet-supported result. C8's declared
test-controller command calls the production session function with a bounded
test-only driver for nominal/fault cases. That proves actual production lifecycle
execution through public Reploy; it does not claim the recording command or a
general RecordingPlan executor is implemented. D1 owns enabling that execution
branch and feeding the validated plan to the driver.

Retain separate public events, client stderr, outer bootstrap diagnostics, raw
Envoy bytes and failure metadata in controller output. After a fatal Envoy outcome,
execute the existing termination effect, retain the request and resulting events,
close partial artifacts, and follow C2's complete/terminated/acknowledgement order.
The startup-failure branch acknowledges terminated without waiting for events
that cannot occur. A failed writer, missing result, failed acknowledgement,
nonclean client exit or incomplete teardown remains failure. The authoritative
host result passes the existing strict host-result codec; stderr and exit status
alone never establish success. Preserve bounded in-memory diagnostics and stream
full evidence to files using the existing ControllerSession sinks.

Acceptance needs actual public Reploy execution with production Envoy/Awsh and
the installed/staged package: nominal closure, startup failure, fatal Envoy
termination, partial-output retention, client/result/acknowledgement failure and
accounted teardown. Faults use existing isolated fixture seams; no private broker
protocol. This is a bounded controller/session harness, not D1's recording adapter
or D3's full tutorial/curses/browser/hostile-bootstrap acceptance.

## Acceptance ownership and honest readiness

Each leaf retains focused tests, full relevant CI, actual production/test/other
changed-line counts, review rounds, external waits and nonoverlapping elapsed
time. A later real integration test may expose an earlier defect; correct it in
its owning successor and invalidate affected evidence, rather than relaxing the
test or rebuilding an already approved PR. Preserve every original proof ID.

C4 closes artifact identity; C5 closes installed-source staging and asset behavior;
C6 closes exact final composition/launch preflight; C7 closes input isolation;
C8 closes public invocation and host-result/termination behavior. Part D still
owns runner adaptation, casts/timelines and the complete isolated terminal demo.
No partial harness closes those later gates.

No OmegaFlow slice split is adopted by this design. The installed artifact bridge
is explicitly costed in C5.1. RP-C6 is the owner-approved separate Reploy
predecessor; its interface design, cost and delivery are pending. This resolves
the design ownership decision, not the underlying capability. C6.2/C6.3 and
downstream launch remain blocked until its stated acceptance is satisfied.
Adopt approved changes through the single catalogue before changing the campaign.
Local design stamps and a remote approved design PR do not manufacture RP-C6
implementation, select a new Reploy release or establish all-C execution readiness.
