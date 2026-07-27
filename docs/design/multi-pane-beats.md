# Synchronized Multi-Pane Beats

## Status

Active pre-release design. This contract is intended to shape the tutorial and
the player before the first public release.

The event, join, lifecycle, transition, and responsive-layout defaults are
settled. The outer unit remains OmegaFlow's existing beat.

## Motivation

OmegaFlow currently assigns one medium, capture runner, payload, and renderer to
each beat. That works for a terminal beat followed by a browser beat, but it
cannot show a beat definition beside its result or show activity from several
streams in one synchronized composition.

A multi-pane composition is not only a player layout. It combines constrained
pane beats under one ordinary OmegaFlow beat on a solved presentation timeline.

## Goals

- Give every narration and visual stream a stable, human-authored identity.
- Let an ordinary OmegaFlow beat compose an ordered sequence of pane beats in
  each participating visual pane.
- Synchronize pane beats and narration with one event and join model.
- Support visualization-only, terminal, and browser panes without giving any of
  them special presentation-timing semantics.
- Permit two, three, or more panes without changing event identity.
- Keep ordinary single-pane recording files concise.
- Keep visual layout independent from event ordering and synchronization.
- Detect missing event references and dependency cycles before capture.
- Make the first complete use case a beat definition above its live terminal
  result.

## Non-goals for the first slice

- Capturing several browser or terminal panes concurrently.
- Capturing arbitrary desktop or mobile applications.
- Branching or conditional event graphs.
- A general constraint language beyond cross-stream joins and bounded offsets.
- Finalizing a replacement surface syntax for `@anchor@`, `@wait:...@`, or
  `after`. That evaluation is tracked separately in the backlog.
- Supporting both an old and a new presentation bundle format. OmegaFlow is
  unreleased; generated bundles move to the new format together.

## Core model

### Narration stream

A recording has one narration stream in the first implementation. Its
human-authored `narration_id` is its stable identity.

Narration may span several outer beats and their pane beats. Beat boundaries do
not split or restart its logical stream identity. Physical TTS takes are
internal fragments of that stream and never appear in event references.

An inline narration anchor begins a named narration segment. That segment ends
at the next anchor or at the end of the narration stream:

```text
@explain_target@ The target identifies the text to emphasize.
@show_result@ Here is what that looks like in the recording.
```

This produces two narration actions, `explain_target` and `show_result`, with
distinct `started` and `ended` events. An anchor is therefore not modeled as a
zero-duration action.

### Pane stream

A pane is a named visual stream. Its human-authored `id` is stable across the
recording. Its kind selects its capture and renderer implementation.

Initial kinds:

- `visualization`: explicitly authored, syntax-highlighted text or other static
  presentation content;
- `terminal`: a captured terminal session;
- `browser`: a captured browser session.

A persistent capture runner is keyed by pane ID, not by pane kind, so two panes
of the same kind can remain independent.

A future desktop, emulator, or other application surface can add a pane kind
without changing event or join identity.

### Pane beat

A pane beat is a constrained visual contribution to one outer beat. A pane may
contain several pane beats, which run sequentially in authored order. A pane
beat owns:

- its human-authored pane-beat ID;
- actions;
- checks;
- effects;
- its entry transition from the pane's preceding visual state;
- pane-kind-specific presentation settings.

Action order within a pane beat and pane-beat order within one pane are
intrinsic to that stream. Cross-stream ordering is expressed only with joins.

Pane beats cannot own narration, headings, guides, checkpoints, viewer holds,
or independent player sections. They cannot be played independently from their
outer beat.

### Outer beat

The outer unit is the existing OmegaFlow beat. It may contain an ordered
sequence of pane beats for each participating pane and place those panes in a
layout.
Narration remains a recording-level logical stream and may cross outer-beat
boundaries.

An outer beat owns:

- its public beat ID, heading, and caption;
- its narration content and narration anchors;
- guides and checkpoints;
- viewer holds;
- a mapping from pane IDs to ordered sequences of constrained pane beats;
- its existing complete-layout transition from the preceding outer beat;
- one solved presentation timeline interval;
- a layout that places panes without changing their event ordering.

The outer beat is the player section and the unit used by existing
single-pane recordings.

Its content interval ends at the latest solved endpoint among the narration
portion assigned to that outer beat, all participating pane beats, and their
transitions. The outer beat's viewer hold follows that shared content interval.
Pane beats never create independent player boundaries or viewer holds.

Every selected pane is mounted in its layout slot for the complete outer-beat
interval. Before its first pane beat begins, it shows its captured or authored
initial state; an author may explicitly make that state hidden without removing
the pane's layout slot. Each subsequent pane beat transitions from the
preceding pane state. After the final pane beat completes, the pane holds its
final state until the outer beat ends. This makes layout, playback, and seeking
deterministic.

Outer-beat and pane-beat transitions are distinct scopes. The outer transition
replaces the preceding beat's complete composition. A pane-beat transition
replaces only that pane's preceding state while the surrounding layout and
other panes remain unchanged.

A pane beat's entry transition occupies an explicit presentation interval
between the preceding pane beat and the new pane beat. It transitions from the
preceding beat's final state to the new beat's captured or authored initial
state. The new pane beat's first action begins after the transition ends. The
transition therefore contributes duration and intrinsic same-stream ordering,
but it is not a join and cannot reference another stream.

Pane-beat entry transitions are also distinct from action-level transitions
inside a renderer. For example, browser presentation settings may fade between
two captured browser states produced by actions within one pane beat. The
compiler must model both intervals without conflating their configuration or
timing.

## Events

An event is an observable endpoint: something started or something ended.
Events do not cause another stream to wait. They are facts that joins may
reference.

Narration event identity:

```text
<narration_id>.<segment_id>.started
<narration_id>.<segment_id>.ended
```

Pane event identity:

```text
<pane_id>.<beat_id>.<action_id>.started
<pane_id>.<beat_id>.<action_id>.ended
```

Examples:

```text
voiceover.explain_target.started
voiceover.explain_target.ended
terminal.highlight_demo.show_status.started
terminal.highlight_demo.show_status.ended
browser.preview.open_player.ended
```

These human-authored identifiers are the canonical internal identities. There
is no parallel opaque stream ID or pane-local alias.

Identifiers are unique in their natural scope:

- narration segment IDs within one narration stream;
- pane IDs within one recording;
- pane-beat IDs within one pane across the recording;
- action IDs within one pane beat.

Only addressable producers emit events:

- narration segments emit `started` and `ended`;
- explicitly identified pane actions emit `started` and `ended`;
- checks validate state but do not emit events;
- effects consume solved timing events but do not emit events.

If an observed condition must be joinable, it is modeled as an explicitly
identified wait action. This keeps event identity independent from renderer
details and avoids treating every check or visual effect as an implicit stream.

## Joins

A join causes one stream to wait until an event from another stream occurs.
Same-stream sequencing is intrinsic and must not be expressed as a join.

The established authoring surface has two join forms:

- `after: ...` attaches a join to a pane-beat start or pane action;
- `@wait:...@` inserts a join at a position in narration.

They normalize to the same internal shape:

```text
Join(waiting_stream_position, referenced_event, non_negative_gap)
```

For example, a browser action may wait for a terminal action:

```yaml
after: terminal.build.start_server.ended
```

Narration may wait for a browser action:

```text
@wait:browser.preview.open_player.ended+200ms@
```

The precise public spelling and endpoint defaults remain a separate backlog
decision. If the syntax changes before release, update repository recordings
atomically rather than adding a compatibility path. The typed model always
stores a fully qualified event identity.

### Capture and presentation meaning

Every join contributes a presentation-ordering constraint.

When a pane-beat start or pane action joins a capture-observable event from
another pane, the capture scheduler delays that complete pane beat or
individual action, respectively, until the event has been observed. This
preserves real application causality.

When either position joins a narration event, capture cannot wait for
synthesized audio that does not exist yet. That join affects presentation
ordering only.

Narration joins are also presentation-only because narration is synthesized and
aligned after capture.

This phase behavior is derived from the streams participating in the join. It
does not require a second public primitive such as `requires`.

### Event meaning

An `ended` event must describe a useful completed state:

- terminal command: the process completed and its output was flushed;
- browser action: the action and its required captured state completed;
- browser wait: the requested state was observed and captured;
- narration segment: aligned speech reached the end of the segment.

When a causal application update is asynchronous, authors join against a
browser wait event rather than assuming that the initiating terminal command's
end means the browser has rendered.

## Timing pipeline

1. Parse outer beats, pane streams, pane beats, actions, narration segments,
   events, and joins through typed OmegaConf schema dataclasses into immutable
   typed plan dataclasses.
2. Validate identity, event existence, cross-stream joins, allowed capture
   phases, and an acyclic capture-observable join graph.
3. Start the required pane runners and dispatch pane actions whose
   capture-observable joins are satisfied.
4. Record observed `started` and `ended` timestamps for every captured action.
5. Synthesize and align narration, producing narration segment endpoints.
6. Build a presentation constraint graph from all joins, observed action
   durations, transition intervals, narration alignment, outer-beat
   boundaries, and viewer holds.
7. Reject cycles with a chain of the involved event references.
8. Solve the earliest feasible presentation time for every endpoint.
9. Materialize one time-mapped payload per participating pane beat and render
   all pane beats against the same outer-beat-local presentation timestamp.

The capture timestamps and solved presentation timestamps are two time domains
for the same event identity. Observed action durations become constraints; the
solver may relocate events without changing their duration or causal order.

The initial solver can use a directed acyclic graph and longest-path evaluation
for minimum-offset constraints. Exact alignment or more general constraints are
not required for the first slice.

## Authoring shape

The explicit shape defines globally identified pane streams in the Markdown
body. A single pane declaration must precede any `beat` or `beats` declaration.
Ordinary outer beats contain the constrained pane beats that contribute their
visuals:

````md
---
narration:
  id: voiceover
---

```yaml studio-directive
scene: Highlight Terminal Text
panes:
- id: definition
  kind: visualization
- id: terminal
  kind: terminal
```

```yaml studio-directive
beat:
  id: apply_highlight
  heading: Highlight Terminal Text
  narration: >-
    @explain_target@ The target identifies the terminal text to emphasize.
    @show_result@ Here is what that looks like in the recording.
  layout:
    areas:
    - [definition]
    - [terminal]
  panes:
    definition:
    - id: explain_target
      actions:
      - id: show_target
        show:
          language: yaml
          text: 'text: "Renderer: ready"'
    - id: highlight_timing
      after: voiceover.show_result.started
      transition: fade
      actions:
      - id: show_timing
        show:
          language: yaml
          text: |-
            start: voiceover.show_result.started
            end: voiceover.show_result.ended
    terminal:
    - id: highlight_result
      actions:
      - id: show_status
        run: python3 status_demo.py
        after: voiceover.show_result.started
      checks:
      - output_contains: "Renderer: ready"
      effects:
      - highlight:
          targets:
          - text: "Renderer: ready"
            start: voiceover.show_result.started
            end: voiceover.show_result.ended
```
````

The visualization content is explicitly supplied by the author. OmegaFlow does
not inspect the other pane beat, resolve configuration interpolations, or read
referenced files to construct it.

For a side-by-side terminal and browser outer beat:

```yaml
layout:
  areas:
  - [terminal, browser]
panes:
  terminal:
  - id: run_server
    actions: [...]
  browser:
  - id: preview_server
    actions: [...]
```

The `areas` matrix is presentation-only. Reordering or responsively stacking
its panes does not change event references or solved timing.

Responsive fallback is driven by the player container's available geometry,
not by a viewport breakpoint. The declared grid is retained while every pane
can satisfy its kind-specific minimum usable size. Otherwise, panes stack
vertically in their first-appearance order in `areas`. This changes geometry
only; event ordering, joins, transitions, and solved timing remain unchanged.

### Single-pane shorthand

Ordinary recordings keep the current concise beat-oriented shape. Normalization
creates one implicit pane named `main` and one constrained pane beat inside each
current outer beat. The outer beat keeps the current beat ID and all public
beat-level content.

The implicit `main` track is local to each outer beat rather than a
recording-global authored pane. This preserves existing recordings that move
from a terminal beat to a browser beat. Explicit pane declarations remain
recording-global, stable, single-kind streams.

This is authoring shorthand, not a second internal model. A recording cannot
combine a beat's single-pane shorthand with explicit pane beats.

## Typed normalized model

Public YAML is parsed through explicit OmegaConf schema dataclasses and
normalized into immutable plan dataclasses before capture, compilation, or
bundle generation. The core model is structurally typed rather than passed
between phases as nested mappings:

```python
@dataclass(frozen=True)
class NarrationStreamPlan:
    id: str
    segments: tuple[NarrationSegmentPlan, ...]


@dataclass(frozen=True)
class PanePlan:
    id: str
    kind: PaneKind


@dataclass(frozen=True)
class PaneTransitionPlan:
    kind: PaneTransitionKind
    duration_ms: int


@dataclass(frozen=True)
class OuterBeatTransitionPlan:
    kind: OuterBeatTransitionKind
    duration_ms: int


@dataclass(frozen=True)
class EventRef:
    stream: StreamRef
    pane_beat_id: str | None
    action_id: str
    endpoint: EventEndpoint


@dataclass(frozen=True)
class JoinPlan:
    waiting_stream: StreamRef
    waiting_position: StreamPosition
    event: EventRef
    gap_ms: int


@dataclass(frozen=True)
class PaneBeatPlan:
    id: str
    start_join: JoinPlan | None
    actions: tuple[PaneActionPlan, ...]
    checks: tuple[PaneCheckPlan, ...]
    effects: tuple[PaneEffectPlan, ...]
    transition: PaneTransitionPlan
    presentation: PanePresentationPlan


@dataclass(frozen=True)
class OuterPaneTrackPlan:
    pane_id: str
    kind: PaneKind
    beats: tuple[PaneBeatPlan, ...]


@dataclass(frozen=True)
class OuterBeatPlan:
    id: str
    heading: str | None
    caption: str | None
    narration: NarrationFragmentPlan | None
    guide: GuidePlan | None
    checkpoint: CheckpointPlan | None
    viewer_hold_ms: int
    pane_tracks: tuple[OuterPaneTrackPlan, ...]
    layout: PaneLayoutPlan
    transition: OuterBeatTransitionPlan
```

`PaneBeatPlan` deliberately has no narration, guide, checkpoint, viewer-hold,
or public-section fields. This makes the pane-beat restriction enforceable by
construction. `StreamRef`, `StreamPosition`, `EventEndpoint`, pane actions,
checks, effects, layouts, and presentation settings are closed typed
dataclasses or enums rather than strings and `dict[str, Any]`.

`OuterPaneTrackPlan` is the sole owner of `pane_id`; child plans do not duplicate
it. This prevents the enclosing track and a pane beat from disagreeing about
event-stream identity.

The authoring layer is typed separately with mutable OmegaConf-compatible
dataclasses such as `PaneConfig`, `PaneBeatConfig`, `PaneLayoutConfig`, and
`OuterBeatPaneTrackConfig`. `RecordingBeatConfig` owns the outer-beat fields and
its typed pane tracks. Schema dataclasses express defaults and reject unknown
YAML fields; frozen plan dataclasses express normalized invariants. Neither
layer uses an untyped mapping as the contract between phases.

Every action in an explicit pane beat requires a human-authored ID because it
is a potential event producer. Current single-pane shorthand may normalize an
unreferenced action to a private deterministic ID, but that generated identity
is never accepted in an authored event reference.

A pane beat's optional `after` is normalized into `start_join`. It gates the
pane beat's entry transition and therefore its complete visual contribution.
An action-level `after` gates only that action and cannot prevent an earlier
pane transition from rendering.

## Presentation bundle

The bundle represents:

- the narration stream and its aligned segment events;
- globally identified panes;
- ordered pane-beat payloads grouped into pane tracks;
- outer beats that contain ordered pane-beat sequences and define their layout;
- solved event endpoints and outer-beat intervals.

The player creates and retains one renderer instance per active pane. Every
renderer receives the same outer-beat-local presentation timestamp.

The complete visualization design can grow to contain canonical escaped text,
syntax token ranges, and timed callouts. The browser does not parse YAML or
infer configuration paths.

Slice 3 defines the static portion of that payload. A version-1 visualization
payload contains its pane-beat identity and duration, a language label, plain
text, and ordered non-overlapping token ranges. Token offsets count Unicode
code points rather than UTF-16 code units. Token kinds are a closed set:
`key`, `string`, `number`, `boolean`, `comment`, `keyword`, `operator`, and
`punctuation`.

The player creates text nodes and token spans from this payload. It never
interprets visualization text as HTML. Payload text and token counts are
bounded, and token ranges must remain within the text.

Slice 4 exposes the first public authoring subset: one explicitly authored
visualization pane beside one captured terminal pane. Each pane track currently
contains exactly one pane beat. Cross-stream joins, explicit browser panes,
and multiple captured panes remain rejected until their execution paths land.

The version-1 bundle uses one representation rather than retaining the former
flat beat representation:

- `panes` declares recording-global pane IDs and their renderer kinds;
- each outer beat carries a layout grid whose area names reference those pane
  IDs;
- each outer beat contains one ordered pane track for every pane in its
  layout;
- each pane track contains one or more pane beats with outer-beat-local
  offsets, durations, payloads, and entry transitions.

A pane beat's `duration_ms` includes its entry transition. The transition
occupies the start of the pane-beat interval; renderer-local time starts at
zero when the transition ends. A `cut` transition has zero duration. Pane
beats in one track may not overlap. A gap holds the preceding pane beat's final
frame, and the final frame remains held through the end of the outer beat.
Before the first pane beat, the track either shows that beat's initial frame or
stays hidden according to its explicit initial-state policy.

Existing single-pane recordings compile into this representation too. Their
implicit `main` tracks receive generated bundle-local pane identities so mixed
terminal and browser outer beats do not pretend to be one persistent pane.
There is no legacy flat-manifest compatibility path.

## Capture coordination

The capture coordinator becomes a stream scheduler:

- create one persistent runner for every captured pane ID in use;
- submit actions when their capture-observable joins are satisfied;
- emit and record fully qualified action endpoint events;
- keep every pane's artifacts and observed timing separate;
- cancel dependent work and close every started runner after a primary failure;
- fail when an action cannot complete, an event is never emitted, a join cycle
  exists, or no further action can become ready.

The first useful slice needs one captured terminal pane plus one static
visualization pane. Until concurrent captured-pane scheduling lands, validation
must reject outer beats containing more than one captured pane.

## Validation

At minimum, validation must reject:

- duplicate or invalid narration, pane, beat, or action identifiers;
- layouts that omit or reference unknown panes;
- outer beats that contain pane beats for an unknown pane;
- pane beats containing narration, guides, checkpoints, viewer holds, or public
  section metadata;
- mixed use of explicit pane beats and single-pane shorthand in one outer beat;
- actions, checks, or effects invalid for their pane kind;
- explicit pane actions without valid IDs;
- missing, ambiguous, same-stream, or phase-invalid join references;
- capture-observable join cycles;
- presentation constraint cycles;
- unsupported combinations of multiple captured panes while capability-gated;
- manifests whose event identities, payloads, or solved durations disagree.

Validation also enforces bounded pane, action, event, visualization-text, and
syntax-token counts. Authored pane IDs are mapped to generated safe layout area
names; they are never interpolated directly into CSS.

Errors for missing references and cycles must include the authored event names
and the shortest relevant dependency chain.

## Verification plan

### Typed model and solver

- parse and normalize immutable narration, pane, outer-beat, pane-beat, action,
  event, and join dataclasses;
- reject missing, ambiguous, same-stream, and cyclic joins;
- verify positive join offsets and narration segments spanning outer beats;
- verify pane entry transitions occupy the interval before the next pane
  beat's first action and contribute to outer-beat duration;
- verify a pane-beat start join gates its entry transition, while an action join
  gates only that action;
- verify a pane-to-pane start join gates the complete pane beat during capture
  and presentation, while a narration-to-pane start join gates presentation
  only;
- verify renderer-internal action transitions remain distinct from pane entry
  and outer-beat transitions;
- verify a first pane-beat entry transition composes with an outer-beat
  transition without double-applying opacity or exposing the player background;
- prove single-pane shorthand has identical timing to the current model;
- verify observed durations survive relocation into presentation time;
- verify pane-to-pane joins gate capture and presentation, while joins involving
  narration affect presentation only;
- prove checks and effects do not become implicit event producers.

### Bundle and player

- validate fixture bundles containing two, three, and four panes;
- play and seek through several sequential pane beats in one mounted pane,
  including their individual transitions;
- verify initial, explicitly hidden, transitioning, and held-final pane states
  under forward playback and backward seeks;
- render, play, seek, preload, resize, and dispose every active renderer;
- verify all panes resolve the same outer-beat-local timestamp;
- verify responsive layout changes geometry without changing timing;
- reject unsafe identifiers and escape visualization text.

### Capture and failure handling

- build a visualization-plus-terminal recording end to end;
- reject a second captured pane while the capability gate is active;
- verify action progress includes pane and beat context;
- verify failures cancel dependent work and close every started runner;
- later, verify concurrent terminal-to-browser causality before lifting the
  capability gate.

### Tutorial acceptance

- migrate the terminal highlight demonstration to the two-pane model;
- remove its occurrence workaround;
- verify narration callouts, the visualized beat definition, and terminal
  highlighting remain synchronized through playback and seeking.

## Implementation slices

1. Typed immutable narration, pane, outer-beat, pane-beat, action, event, and
   join plan dataclasses, plus typed OmegaConf authoring dataclasses and
   normalization of existing single-pane recordings.
2. Multi-pane presentation schema and player composition using fixture
   payloads, including sequential pane beats and pane-scoped transitions.
3. Explicit visualization renderer with escaped text and syntax tokens.
4. Visualization-plus-terminal capture/build path, with validation rejecting
   more than one captured pane. Implemented as the strict one-pane-beat-per-track
   subset described above.
5. Migrate the terminal highlight demo and remove its occurrence workaround.
6. Add multi-stream event resolution, join validation, presentation retiming,
   concurrent captured-pane scheduling, cleanup, progress attribution, and
   tests; then lift the capability gate.
7. Demonstrate synchronized terminal-to-browser influence.

No public configuration is accepted before its corresponding execution path is
available.

## Resolved design defaults

- The existing outer beat is the public player section.
- Pane beats are narration-free visual units nested under an outer beat.
- An outer beat ends after its narration portion and pane tracks complete, then
  applies its viewer hold.
- Every selected pane remains mounted for the outer beat, using an initial
  state before its first pane beat and holding its final state afterward.
- Outer beats retain complete-layout transitions; sequential pane beats may
  define pane-scoped entry transitions.
- Responsive fallback uses container geometry and stacks panes in declared
  order when their minimum usable sizes cannot be preserved.
