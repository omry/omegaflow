# Browser Recording Detailed Design

## Status

Implemented for the constrained `desktop-v1` capture profile, shared
terminal/browser timeline, browser renderer, CLI, documentation, and secure
publishing surfaces described here. Phase 0 selected five bounded visual
policies: stable-state detection, text-overlay eligibility, scroll synthesis,
dynamic-fragment capture, and dynamic redaction. Automated desktop and
responsive mobile-emulation playback validation passes. Physical-device
playback and the provisional decoded-asset memory budget remain later
release-stage work.

## Scope

Phase 1 adds browser and mixed terminal/browser recordings while preserving
the current terminal-only workflow. The implementation includes:

- strict browser authoring schemas
- one source-ordered capture coordinator with persistent terminal and browser
  runners
- a private semantic browser capture log and private diagnostic artifacts
- beat-local terminal and browser presentation payloads
- narration takes that may span contiguous beats
- a versioned global presentation manifest
- a media-neutral player shell with terminal and browser renderers
- atomic, allowlist-based publishing through the existing surfaces

The non-goals and browser support matrix remain those in the HLD.

## Design constraints

The following invariants are normative:

1. A beat has exactly one `medium`: `terminal` or `browser`.
2. Source order is execution order. Capture does not reorder beats or actions.
3. A runner persists for the recording once started. Only the active runner is
   captured; inactive runners retain state.
4. Every visual event is stored in beat-local integer milliseconds, starting
   at zero.
5. The global manifest assigns one offset and duration to each beat. Published
   events never use capture-clock timestamps.
6. Browser pixels are playback truth. Locators and DOM observations are
   capture inputs and private diagnostics, never a replay format.
7. Narration may be synthesized across contiguous beats, but visual beats stay
   independently addressable and relocatable.
8. Capture-only checks may fail a build but never become presentation events or
   synchronization targets.
9. The public bundle is generated from an explicit allowlist. A private
   capture artifact cannot become public merely because another artifact
   references it.
10. An unknown manifest version, renderer payload version, or event kind is a
    hard player error.

All time intervals are half-open: `[start_ms, end_ms)`. At an exact beat
boundary the later beat is active. The final recording duration is inclusive
for seeking and exclusive for event selection.

## Integration with the current implementation

The implementation uses one pipeline for every recording type:

| Current area | Phase 1 change |
| --- | --- |
| `studio_config.py` dataclass schema | Add recording-level browser and presentation config, `medium`, `narration_take`, and modality-specific action validation |
| terminal execution | Use the persistent terminal runner as the sole executor |
| `audio.py` one TTS segment per beat | Normalize narration takes before planning audio and publish take/member metadata |
| `audio.py` guide payload requires commands | Preserve terminal command guides and allow `success_hint`-only payloads for browser beats |
| presentation compiler | Solve media-neutral timing and emit beat-local terminal and browser payloads |
| `studio.py` | Build and publish the allowlisted presentation bundle |
| `cast-player.html` | Load the presentation manifest and switch renderer adapters on one clock |
| `cast-player-embed.js` and `VideoPlayer` | Require the presentation manifest input |

A terminal-only recording runs through the same capture, compilation, play,
and publish path as browser and mixed recordings.

### Canonical terminal executor migration

Every app-level terminal beat is executed by the persistent terminal runner.
The presentation compiler consumes its beat-local captures directly; it does
not reconstruct a recording-wide cast or timeline.

The migration is complete only when parity tests cover:

- persistent shell state, including working directory, exports, functions,
  aliases, and environment path precedence
- isolation and bounded cleanup for `exit`, syntax errors, `set -e`, traps, and
  background processes
- real, suppressed, and replacement output; prompt rendering; typing, pause,
  and follow-along timing; captions; and viewer holds
- command checks, failure diagnostics, project setup and cleanup, guide data,
  watch behavior, and terminal-only publishing

After parity is established, delete the monolithic executor and its oracle-only
tests. Commit history is the reference for the former implementation; the live
tree has one terminal execution implementation.

## Authoring schema

### Recording header

Browser capture and browser presentation defaults are recording-level header
concerns:

```yaml
---
id: create-project
title: Create a project
browser:
  profile: desktop-v1
  base_url: http://127.0.0.1:3000
  auth:
    storage_state_env: OMEGAFLOW_BROWSER_AUTH_STATE
  timeouts:
    action_ms: 10000
    readiness_ms: 15000
  redactions:
  - target:
      test_id: account-email
presentation:
  browser:
    window:
      mode: framed
      theme: kde-breeze
      title: OmegaFlow
      opening_transition: window-open
    chrome:
      mode: full
    transitions:
      default: fade
    pointer:
      visible: true
    typing:
      policy: natural-v1
---
```

`browser` is required when any beat uses `medium: browser`. `profile` defaults
to `desktop-v1`; authors may write it explicitly to make the capture contract
obvious.

### Capture profiles

`desktop-v1` is an immutable named profile with these resolved values:

- the Phase 1 pinned headless Chromium build
- a 1440 by 900 CSS-pixel viewport and matching screen
- device scale factor 1
- `en-US` locale and UTC timezone
- light color scheme and reduced motion
- no granted permissions
- desktop mode with touch disabled
- the pinned browser build's default user agent
- webpage audio muted

OmegaFlow may change these defaults only by introducing another profile name,
such as `desktop-v2`. Phase 1 has no other capture profile.

A recording may override `viewport` and `context`. Viewport width and height
must be provided together; device scale factor may be overridden separately.
Context overrides are limited to locale, timezone, color scheme,
reduced-motion preference, and permissions. Headless mode, browser engine,
user agent, desktop/touch mode, and page-audio muting are fixed by
`desktop-v1`.

The runner materializes the complete resolved profile before capture. The
profile name and every resolved value enter the capture fingerprint, including
values the author did not override.

`base_url` is optional. Relative `open_page.url` values require it. Capture
URLs are private build inputs. They are never inferred as public display URLs.

The viewport uses CSS pixels. `device_scale_factor` controls raster density but
does not alter target coordinates or player layout. Phase 1 requires desktop
mode and rejects touch/mobile context settings.

`auth.storage_state_env` and `auth.storage_state_path` are mutually exclusive.
The environment form contains the name of a variable whose value is a local
path, not the storage-state JSON itself. The path is resolved at capture time
and is never copied into the public bundle.

`redactions` are recording-wide target masks. A secret input also creates an
automatic action-scoped redaction for its target.

### Beats

`medium` defaults to `terminal` as the authoring-schema default.
`narration_take` is optional. Its absence creates an internal singleton take
for that narrated beat.

```yaml
beats:
- id: start-server
  medium: terminal
  heading: Start the application
  narration: Start the local service.
  actions:
  - run: ./scripts/start-demo.sh
    name: start demo server
    expect:
      contains: ready

- id: create
  medium: browser
  heading: Create a project
  narration_take: project-creation
  narration: >-
    Open the project menu and @choose_new@ select New project.
    @wait:dialog_ready+300ms@ Enter a project name and submit it.
  guide:
    success_hint: The dialog opens after selecting New project.
  actions:
  - id: open_app
    open_page:
      url: /projects
      display_url: https://app.example.com/projects
      loading: hide
      ready:
        visible:
          role: main
  - id: open_menu
    click:
      target:
        role: button
        name: Project menu
  - id: choose_new
    click:
      target:
        role: menuitem
        name: New project
    after: "@choose_new@"
  - id: dialog_ready
    wait_for:
      visible:
        role: dialog
        name: Create project
  - id: enter_name
    fill:
      target:
        label: Project name
      text: Example project
  - id: submit
    press:
      key: Enter
  checks:
  - name: project was created
    url:
      equals: /projects/example-project
```

Browser beats reject terminal step keys such as `run`, `run_file`, `commands`,
and terminal `guide.commands`. Terminal beats reject browser action kinds.
`guide.success_hint` is valid for either medium; browser beats display only the
explanation and Continue/Restart controls.

### Browser action envelope

Every browser action has:

- a recording-wide unique `id`
- exactly one action-kind key
- optional `after`, `hold_after_ms`, `transition`, and `display_url_after`

The action-kind keys are `open_page`, `click`, `fill`, `type_keys`, `press`,
`scroll`, and `wait_for`. Unknown keys fail source validation.

The first browser action in the resolved presentation must be `open_page`.
Later browser beats reuse the persistent page and need not open it again.
Authors who need an intentionally blank initial page use
`open_page.url: about:blank`.

`after` contains one narration anchor in the form `"@anchor_id@"`. Anchor IDs
remain beat-scoped for compatibility with current terminal recordings, so an
action resolves `after` only against narration authored in the same beat.
`hold_after_ms` is a non-negative presentation hold and does not sleep during
capture. `transition` is one of `cut`, `fade`, or `captured`; `captured`
retains the action's dynamic fragment through action completion. On a
`wait_for` action, that means capture continues until the authored condition
succeeds; the condition itself is bounded by its timeout. The final screenshot
synchronizes the retained video boundary with the completed browser frame.
Automatically selected dynamic fragments retain the short safety limit;
explicitly captured fragments may exceed it and remain subject to the
encoded-size budget.

`display_url_after` is optional public presentation metadata for an action that
changes the visible application route, such as a click that navigates. It does
not affect capture and is subject to the same validation as
`open_page.display_url`.

### Targets

A target selects exactly one locator family:

```yaml
target:
  role: button
  name: Save
  exact: true
```

The supported families are:

| Family | Fields | Publication behavior |
| --- | --- | --- |
| role | `role`, optional `name`, optional `exact` | Locator remains private |
| label | `label`, optional `exact` | Locator remains private |
| placeholder | `placeholder`, optional `exact` | Locator remains private |
| text | `text`, optional `exact` | Locator remains private |
| test id | `test_id` | Locator remains private |
| CSS | `css` | Private and emits a portability warning |
| XPath | `xpath` | Private and emits a portability warning |

An action fails when its target resolves to zero or multiple elements unless
the action kind explicitly permits multiple matches. Phase 1 action kinds do
not permit multiple matches. Coordinates are never valid authoring targets.

### Action payloads

`open_page` fields:

- `url`: required capture URL, absolute or relative to `browser.base_url`
- `display_url`: optional public URL shown by generated browser chrome
- `lifecycle`: `domcontentloaded` by default, or `load`
- `ready`: optional condition evaluated after the lifecycle boundary
- `loading`: `hide` by default, or `show`
- `timeout_ms`: optional positive override

When full browser chrome is enabled, every `open_page` must provide a
`display_url`. It must be HTTP(S) or exactly `about:blank`, must not contain
user information, and must pass the public secret scan.

`loading: hide` starts the published action at the first ready visual state.
`loading: show` retains the interval from navigation dispatch through readiness
using the spike-selected frame or clip path. If that path cannot faithfully
capture the loading interval, the action fails rather than silently behaving
like `hide`.

The renderer retains the last public display URL until another `open_page` or
`display_url_after` changes it. It never derives an address-bar value from a
captured URL.

`click` fields:

- `target`: required
- `button`: `left` by default, or `middle`/`right`
- `position`: `center` by default, or `{x, y}` relative to the target

`fill` and `type_keys` fields:

- `target`: required
- exactly one of `text` or `secret`
- `type_keys.capture_delay_ms`: optional non-negative execution pacing

A secret reference has this shape:

```yaml
secret:
  env: DEMO_PASSWORD
  presentation: placeholder
  placeholder: correct horse battery staple
```

`presentation` is `masked`, `placeholder`, or `omitted`. `placeholder` requires
a non-secret public value. The resolved secret is used only by the live page
and the private pre-publish scanner. It never enters a timeline or diagnostic
message. `fill` never falls back to `type_keys`; the author chooses the latter
when the application requires keyboard events.

`press` accepts a normalized `key` such as `Enter`, `Escape`, or
`Control+Shift+P`, plus an optional target to focus first.

`scroll` accepts exactly one destination:

- `target`: scroll that element into view
- `by: {x, y}` with an optional `container` target
- `to: {x, y}` with an optional `container` target

`wait_for` and `open_page.ready` accept exactly one condition:

- `visible` or `hidden`: a target
- `url`: `{equals|contains|matches: value}`
- `response`: URL matcher plus optional method and status

Each condition has an optional `timeout_ms`. URL values beginning with `/` are
matched against path and query; absolute values are matched against the whole
URL. Regular expressions use Python syntax during capture and are not
published.

### Checks

Checks have a required `name` and exactly one of:

- `url`
- `visible`
- `hidden`
- `text`
- `value`
- `count`
- `response`

Text and value checks contain a target and one of `equals`, `contains`, or
`matches`. Count checks contain a target and a non-negative `equals`. Response
checks query the private response registry maintained throughout capture.
Checks have no action ID, cannot be named in `@wait`, and are absent from the
presentation payload.

## Schema loading and normalization

OmegaConf structured dataclasses remain the sole schema authority for both
authoring and generated artifacts. JSON is a serialization format, not a
second schema system; the implementation does not introduce JSON Schema.

The existing structured dataclass validation remains the first authoring pass.
Because terminal and browser action mappings have different shapes, `actions`
becomes `list[Any]` only at the outer dataclass boundary. A required second pass
applies the strict action dataclass selected by `beat.medium`; this is not an
escape from unknown-key validation.

Generated formats have versioned dataclasses such as
`PresentationManifestV1`, `BrowserPayloadV1`, and one dataclass per browser
event kind. Writers instantiate those dataclasses and serialize the resolved
OmegaConf container to JSON.

Generated-artifact readers first inspect the raw version and event-kind
discriminators. For a recognized version and kind, a compatibility projection
discards fields unknown to that reader before OmegaConf structured validation.
Missing required fields, invalid known fields, unknown versions, and unknown
event kinds remain errors. This permits a newer writer to add an optional field
without changing the version while keeping current authoring and compiler
output strict.

Adding an optional generated field is compatible within a version. Removing a
field, making an optional field required, changing field meaning, or adding an
event kind requires a new payload or manifest version. The JavaScript player
implements the same required-field and discriminator contract, ignores unknown
fields, and is tested against fixtures serialized from the Python dataclasses.

Normalization produces an immutable `RecordingPlan` before any external tool
runs:

```text
RecordingPlan
  recording config and capture profile
  ordered BeatPlan[]
    id, medium, heading, guide
    narration member reference
    typed TerminalAction[] or BrowserAction[]
    typed capture-only checks
  ordered NarrationTakePlan[]
  presentation defaults
```

The normalizer performs all cross-reference checks:

- recording and beat IDs are valid and unique
- browser action IDs are unique across the recording
- action kinds match the beat medium
- narration anchor IDs are unique within their beat
- `after` resolves to an anchor in the same beat
- narration waits resolve to an action or terminal command ID in the same beat
- checks are not synchronization targets
- target and condition unions contain exactly one variant
- secret references and presentations are valid
- browser configuration exists when required
- narration takes are contiguous

No capture runner reads the original untyped dictionaries.

## Narration takes

### Normalization

Every narrated beat receives a resolved take ID. An explicit
`narration_take` is used as written. Otherwise the internal ID is
`__beat__:<beat-id>`.

Contiguity validation scans the complete resolved sequence after singleton IDs
have been assigned. A take is fragmented when its ID appears, another take
appears, and the first ID later reappears. This single rule covers explicit and
implicit takes without separate validation paths.

Each `NarrationTakePlan` contains ordered members:

```text
take id
explicit flag
voice/model/format/settings
ordered beat ids
concatenated synthesis text
per-member character range
anchors and waits expressed as take-text offsets
```

Member text is joined with one normalized space. The member character ranges
refer to that exact synthesized string.

### Cache identity and reorder warning

The take cache key hashes:

- provider, model, voice, format, instructions, and timing settings
- ordered beat IDs
- normalized member texts with their boundaries
- the generated synthesis text

The cache sidecar stores the take ID and ordered member IDs. If an existing
sidecar has the same explicit take ID with a different member order, the build
continues but emits `NARRATION_TAKE_REVIEW`. The changed key prevents reuse of
the old audio.

### Published audio metadata

Audio generation publishes one content-addressed file per take. `audio.json`
uses version 3 for manifest recordings and identifies every take file by path
and SHA-256 digest:

```json
{
  "version": 3,
  "recording": "create-project",
  "duration_ms": 7800,
  "takes": [
    {
      "id": "__beat__:start-server",
      "src": "audio/__beat__-start-server-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.mp3",
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "source_start_ms": 0,
      "source_end_ms": 2600,
      "timestamps": "timestamps/start-server.json",
      "members": [
        {
          "beat_id": "start-server",
          "text": "Start the local service.",
          "text_start": 0,
          "text_end": 24
        }
      ]
    },
    {
      "id": "project-creation",
      "src": "audio/project-creation-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.mp3",
      "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "source_start_ms": 2600,
      "source_end_ms": 7800,
      "timestamps": "timestamps/project-creation.json",
      "members": [
        {
          "beat_id": "create",
          "text": "Open the project menu and select New project.",
          "text_start": 0,
          "text_end": 45
        }
      ]
    }
  ]
}
```

### Take timestamp sidecar

Each narration take has one media-independent timestamp sidecar. It maps the
take's synthesized text to take-local source-audio milliseconds:

```json
{
  "version": 1,
  "take_id": "project-creation",
  "duration_ms": 5200,
  "members": [
    {
      "beat_id": "create",
      "text_start": 0,
      "text_end": 45,
      "source_start_ms": 0,
      "source_end_ms": 5200
    }
  ],
  "words": [
    {
      "text": "Open",
      "text_start": 0,
      "text_end": 4,
      "start_ms": 0,
      "end_ms": 310
    }
  ],
  "anchors": [
    {
      "beat_id": "create",
      "id": "choose_new",
      "text_offset": 26,
      "source_ms": 2740
    }
  ],
  "waits": [
    {
      "beat_id": "create",
      "target": "dialog_ready",
      "text_offset": 45,
      "source_ms": 5200,
      "gap_ms": 300
    }
  ]
}
```

Member ranges partition the exact synthesized text in take order. Word ranges
refer to that text, and all source times are monotonic and bounded by
`duration_ms`. Every anchor and wait belongs to one member and uses both its
text offset and resolved source-audio time. Failure to resolve a marker to an
audio time is a build error.

The sidecar does not contain presentation pauses or global offsets. The
presentation compiler derives those from waits and visual action completion.
It supports word highlighting, anchors, waits, and member boundaries for
terminal, browser, and mixed takes alike.

An implicit singleton terminal take writes the same versioned take sidecar as
an explicit narration take. Public metadata contains no synthesis cache paths.

## Capture coordinator

### Lifecycle

`CaptureCoordinator.capture(plan, run_dir)` owns the recording lifecycle:

1. Create a staged run directory and the recording-scoped environment.
2. Start the terminal runner when setup, cleanup, or a terminal beat requires
   it.
3. Run project-authored setup once with terminal capture hidden.
4. Start the browser runner on the first browser beat.
5. Dispatch beats in source order to the matching persistent runner.
6. Run each beat's capture-only checks before advancing.
7. Close the browser page/context/browser and materialize optional diagnostics.
8. Run project-authored cleanup once through the still-live terminal runner,
   even when setup, a beat, a check, or browser close fails.
9. Close the terminal runner.
10. Tear down the recording-scoped environment after both runners close and
    mark the private capture log complete only after successful cleanup.

Cleanup failure is reported in addition to the primary failure and makes the
run unsuccessful. It never replaces the primary error.

Project cleanup and coordinator teardown are distinct. Project cleanup runs in
the persistent shell so it can use shell-local state. Coordinator teardown
releases the remaining process, filesystem, and network resources after both
runners have closed. If the terminal runner never started successfully,
project cleanup is reported as unavailable rather than attempted in a fresh
shell with different state.

The shared environment fixes the working directory, initial environment map,
filesystem workspace, temporary directory, and network namespace before either
runner starts. A shell-local `cd` or `export` persists in later terminal beats
but cannot mutate the parent process or an already-created browser process.
Cross-modality state therefore flows through files, processes, services, and
network endpoints created in the shared environment, not through reverse
propagation of shell-local variables.

### Runner contract

```python
class CaptureRunner(Protocol):
    def start(self, context: CaptureContext) -> None: ...
    def capture_beat(self, beat: BeatPlan) -> BeatCapture: ...
    def close(self) -> None: ...
```

`start` and `close` are idempotent. `capture_beat` may be called only in source
order and never concurrently. A runner writes private artifacts only under its
assigned staged run directory.

### Terminal runner

Mixed capture still uses one persistent shell and one asciinema process. The
current generated Bash helpers are split into a reusable session program that
accepts beat requests over a local control channel and returns structured
completion messages. Asciinema wraps that session for the recording lifetime.

The control channel is two newline-delimited JSON streams created below the
private staged run directory with user-only permissions. The coordinator sends
one request at a time:

```json
{"seq": 3, "op": "beat", "beat_id": "start-server", "actions": []}
```

`op` is `setup`, `beat`, `checks`, `cleanup`, or `shutdown`. The session replies
with the same `seq`, `status` (`started`, `completed`, or `failed`), and private
timeline references. It never returns rendered terminal output over the control
stream; stdout and stderr continue through the PTY into asciinema. A failed or
malformed response terminates capture. On cancellation, the coordinator sends
`shutdown`, allows a five-second internal grace period, and then terminates the
session process. The streams are deleted after both processes close.

Setup, cleanup, terminal checks, and intervals while browser beats run are
marked hidden in the terminal timeline. After capture, the terminal compiler
uses beat markers to extract each terminal beat into a local-time cast. The
cast header is copied, event deltas are rebased to zero, and output outside the
beat's visible intervals is excluded. This preserves terminal state during
capture without making the physical cast a composition boundary.

### Browser runner

The browser runner uses Playwright's synchronous Python library in the
coordinator process. It creates one pinned Chromium browser, one context, and
one active page. The context and page persist across browser beats.

Context creation resolves every deterministic setting explicitly:

- viewport and device scale factor
- locale and timezone
- color scheme and reduced-motion preference
- permissions
- user agent and screen size
- storage state
- webpage audio mute policy

The page is muted at browser launch and dynamic fragments are verified to have
no audio stream. Console, page-error, request-failure, and response summaries
go to private diagnostics with headers, bodies, credentials, and known secret
values removed.

The browser runner registers network observations before the first navigation.
It records only the origin and result category needed for reproducibility
warnings. Any non-loopback origin emits one non-blocking
`EXTERNAL_NETWORK_CAPTURE` warning per origin.

## Browser action execution

For every action the runner:

1. Resolve and strictly validate the locator, when present.
2. Capture target bounds and a deterministic interaction point.
3. Record the private pre-action visual state when required by the action.
4. Execute the Playwright operation with its authored timeout.
5. Await only the action's defined execution boundary.
6. Observe the visual result using the selected Phase 0 stability policy.
7. Capture the post-action state or dynamic fragment classification.
8. Append a completed action record to the private JSONL log.

Execution behavior and presentation output are intentionally separate:

| Action | Live execution boundary | Presentation fact |
| --- | --- | --- |
| `open_page` | Navigation lifecycle plus optional authored `ready` | State change, optional retained loading, public display-URL update, completion milestone |
| `click` | Playwright click completes | Pointer path, click feedback, optional public display-URL update, resulting state/fragment |
| `fill` | Field value is set and input events complete | Focus and retimed character reveal |
| `type_keys` | Authored key sequence completes | Same focus and retimed character reveal |
| `press` | Key/chord dispatch completes | Shortcut badge or key feedback and resulting state |
| `scroll` | Requested live scroll completes | Synthesized scroll or captured fragment |
| `wait_for` | Condition becomes true | Invisible completion milestone and resulting stable state |

An action completion milestone says that the action's presentation has
finished. It does not claim an unmentioned asynchronous application result is
ready. Authors use `wait_for` for that result.

The response registry assigns an increasing sequence to sanitized response
summaries. `open_page.ready.response` searches from navigation dispatch.
`wait_for.response` searches from the start of the immediately preceding
action, which includes a response that completed during a click. A response
check searches only the current beat, so `wait_for.response` cannot be the
first action in a beat. Other `wait_for` conditions may be first because they
inspect current page state. This scoping prevents an old matching response
from satisfying a later condition accidentally.

Before declaring a state stable, the runner also waits for loaded fonts and
decoding of visible images, bounded by the action readiness timeout. Continuous
motion selects the spike-defined dynamic fallback or fails with an actionable
diagnostic; it is never silently labeled stable.

## Private capture log

`capture/browser.capture.jsonl` is append-only so a failed run retains evidence.
Each line has `capture_version`, a monotonically increasing `seq`, and a
`type`. Record types are:

- `run_start` and `run_end`
- `beat_start` and `beat_end`
- `action`
- `check`
- `warning`
- `diagnostic`

Action records use beat-local capture time and private references:

```json
{
  "capture_version": 1,
  "seq": 7,
  "type": "action",
  "beat_id": "create",
  "action_id": "open_menu",
  "kind": "click",
  "execution": {"start_ms": 1142, "end_ms": 1231},
  "target": {
    "locator": {"role": "button", "name": "Project menu"},
    "bounds": {"x": 1310, "y": 24, "width": 48, "height": 36},
    "point": {"x": 1334, "y": 42}
  },
  "before_state": "capture/states/sha256-before.png",
  "after_state": "capture/states/sha256-after.png",
  "completion": {"kind": "stable_state", "policy": "stable-v1"}
}
```

The compiler accepts only a log ending in a successful `run_end`. Unknown
record types fail compilation. The capture log never enters the public asset
directory.

## Visual assets

Stable states are captured as lossless PNG, masked, normalized, and encoded as
lossless WebP for publication. The published filename is the SHA-256 of the
encoded bytes. Equal states therefore deduplicate across actions and beats.

Dynamic content is represented by the manifest's generic `clip` asset kind.
Phase 0 selected Playwright context video as `playwright-video-v1`, with VP8 in
WebM and no audio. Action windows are frame-accurately trimmed to a new VP8
WebM whose first frame is independently decodable. CDP JPEG screencast frames
are diagnostic-only: the reference capture used about 8 MB for roughly 130
JPEG frames versus about 230 KB for the trimmed, seekable 1.4-second Playwright
video. Phase 1 limits automatically selected clips to 3 seconds and every clip
to 2 MB encoded. An explicit `transition: captured` may exceed 3 seconds because
its action completion and timeout provide the primary duration bound. Capturing
the synchronized final frame may extend the retained boundary beyond action
completion. The compiler interface remains:

```text
ClipAsset
  content hash and media type
  width, height, duration_ms
  source capture start/end
  presentation trim start/end
  has_audio = false
```

The published player supports only the selected Phase 1 combination. There is
no codec or feature negotiation in authoring files.

All overlay geometry uses capture CSS pixels. Raster pixel dimensions equal
CSS dimensions multiplied by device scale factor. The renderer scales the
raster and geometry together, so the page never reflows during playback.

## Presentation compilation

### Inputs and outputs

The compiler consumes the normalized plan, successful private capture logs,
terminal baseline media, narration-take audio metadata, and selected visual
policies. It emits:

- one beat-local terminal cast or browser payload per visual beat
- one global presentation manifest
- public audio and timestamp metadata
- content-addressed WebP and clip assets
- a private compilation report containing decisions and warnings

### Constraint model

The compiler first builds a recording-wide directed acyclic constraint graph.
Nodes include beat boundaries, action starts and completions, narration
anchors, narration wait points, and take-member boundaries.

Constraints are:

- actions in a beat preserve source order
- an action starts no earlier than the completion of its predecessor
- `after: "@anchor@"` adds `action_start >= anchor_time` using the action's
  beat-scoped narration anchor
- `@wait:action_id+gap@` pauses narration until the referenced action or
  terminal command completion plus the gap; its target is in the same beat
- `hold_after_ms` extends an action's completion
- a beat ends after all its actions and its narration member
- `viewer_hold` extends the beat after both visual and narration work finish

Cycles are source errors and report the shortest discovered dependency chain.
The solver produces integer milliseconds; fractional media timestamps are
rounded once at input using round-half-up.

### Cross-beat narration takes

A take's audio source is continuous across its members. Timestamp metadata
maps each member boundary and anchor to a take-local source-audio time. The
presentation audio map may pause only at authored `@wait` markers.

For two adjacent members, the beat boundary is the presentation time at which
the audio source reaches the second member's first word. All visual events of
the first beat must complete by that boundary. Otherwise compilation fails and
asks the author to add an explicit narration wait, change action pacing, or
rewrite the take. The compiler does not insert an arbitrary silent pause that
would damage the natural take.

If a member's visuals finish early, its final state remains visible until the
member boundary. The last member ends at the later of its take end and visual
completion. This preserves uninterrupted speech while keeping one active
visual beat at every global time.

### Beat materialization

After solving global constraints, the compiler subtracts each beat offset from
its visual event times. Every materialized event must satisfy:

```text
0 <= event.at_ms <= event.end_ms <= beat.duration_ms
```

Moving a compiled beat changes only its global manifest offset unless a
recording-wide dependency, shared narration take, or stateful capture ordering
requires recompilation. The compiler reports those dependencies explicitly.

### Deterministic animation policies

Pointer animation `pointer-v1` uses the previous pointer position and captured
target point. Duration is clamped between 220 and 900 milliseconds as a
function of Euclidean distance. A cubic Bézier curve receives a small
perpendicular offset whose direction is derived from a stable hash of recording
ID, beat ID, and action ID. The same input always produces the same path.

Text animation `natural-v1` uses the published text or safe secret
presentation, never capture pacing. It applies character-class intervals,
longer punctuation and whitespace pauses, and bounded acceleration for long
values. The policy version and resolved timing parameters are stored in the
browser payload.

`input-overlay-v1` permits an overlay only for a plain or controlled
single-line input whose captured style contract is complete and whose `fill`
and keyboard paths resolve to the same public value. Textareas, formatted
inputs, contenteditable, and any field with unresolved clipping, wrapping,
selection, caret, or formatting use captured states or a clip. The reference
single-line overlay measured SSIM 0.999971 against the captured field.

`scroll-v1` synthesizes only a nested container with no video/canvas,
animation, sticky/fixed descendant, virtualization, or scroll-linked behavior.
Every other scroll uses `playwright-video-v1` or fails when the clip budget or
redaction policy makes that fallback unavailable.

## Browser beat payload

Each browser beat has a separate JSON payload. It contains only publish-safe
facts and references assets by manifest asset ID:

```json
{
  "payload_version": 1,
  "beat_id": "create",
  "duration_ms": 5200,
  "viewport": {
    "width": 1440,
    "height": 900,
    "device_scale_factor": 1
  },
  "initial_state": "state-before",
  "initial_pointer": {"x": 720, "y": 450, "visible": true},
  "initial_display_url": "https://app.example.com/projects",
  "animation_policies": {
    "pointer": "pointer-v1",
    "typing": "natural-v1"
  },
  "events": [
    {
      "kind": "pointer_move",
      "action_id": "open_menu",
      "at_ms": 600,
      "end_ms": 1040,
      "start": {"x": 720, "y": 450},
      "end": {"x": 1334, "y": 42},
      "curve": {"x1": 850, "y1": 400, "x2": 1200, "y2": 90}
    },
    {
      "kind": "click",
      "action_id": "open_menu",
      "at_ms": 1040,
      "end_ms": 1160,
      "point": {"x": 1334, "y": 42},
      "button": "left"
    },
    {
      "kind": "state",
      "action_id": "open_menu",
      "at_ms": 1160,
      "end_ms": 1340,
      "asset": "state-after",
      "transition": "fade"
    },
    {
      "kind": "complete",
      "action_id": "open_menu",
      "at_ms": 1340,
      "end_ms": 1340
    },
    {
      "kind": "display_url",
      "action_id": "open_menu",
      "at_ms": 1340,
      "end_ms": 1340,
      "value": "https://app.example.com/projects/example-project"
    }
  ]
}
```

Phase 1 event kinds are `state`, `pointer_move`, `click`, `focus`, `text`,
`key`, `scroll`, `clip`, `display_url`, and `complete`. Each kind selects one
OmegaConf structured event dataclass. Event arrays are sorted by `at_ms`, then
source action order, then this fixed kind priority from first to last:
`state`, `clip`, `scroll`, `focus`, `text`, `key`, `pointer_move`, `click`,
`display_url`, `complete`. The priority is a deterministic serialization
tie-breaker; event intervals and renderer reconstruction rules remain the
playback semantics.

Every event has `kind`, `action_id`, `at_ms`, and `end_ms`. Kind-specific
fields are:

| Kind | Required fields |
| --- | --- |
| `state` | asset ID and `cut`/`fade` transition |
| `pointer_move` | start/end points and cubic Bézier control points |
| `click` | point and mouse button |
| `focus` | target bounds |
| `text` | target bounds, safe initial/final presentation strings, literal/masked/placeholder mode, and captured overlay style |
| `key` | normalized key chord and public display label |
| `scroll` | container bounds, start/end offsets, and start/end state asset IDs |
| `clip` | asset ID and media trim start/end milliseconds |
| `display_url` | validated public URL value |
| `complete` | no additional fields |

The text overlay style is a closed set of renderer inputs: font family, size,
weight, style, line height, letter spacing, color, alignment, padding, clipping
rectangle, selection, and caret state. A secret event's strings contain only
its selected safe presentation. Scroll events are emitted only when the Phase
0 classifier proves those fields are sufficient; otherwise the compiler emits
a `clip` or fails.

`initial_state`, `initial_pointer`, and `initial_display_url` make the payload
self-contained at local time zero. The compiler may derive them from the
preceding captured beat, but their resolved values are stored in this beat and
do not require the preceding payload during playback.

The payload contains no locator, capture URL, DOM text collected for targeting,
console entry, network URL, secret value, or local path.

## Global presentation manifest

The published entry point is `recording.presentation.json`:

```json
{
  "manifest_version": 1,
  "recording": {
    "id": "create-project",
    "title": "Create a project",
    "duration_ms": 18400
  },
  "renderers": {
    "terminal": {"payload_version": 1},
    "browser": {"payload_version": 1}
  },
  "presentation": {
    "browser": {
      "window": {
        "mode": "framed",
        "theme": "kde-breeze",
        "title": "OmegaFlow"
      },
      "chrome": {"mode": "full"}
    }
  },
  "audio": {
    "metadata": "audio.json",
    "intervals": [
      {
        "presentation_start_ms": 0,
        "presentation_end_ms": 2600,
        "source_start_ms": 0,
        "source_end_ms": 2600
      },
      {
        "presentation_start_ms": 13200,
        "presentation_end_ms": 18400,
        "source_start_ms": 2600,
        "source_end_ms": 7800
      }
    ]
  },
  "assets": {
    "state-before": {
      "path": "media/6f1b.webp",
      "media_type": "image/webp",
      "sha256": "6f1b",
      "bytes": 28431
    },
    "state-after": {
      "path": "media/ea92.webp",
      "media_type": "image/webp",
      "sha256": "ea92",
      "bytes": 30102
    }
  },
  "beats": [
    {
      "id": "start-server",
      "heading": "Start the application",
      "renderer": "terminal",
      "offset_ms": 0,
      "duration_ms": 13200,
      "payload": "beats/start-server.cast"
    },
    {
      "id": "create",
      "heading": "Create a project",
      "renderer": "browser",
      "offset_ms": 13200,
      "duration_ms": 5200,
      "payload": "beats/create.browser.json",
      "guide": {
        "success_hint": "The dialog opens after selecting New project."
      },
      "transition_in": "window-open"
    }
  ]
}
```

All paths are relative POSIX paths beneath the manifest directory. Absolute
paths and parent traversal are invalid. `renderers` is derived from the beats;
authors do not declare versions or capabilities.

`presentation.browser` is the generated renderer-header configuration resolved
from the user-facing recording header. It applies to every browser beat; beats
cannot override renderer versions or framing profiles.

The asset table is authoritative. A renderer may load only a beat payload and
assets present in the table or the small set of top-level files named by the
manifest. SHA-256 values in the real manifest are full lowercase hex digests.

### Manifest validation

Before play or publish, the manifest validator requires:

- the first beat offset is zero
- each later beat offset equals the previous offset plus duration
- the final beat end equals `recording.duration_ms`
- each external payload's beat ID, duration, renderer, and payload version
  match its manifest entry
- the renderer header contains exactly the renderers used by beats
- every asset reference resolves through the asset table and its full hash,
  byte size, and media type match the file
- audio intervals are ordered, non-overlapping, within recording and source
  duration, and have equal presentation/source lengths
- the ordered audio source intervals cover published narration source time
  exactly once; presentation gaps represent pauses or unnarrated beat time
- browser guides contain `success_hint` but no command payload
- every path remains beneath the manifest directory

Audio fields are omitted as a group when narration is disabled. A manifest
that violates any invariant is rejected before renderer creation.

## Player architecture

### Shared shell

The shell owns:

- manifest loading and validation
- the global clock, play/pause, seeking, playback rate, and duration
- narration audio mapping and word highlighting
- current beat, heading, markers, guide overlays, and fullscreen
- renderer creation, disposal, preloading, and errors
- responsive letterboxing and mobile controls

It selects the last beat whose `offset_ms` is not greater than global time,
computes `local_ms = global_ms - offset_ms`, and calls
`renderer.renderAt(local_ms)`.

The renderer contract is:

```javascript
class Renderer {
  async load({beat, payload, assets, container}) {}
  renderAt(localMs) {}
  setPlaybackRate(rate) {}
  async preload() {}
  dispose() {}
}
```

`renderAt` is seek-pure: it reconstructs the correct state for any local time
without depending on earlier calls. This is required for seeking, replay,
playback-rate changes, and mobile tab suspension.

The shell keeps the current and next beat payload ready. It disposes older
browser images and clips under the decoded-asset memory budget owned by the
versioned browser-renderer policy. This is not an authoring setting in Phase 1.
The Phase 0 policy measured 3-second and 2 MB encoded clip limits. Phase 1 keeps
the 3-second limit for automatically selected clips, allows explicitly captured
clips to follow their action completion timeout, and keeps the 2 MB limit for
all encoded clips. It proposes a provisional 64 MiB decoded-asset budget.
Content-addressed assets may remain in the browser HTTP cache.

The memory-budget candidate passes real-device playback validation at the
Phase 1 release gate only when the
largest allowed Phase 1 reference bundle can repeatedly play, seek across
every beat boundary, change
orientation, enter fullscreen, suspend, and resume on iOS Safari and Android
Chrome without a tab reload, out-of-memory failure, visible transition stall,
or decoded browser-asset residency above the candidate budget. The current and
next beats must stay ready; older evicted assets may reload on demand. If the
candidate does not pass, the release gate must reduce the allowed state/clip
size, memory budget, or interaction scope rather than ship an unbounded cache.

### Terminal renderer

The terminal renderer consumes one beat-local cast. It reuses the existing cast
decoder and terminal DOM. At local time zero it resets from the cast header;
seeking replays local cast events to the requested time.

### Browser renderer

The browser renderer is a layered stage:

```text
optional synthetic operating-system window
  optional synthetic browser chrome
    fixed-aspect viewport
      base state image or clip
      focus/text/scroll overlay
      pointer and click feedback
```

The viewport scales uniformly with
`min(available_width / viewport_width, available_height / viewport_height)`.
Unused space is letterboxed. All overlays share the same transform.

`window.mode` is `none` or `framed`. Browser chrome is `hidden`, `minimal`, or
`full`. Window and chrome assets are HTML/SVG owned by the renderer. They do
not change the captured page viewport and do not require recapture.

Browser chrome state is reconstructed from the beat's
`initial_display_url` plus `display_url` events, just as page pixels are
reconstructed from `initial_state` plus visual events. Seeking never consults
a captured URL or a previous beat.

Entry transitions are `cut`, `fade`, and `window-open`. Between-state
transitions are `cut`, `fade`, or a captured `clip`. Reduced-motion playback
turns `window-open` and `fade` into cuts without changing timeline duration.
`window.opening_transition` applies to the first browser beat and whenever a
browser beat follows a non-browser beat. Consecutive browser beats use the
normal between-beat transition.

Dynamic clips are muted, use `playsInline`, and are slaved to `local_ms` rather
than allowed to own the clock. A clip seek sets its media time from the active
event's local interval.

### Audio clock

Narration audio remains the only audio source. The manifest's audio intervals
map presentation time to source-audio time. During a gap between intervals the
audio element pauses; at the next interval it seeks to that interval's source
start and resumes if the global player is playing.

The existing `createCastAudioTimeline` behavior becomes a media-neutral audio
timeline helper using integer milliseconds. The global presentation clock is
authoritative; audio drift beyond 150 milliseconds triggers a corrective seek.

### Manifest-only player contract

The embed and `VideoPlayer` require a `manifest` attribute. Audio, renderer
payloads, assets, guides, and global timing are resolved from that manifest.

For a browser beat, the shell renders a `success_hint`-only guide without a
command block or Copy command. Terminal guide rendering is unchanged.

## Capture and presentation fingerprints

The recording fingerprint is split logically into two hashes:

- `capture_fingerprint`: source and environment inputs that require recapture
- `presentation_fingerprint`: timing, narration, framing, animation, and
  renderer inputs that require only recompilation

The capture hash includes:

- normalized browser actions, checks, capture URLs, and readiness conditions
- recording source and `run_file` dependencies
- setup and cleanup
- Playwright package version and exact browser revision
- operating-system/container profile
- viewport, device scale factor, user agent, locale, timezone, color scheme,
  reduced-motion preference, permissions, fonts, and page-audio mute policy
- auth-state content hash, never its content
- selected stability, fragment, and redaction policy versions

The presentation hash includes:

- capture fingerprint and referenced visual asset hashes
- narration-take cache identities and timestamp hashes
- anchors, waits, holds, and beat order
- pointer, typing, transition, window, and browser-chrome settings
- presentation compiler and renderer payload versions

Changing only window theme, display URL, pointer pacing, or a fade reuses the
private capture. Changing viewport, browser build, locator, capture URL, auth
state, or page mute policy requires recapture.

Published `recording.recording.json` keeps only hashes, relative source
dependency paths, versions, and safe warning codes. It contains no normalized
private spec.

## Authentication, secrets, and redaction

The capture runner resolves auth and secret paths before launching the browser
and registers their values with a `SecretRegistry`. The registry is in memory
and provides:

- exact-value scrubbing for private textual diagnostics
- rejection of a known secret in any public text file
- automatic input target masks
- hashes for freshness without disclosure

Diagnostics never record request or response bodies, cookies, authorization
headers, storage state, DOM snapshots, or arbitrary page HTML. Optional traces
and diagnostic video are disabled by default, stored below `diagnostics/`, and
never considered publish candidates.

Static masking happens before every captured state. The mask must cover the
configured target's current bounding box and is itself part of the captured
pixels. `redaction-v1` permits this only for stable states. Phase 0 did not
prove complete per-frame coverage for a moving target in Playwright video, so
a beat that combines required redaction with a dynamic fragment fails closed
with `BROWSER_REDACTION_UNSAFE`.

The pre-publish validator:

1. Walks only the generated public staging directory.
2. Rejects symlinks, absolute paths, and path traversal.
3. Parses every JSON file and validates it through the recognized versioned
   OmegaConf dataclass.
4. Scans public text for registered secret values and private absolute paths.
5. Verifies each referenced file is allowlisted and beneath the staging root.
6. Verifies asset size, hash, media type, viewport, duration, and absence of an
   audio stream in clips.
7. Proves that no private capture or diagnostic path is reachable.

Unknown secrets embedded only in pixels cannot be detected reliably. The
author remains responsible for sanitized accounts and fixture data.

## Artifact layout

A successful run uses this internal layout:

```text
<run-dir>/
  capture/
    terminal.cast
    terminal.timeline.jsonl
    browser.capture.jsonl
    states/
    fragments/
  presentation/
    recording.presentation.json
    beats/
    media/
    audio/
      <take-id>-<sha256>.<format>
    audio.json
    timestamps/
  audio/
    audio.json
    timestamps/
  diagnostics/
    trace.zip
    diagnostic.webm
    console.jsonl
    network.jsonl
  recording.fingerprint.json
  compilation-report.json
```

Only these generated classes may be published:

```text
recording.presentation.json
recording.recording.json
audio/*.<supported-audio-extension>
audio.json
timestamps/*.json
beats/*.cast
beats/*.browser.json
media/*.webp
media/*.<selected-clip-extension>
```

Diagnostics are absent unless explicitly enabled and remain private even then.

Publishing builds a sibling temporary asset directory, validates it, then
replaces the destination on the same filesystem. If replacement cannot be
atomic on the platform, content-addressed assets are copied first and the
manifest is replaced last; a reader therefore observes either the previous or
new complete reference graph. A failed publish preserves the previous public
bundle.

## Build, check, watch, and clean

`omegaflow action=build` for every recording runs:

1. load and normalize
2. decide capture freshness
3. capture when needed
4. generate or reuse narration takes
5. compile beat payloads and the global manifest
6. validate presentation and public safety
7. publish configured surfaces

`action=record` stops after successful private capture and fingerprints.
`action=check` validates source plus the freshest available capture and
presentation artifacts. `action=watch` requires a run-local presentation
manifest. `action=clean` removes
generated presentation artifacts but retains narration cache and preserved run
diagnostics under the existing retention policy.

Browser dependency failures are actionable and distinct:

- Python package missing: install the OmegaFlow browser extra
- browser binary missing: run `python -m playwright install chromium`
- Linux system libraries missing: run the documented Playwright dependency
  installation for the pinned profile
- selected fragment codec unavailable: install the documented ffmpeg build

The Playwright package is an optional `browser` project extra. Its exact pinned
version and corresponding browser revision are selected and updated together.
Playwright's browser binaries remain an explicit runtime installation rather
than wheel contents.

## Errors and warnings

Errors use stable codes plus source locations when available. Initial codes:

| Code | Severity | Meaning |
| --- | --- | --- |
| `BROWSER_SCHEMA` | error | Invalid browser config, action, target, or check |
| `BROWSER_TARGET_COUNT` | error | Target resolved to zero or multiple elements |
| `BROWSER_READINESS_TIMEOUT` | error | Lifecycle, wait, asset, or stability boundary timed out |
| `BROWSER_UNSUPPORTED_MOTION` | error | No faithful stable or captured presentation is available |
| `BROWSER_REDACTION_UNSAFE` | error | Required redaction cannot be proven |
| `PRESENTATION_CYCLE` | error | Anchor/wait/action constraints form a cycle |
| `PRESENTATION_OVERFLOW` | error | A cross-beat take member's visuals exceed its narration boundary |
| `PUBLISH_PRIVATE_REFERENCE` | error | A public artifact reaches private data |
| `EXTERNAL_NETWORK_CAPTURE` | warning | Capture depends on a non-loopback origin |
| `FRAGILE_BROWSER_SELECTOR` | warning | CSS or XPath selector used |
| `NARRATION_TAKE_REVIEW` | warning | A cross-beat take's member order changed |

Warnings are printed, stored by code in the private compilation report, and
copied to the safe fingerprint only as codes and beat/action IDs. They do not
block build or publish.

On capture failure, the run preserves the partial private log, available
states/fragments, redacted console/network summaries, and a postmortem entry
point when safe. No presentation manifest is produced from an incomplete run.

## Module plan

The implementation should introduce narrow modules rather than continue to
grow `studio.py` and `record.py`:

| Module | Responsibility |
| --- | --- |
| `recording_plan.py` | Typed normalization, cross-reference validation, narration-take planning |
| `capture.py` | Coordinator, shared environment, lifecycle, failure aggregation |
| `terminal_capture.py` | Persistent terminal control protocol and beat extraction |
| `browser_capture.py` | Playwright lifecycle, action dispatch, targeting, checks, capture log |
| `browser_visuals.py` | State normalization, policy interfaces, asset hashing, redaction |
| `presentation.py` | Constraint graph, global solve, beat-local materialization, manifest |
| `publish.py` | Public staging, schema/security validation, atomic replacement |
| `player/static/recording-player-core.js` | Global clock, manifest, audio intervals, renderer lifecycle |
| `player/static/terminal-renderer.js` | Beat-local cast playback |
| `player/static/browser-renderer.js` | Browser states, overlays, clips, framing, seek-pure rendering |

Compatibility wrappers keep existing Python imports, static cast-player URLs,
and generated embeds working while responsibilities move.

## Test strategy

### Schema and normalization

- strict acceptance and rejection for every action, target, condition, and
  check variant
- terminal default medium and unchanged existing recording fixtures
- browser/terminal action mismatch rejection
- recording-wide browser action IDs and beat-scoped anchor/wait validation
- implicit singleton take deduction before uniform fragmentation validation
- explicit contiguous takes and fragmented-take rejection
- take cache key and non-blocking reorder warning
- secret reference and display URL validation
- browser `success_hint`-only guide metadata and rejection of
  `guide.commands`

### Coordinator

Fake-runner tests assert exact calls for terminal-only, browser-only, and mixed
orders. Fault injection at setup, runner start, each beat, each check, browser
close, and cleanup proves:

- cleanup is attempted exactly once
- both started runners close
- primary and cleanup failures are both retained
- no successful `run_end` or presentation artifact is written
- terminal and browser state persist across intervening modality beats

One local integration fixture has a terminal beat write application state, a
browser beat consume it through a local server, and a later terminal beat
verify the browser-produced server state.

### Browser capture

A deterministic local fixture site covers:

- navigation and retained/hidden loading
- every semantic locator plus CSS/XPath warnings
- fill and type-keys execution with identical presentation facts
- shortcuts, nested scrolling, waits, and checks
- controlled inputs, contenteditable, font/image readiness, and timeouts
- auth state without public leakage
- static and dynamic redaction failure paths
- external-origin warning without blocking
- muted media and video-only fragments

Browser tests pin the same Playwright package and browser revision as capture.
Golden images are used only for stable renderer fixtures and include bounded
platform-specific tolerance.

### Compiler

- integer time and half-open boundary behavior
- source-order action constraints
- anchors, waits with gaps, holds, and cycle diagnostics
- cross-beat take continuity, early visual hold, and overflow rejection
- beat relocation by offset without event rewriting
- terminal/browser boundary seeking
- unknown event/version rejection
- content hash deduplication and deterministic animation output

### Player and publishing

- manifest-only embeds and `VideoPlayer` props
- browser guide text without terminal command or copy UI
- manifest loading, renderer switching, arbitrary seek, replay, and rate changes
- narration pause/resume and corrective drift seeking
- browser scaling, chrome/window combinations, display URL, and transitions
- keyboard and touch controls, orientation changes, fullscreen, and tab resume
- iOS Safari and Android Chrome memory budgets
- public allowlist, path traversal, symlink, hash, secret, and private-reference
  failures
- atomic publish rollback under injected copy and rename failures

The Phase 0 prototype additionally runs the HLD's five experiment matrices and
composition demonstration. Its selected policies become versioned fixtures for
the production tests above.

## Rollout order

1. Land schema normalization and narration takes.
2. Land the versioned manifest, shell, and terminal renderer adapter.
3. Run Phase 0 and freeze the five visual policy versions and clip codec.
4. Land coordinator and browser capture against the local fixture site.
5. Land browser presentation compilation, renderer, and security validation.
6. Route terminal, browser, and mixed build, check, watch, and publish through
   the manifest path.
7. Run the desktop and mobile playback matrix before marking Phase 1 stable.

Once the parity contract above passes, the old monolithic executor and its
recording-wide artifact path are deleted.

## Phase 0-owned implementation constants

Detailed design can proceed without these values, but production defaults must
not be guessed. The spike must provide:

- stability evidence, thresholds, timeout, and policy version
- text-overlay eligibility classifier and fallback
- scroll-synthesis classifier and fallback
- dynamic-fragment source, codec, trim/timestamp mapping, media-size budget,
  and a provisional browser-renderer decoded-asset memory budget for later
  real-device validation
- dynamic-redaction mechanism and prohibited combinations

The production interfaces in this document accept those results as versioned
policy objects. A result that cannot meet the HLD's pass criteria removes the
affected interaction from Phase 1 instead of weakening capture or publication
safety.
