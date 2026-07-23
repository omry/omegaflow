# Website And Tutorial Information Architecture

## Status

- Feedback collection complete; consolidated design and implementation sequence
  recorded here
- Scope: public website, documentation hierarchy, and tutorial curriculum
- Product implementation has not started. Prerequisite capabilities must be
  implemented, demonstrated, and approved one at a time before tutorial
  authoring begins.

## Context

OmegaFlow now supports scripted terminal and browser workflows, optional
narration, guided playback, checks, and multiple publishing surfaces. The
website still reflects an earlier, terminal-centered product and its tutorial
chapters are placeholders. Reference material exists, but it is not organized
around a clear learning path.

This document defines what the website needs to communicate, how the content
should be organized, and what the tutorial should teach. It will be refined
section by section before implementation begins.

## Goals

1. Explain OmegaFlow accurately to a first-time visitor.
2. Give a new user one obvious path from installation to a published video.
3. Separate learning material, task-oriented guides, and exhaustive reference.
4. Cover terminal and browser workflows as equal product capabilities.
5. Make installation requirements and platform support easy to find.
6. Reuse the strongest existing documentation without retaining duplicate
   paths to the same information.
7. Keep the written tutorial useful without requiring video playback.
8. Leave room for adjacent scripted experiences without presenting unbuilt
   capabilities as current product features.

## Non-goals

1. Redesigning the visual identity or player UI.
2. Implementing the new website structure in this phase.
3. Documenting every internal media or build-pipeline detail in the tutorial.
4. Preserving unreleased URLs or categories when they duplicate or obscure the
   target structure.

## Design principles

### One product lifecycle

The public explanation should consistently follow:

**Script -> Build and check -> Watch and iterate -> Publish**

The lifecycle is more useful to a new user than a list of CLI actions or file
types. Rebuildability is the loop that connects later source edits back to
build and preview; it is not a disconnected final step.

### One canonical learning path

There should be one Getting Started path and one tutorial. The current public
first-video material and tutorial introduction should not remain as competing
versions of the same journey.

### Teach before referencing

Tutorial pages teach concepts in a deliberate order. Guides solve specific
tasks. Reference pages describe complete contracts. A reference page should
not be required to understand an early tutorial chapter.

### Introduce product vocabulary deliberately

A beat is one recorded unit contained in an OmegaFlow video. The tutorial
defines `beat` before relying on it and uses the term whenever the thing being
discussed is an OmegaFlow beat, rather than alternating among section, step,
segment, and beat without explanation.

### Written content stands on its own

Videos demonstrate and reinforce a page. They do not contain the only copy of
an instruction, command, prerequisite, or explanation.

## Product framing and audiences

### Proposed product definition

OmegaFlow turns scripted terminal and browser workflows into narrated,
rebuildable videos that live with the code and documentation they explain.

The supporting value proposition is that an OmegaFlow recording is authored,
reviewed, checked, rebuilt, and published as a project artifact instead of
being trapped in a one-off screen capture.

OmegaFlow is not a screen recorder and does not record an arbitrary desktop.
It builds presentations from scripted, supported recording media. Terminal and
browser are the media supported today; future desktop or emulator-hosted mobile
adapters may extend the model without changing that authored-source principle.

### Product horizon

The underlying authoring model may eventually support more than linear product
videos. Plausible adjacent uses include:

- interactive tutorials that ask the viewer to perform or confirm work
- demonstrations of agent workflows
- branching or choose-your-own-adventure presentations

Only the first of these has a meaningful foothold in the current product
through guided playback and checkpoints. Agent-specific workflows and
branching presentations are not committed product capabilities.

The information architecture should therefore distinguish the broader product
category from the current output:

- **Broader category:** scripted, reproducible demonstrations and tutorials
- **Current concrete product:** narrated, guided, rebuildable videos of
  terminal and browser workflows

Homepage copy must lead with current behavior. It may describe the broader
category as context, but it must not imply that agent demonstrations or
branching content are supported until those workflows exist. Likewise, the
documentation hierarchy should be organized around authoring, playback, and
publishing rather than assuming that every future experience is a passive
video.

### Proposed audience hierarchy

1. Developers and technical writers who maintain product documentation and
   tutorials in a repository.
2. Developer-relations and product engineers who create repeatable demos,
   release walkthroughs, or onboarding material.
3. Existing OmegaFlow authors looking for a specific guide or reference.
4. Contributors working on OmegaFlow itself.

### Decision D1: website job and primary audience

**Recommendation:** make the homepage evaluator-first and the documentation
author-first. The homepage should quickly prove what OmegaFlow is, what it can
record, and why a source-controlled video is useful. Once a visitor enters the
documentation, navigation should optimize for completing authoring tasks.

This decision was required before finalizing homepage hierarchy.

It also includes a positioning choice:

- keep **rebuildable video** as the entire product identity, or
- use a two-layer identity in which OmegaFlow is a system for scripted,
  reproducible demonstrations and tutorials whose current primary output is a
  rebuildable video

**Recommendation:** use the two-layer identity, while keeping the first
homepage proof and all feature claims grounded in the current video product.

**Resolution:** approved. The homepage is evaluator-first, the documentation
is author-first, and the product uses the recommended two-layer identity.

## Concept and content inventory

The following subjects need a deliberate home. Inclusion here does not imply
that every subject belongs in the tutorial.

| Area | Concepts and tasks to document | Likely home |
| --- | --- | --- |
| Product | Definition, use cases, source-controlled rebuildability | Homepage, introduction |
| Lifecycle | Script, build/check, watch/iterate, publish, rebuild | Homepage, Getting Started |
| Mental model | Project, recording workspace, video, collection, scene, beat | Concepts |
| Authoring | Markdown prose, frontmatter, scene and beat directives | Tutorial, reference |
| Terminal workflows | Commands, visible commands, output, checks, realtime mode, synchronized effects | Tutorial, guides, reference |
| Browser workflows | Navigation, click, type, scroll, pointer, waits, checks, presentation | Tutorial, guides, reference |
| Narration | Optional voiceover, takes, anchors, waits, cross-beat synchronization | Tutorial, guides, reference |
| Reliability | Expectations, setup and cleanup, readiness, deterministic waits | Tutorial, guides |
| Build behavior | Capture, generation, retiming, assembly, validation, caching, force rebuilds | Concepts, guides |
| Player | Beat navigation, previews, guided mode, checkpoints, seeking, speed | Tutorial, guide |
| Publishing | Docusaurus, standalone HTML, assets, tracked versus runtime files | Tutorial, guides, reference |
| Configuration | Project settings, recording defaults, frontmatter, CLI overrides | Concepts, reference |
| Operations | Bootstrap, list, build, watch, check, inspect, runs, clean, GC | Getting Started, reference |
| Security | Authentication, secrets, redaction | Browser guide, reference |
| Environment | Installation, Python and Bash requirements, supported platforms, WSL | Getting Started |
| Collections | Build and review shortcuts for related videos | Concepts, guide |
| Internals | Manifest, media fragments, encoding, bundle layout, diagnostics | Advanced reference |

## Proposed website structure

### Primary navigation

1. **Get Started**
2. **Tutorial**
3. **Guides**
4. **Reference**
5. **GitHub**

The CLI belongs under Reference rather than being a product-level navigation
item.

### Homepage hierarchy

The homepage should answer four questions in order:

1. What is OmegaFlow?
2. What does the result look and feel like?
3. Why script a demonstration instead of recording it once?
4. How do I try it?

#### 1. Navigation

- Logo and product name
- Get Started
- Tutorial
- Guides
- Reference
- GitHub, visually separated on the right

The navigation should expose user goals rather than elevating the CLI as a
separate product surface.

#### 2. Hero and representative demo

The hero contains:

- a short category line describing scripted demonstrations and tutorials
- a concrete headline about terminal and browser workflows becoming guided,
  rebuildable videos
- one supporting sentence explaining that the recording source lives with the
  project
- primary action: **Build your first video**
- secondary action: **Read the docs**
- the current representative video as the primary visual proof

The demo should be visible in the initial desktop composition. On narrow
screens it should appear immediately after concise hero copy and actions,
before explanatory feature sections. The mobile layout should not require the
user to scroll through a long marketing introduction before reaching the
product.

The homepage demo starts paused. Its initial frame, title, and play affordance
must therefore communicate that it is a playable product demonstration without
depending on autoplay or narration. A spacious player is preferred on desktop
when viewport height permits; compact layout remains appropriate when vertical
space is genuinely constrained.

The demo itself provides the watch action, so a separate **Watch demo** button
would duplicate the same path.

#### 3. Capability summary

A compact strip or short row establishes what the demo contains:

- terminal and browser workflows
- optional synchronized narration
- guided, interactive playback
- publishing for documentation sites or standalone viewing

This is orientation, not a full feature grid.

#### 4. Authored, not captured

This section explains the core differentiator by pairing a small recording
source excerpt with its visible result. It should show that commands, browser
actions, narration, checks, and publishing intent are reviewable project
source.

Recording examples should use an OmegaFlow-aware syntax grammar rather than
generic Markdown coloring. Beat declarations, media, actions, checks,
narration, guides, and timing fields must be visually distinguishable.

The section should emphasize three outcomes:

- update the script when the product changes
- verify important results while building
- rebuild the published presentation instead of re-recording it by hand

#### 5. Lifecycle

Present the lifecycle in three compact stages:

1. **Script** terminal and browser beats.
2. **Build, watch, and check** the synchronized presentation while iterating.
3. **Publish and rebuild** it as the project changes.

The detailed Getting Started path should explain that the first watch requires
a successful build and that an active watch server rebuilds a recording after
its source changes. The homepage can keep this as one compact authoring stage.

#### 6. Use cases

Show current, supportable uses:

- documentation walkthroughs
- guided tutorials and onboarding
- repeatable application and release demonstrations
- small browser applications, visualizations, and simulations

Do not reduce this list to "product demos." A scripted earthquake visualization
or a local creative tool is a representative OmegaFlow use even when it is not
a product walkthrough.

Agent demonstrations and branching presentations should not appear as current
use-case cards until their workflows are supported. The surrounding taxonomy
must nevertheless leave room to add them later.

#### 7. Publishing and environment

Briefly identify Docusaurus and standalone HTML as current publishing surfaces.
State only the currently verified environment contract and link to Installation
and Supported Platforms for the precise support matrix. Direct native Windows
browser recording is a pre-release capability target rather than a claim the
homepage may make before validation. Native desktop application recording is a
separate, cross-platform future medium, not a Windows-specific extension.

#### 8. Final action

Repeat **Build your first video**, with a secondary link to the tutorial for
users who want the complete guided path.

### Homepage content constraints

- The hero should not enumerate commands or configuration fields.
- The page should not present speculative capabilities as available.
- The page should demonstrate both terminal and browser recording.
- The source-controlled authoring model must be visible, not merely asserted.
- The representative demo should execute the same public bootstrap, build, and
  watch workflow taught by Getting Started. Private setup may isolate the demo,
  but visible commands and claimed results must remain authentic.
- Installation commands and the support matrix belong on Getting Started
  pages; the homepage carries only a concise compatibility statement.
- Avoid a dense feature-card wall. Every section must advance the evaluator
  from understanding the output to understanding why it is different.

### Documentation hierarchy

The documentation uses five content types, but only four appear in the primary
navbar. Concepts remains a compact sidebar category and a cross-link target;
it does not compete with the task-oriented entry points.

### Content-type contract

| Type | Reader question | Content rule |
| --- | --- | --- |
| Getting Started | How do I get one successful result quickly? | Short, copyable path with minimal explanation and no optional branches |
| Tutorial | How do the important parts work together? | One cumulative project in a deliberate teaching order |
| Concepts | Why does OmegaFlow work this way? | Explanations and mental models without procedural or exhaustive field coverage |
| Guides | How do I accomplish this specific task? | Standalone, goal-oriented procedures with explicit prerequisites |
| Reference | What exactly does this command or field mean? | Complete, precise contracts organized for lookup |

The same subject may be introduced in a tutorial, explained in Concepts, and
specified in Reference, but only one page owns the complete definition. Other
pages link to it instead of copying field tables or behavioral contracts.

### Route and navigation model

Primary navbar:

- **Get Started** -> `/getting-started/`
- **Tutorial** -> `/tutorial/`
- **Guides** -> `/guides/`
- **Reference** -> `/reference/`
- **GitHub** -> repository

Documentation sidebar:

1. Getting Started, expanded for a first-time visitor
2. Tutorial
3. Concepts
4. Guides
5. Reference

Every category has a useful landing page. Large categories use collapsed
subgroups, and the sidebar should not expand the complete CLI or action tree by
default. The structure must remain usable on mobile without requiring a long
scroll past unrelated reference pages.

#### Getting Started

- `/getting-started/`: what OmegaFlow is, who it is for, and the shortest path
  to a first result
- `/getting-started/install/`: installation, Python and ffmpeg/ffprobe
  prerequisites, required codec capabilities, and the supported platform matrix
- `/getting-started/first-video/`: bootstrap, build, watch, and one source edit
- `/getting-started/next-steps/`: choose the tutorial or a task-oriented guide

Getting Started targets roughly five to ten minutes. It uses the generated
test video as-is, demonstrates the active watch-and-rebuild loop, and stops
before teaching the full authoring model.

`nano` is not an OmegaFlow dependency and must not appear on the general
installation page. Declare it only as a prerequisite of the reproducible
tutorial editor workflow.

The installation contract names capabilities as well as executables: browser
state publication requires libwebp encoding, and captured browser motion
requires libx264/H.264. A concise tutorial preflight checks these together with
the browser extra, installed capture browser, nano, and narration credential.
Platform-specific package-manager commands stay on Installation and Supported
Platforms.

Keep environment preparation separate from authoring. The current release
documents and preflights local dependencies. A future Reploy-backed recording
environment may supply Python, terminal and browser runtimes, ffmpeg/ffprobe
codecs, nano, and related tools without changing the recording source or
tutorial concepts.

#### Tutorial

- One continuous, project-based curriculum
- Each milestone has a written procedure and a corresponding part of the
  continuous supporting demonstration
- The tutorial begins where Getting Started ends rather than repeating package
  installation and first-build instructions in full

The milestone structure is decided separately under D4.

#### Concepts

Start with one cohesive `/concepts/` page. Develop the substantive explanation
before choosing page boundaries; the current material does not justify four
small pages.

The page covers:

- project, recording workspace, video, scene, and beat
- actions versus checks
- presentation time, realtime, narration anchors, and authored waits
- the build metaphor: OmegaFlow rebuilds requested output while reusing
  unchanged intermediate narration and capture artifacts
- the build, watch, publish, and rebuild lifecycle
- collections as build and review shortcuts, not combined playback

Restore and update the earlier Mermaid recording-workflow diagram from
`website/docs/omegaflow-studio.md` at commit `b391d9270886`. The new version
must include terminal and browser capture, reusable intermediates, narration,
retiming, assembly, validation, and publishing.

Sequential collection playback is a possible future capability, not part of
the current collection contract.

#### Guides

##### Authoring

- Record a terminal workflow
- Control displayed commands and output
- Add expectations and checks
- Record a browser workflow
- Wait for browser state reliably
- Control the browser pointer and presentation
- Handle browser authentication, secrets, and redaction

##### Narration and playback

- Add or update narration
- Synchronize actions with narration
- Span narration across beats
- Choose presentation-time or realtime capture
- Add guided checkpoints
- Add synchronized terminal effects

##### Publishing

- Publish to Docusaurus
- Publish standalone HTML
- Embed and size a player responsibly

##### Operations

- Use watch while editing
- Diagnose and rebuild a stale or failed recording
- Inspect runs and generated output
- Clean generated state and control retention

#### Reference

##### Authoring reference

- Recording-file structure and frontmatter
- Scene and beat schema
- Terminal actions
- Browser actions and targets
- Timing scopes and completion conditions
- Checks
- Narration, anchors, and wait syntax
- Guides, player highlights, and synchronized effects

##### Configuration reference

- Project configuration
- Recording defaults
- Per-recording configuration
- CLI overrides and precedence

##### CLI reference

- Command syntax
- Build and check
- Bootstrap
- Watch
- List and collections
- Runs, inspect, and output
- Clean and garbage collection
- Complete option reference

##### Output reference

- Publishing configuration and surfaces
- Presentation and player contract
- Generated asset and runtime-state layout

#### Contributor material

Contributor setup and internal architecture should remain outside the main
authoring path and be linked from GitHub or a clearly separated maintainer
area.

### Decision D2: homepage hierarchy

**Recommendation:**

- primary action: **Build your first video**
- secondary action: **Read the docs**
- keep the representative demo in the initial desktop composition and
  immediately after the hero actions on mobile
- use the demo as the main proof, followed by a script-to-video explanation
- show only a concise compatibility statement on the homepage
- present Script, Build/watch/check, Publish and rebuild as the supporting
  lifecycle

This decision was required before finalizing the documentation taxonomy.

**Resolution:** approved. Keep the demo-led homepage, **Build your first
video** as the primary action, **Read the docs** as the secondary action, and
the script-to-video explanation as supporting proof.

### Decision D3: documentation taxonomy

**Recommendation:** approve the five content types with four primary navbar
destinations. Keep Concepts as a small sidebar category rather than folding its
cross-cutting explanations into tutorials, guides, or reference. Use one
canonical definition for each command, field, and behavior.

This decision was required before finalizing the tutorial curriculum.

**Resolution:** approved. Use four task-oriented navbar destinations and keep
Concepts as a small, cross-linked sidebar category.

## Proposed tutorial curriculum

### Tutorial shape

The tutorial begins after Getting Started. The reader has installed OmegaFlow,
run `omegaflow bootstrap=project`, and built and watched the generated
`test-video`. The tutorial then runs `omegaflow bootstrap=tutorial` to add a
separate cumulative learning workspace.

Use one written tutorial page with anchored milestones and one continuous
supporting walkthrough divided into meaningful beats. Do not publish one page
or one video per feature. Split the tutorial only after complete narration and
real runtime measurements show that navigation between substantial chapters is
useful.

The written procedure is authoritative and remains usable without media. The
supporting video demonstrates meaningful edits, errors, playback, and results;
it must not contain the only command or explanation.

### Supplied application: Tiny Canvas

The tutorial records a packaged local WYSIWYG SVG editor, provisionally named
Tiny Canvas. The application is supplied by OmegaFlow and copied into the
learner's workspace; the learner authors the recording rather than building the
sample application.

Tiny Canvas opens a nearly finished artsy sunset-beach SVG containing a sky,
sea, beach, sun, and one compound coconut-tree group. The tutorial changes the
title, moves the sun toward a semantic horizon target, repositions the tree,
saves the artwork, and exports `sunset-beach.svg`. The application uses a
deterministic standard-library local server, DOM-backed SVG objects, semantic
targets and destination anchors, disposable state, and no external network,
account, framework, or secret.

The finished learner-authored video has three beats:

1. **Inspect the draft** in the terminal and prove the known starting state.
2. **Refine the artwork** in the browser by changing the title and dragging the
   sun and tree before saving.
3. **Verify the result** in the terminal by exporting and checking the finished
   SVG.

This is a terminal-browser-terminal story with a visible before/after result.
Browser primitives are taught once while constructing the second beat; later
milestones reuse that beat to teach narration, guidance, and publishing.

### Application distribution and acceptance

Tiny Canvas and its immutable draft artwork are versioned package assets in the
wheel and source distribution. `omegaflow bootstrap=tutorial` materializes an
editable, user-owned copy only after confirming that `bootstrap=project` has
created a valid OmegaFlow project.

Automated acceptance must use the exact distribution under test:

1. Build the wheel and source distribution.
2. Install the wheel and documented browser dependencies in a clean
   environment.
3. Run `omegaflow bootstrap=project`, followed by
   `omegaflow bootstrap=tutorial`.
4. Exercise the supplied application, styled editor workflow, browser actions,
   narration, guide, build, watch, check, and publish commands through that
   environment's installed `omegaflow` entry point.
5. Build and check the completed tutorial recording and the continuous
   supporting walkthrough.
6. Build the production documentation site from their generated assets.
7. Prove that no command or artifact depends on the source checkout or an
   undeclared dependency.

The tutorial is therefore both documentation and an end-to-end compatibility
test for the released package.

### Tutorial prerequisites

- Complete Getting Started through the first successful `test-video` watch.
- Install the documented browser dependencies and ffmpeg/ffprobe capabilities.
- Install `nano` only for the tutorial's reproducible editor interaction.
- Add `OPENAI_OMEGAFLOW_API_KEY` to the generated private OmegaFlow secret file
  before the narration milestone.

The page states prerequisites and expected TTS cost before they are needed and
links to the support matrix instead of copying it.

### Default-driven authoring surface

Do not begin with the complete schema. Project and recording defaults carry
capture style, audio policy, output locations, publishing defaults, and other
values that do not distinguish this recording. Starting frontmatter should be
close to:

```yaml
---
id: sunset-beach
title: Refine a Sunset Beach Poster
---
```

`kind` defaults to `video` and is not repeated. Browser, narration, timing, and
publishing fields appear only when the corresponding milestone needs them.
Reference owns the complete schema.

### Milestone 1: Read and validate the starter

Run `omegaflow bootstrap=tutorial`, open the generated recording source in a
scripted, styled `nano` session, and identify frontmatter, the first beat, its
terminal action, and its check. Deliberately change `medium: terminal` to the
invalid `medium: shell`, build, and show OmegaFlow rejecting the source before
capture with a diagnostic that identifies the field and lists `terminal` and
`browser`. Correct it and build successfully.

This teaches source structure, beats, typed configuration, validation, and the
edit-build-watch loop. The supporting video must show the real editor and real
error; it may not fake the edit with a hidden replacement. The packaged nano
configuration gives recording syntax distinct colors, visible line numbers and
position, and clean shortcut rendering without modifying the learner's global
configuration.

### Milestone 2: Establish a repeatable baseline

Explain why the initial terminal beat is reliable rather than merely plausible:

1. Setup restores an editable SVG from the immutable packaged draft before
   capture.
2. The action runs the real Tiny Canvas inspector against that working file.
3. Checks require a valid SVG, the expected draft title, and the semantic
   `sun` and `coconut-tree` objects used later.

Build and watch the one-beat recording. A missing, malformed, or previously
modified draft must fail instead of producing a convincing stale video. Keep
visible commands approachable; do not use `sed` or other implementation tools
merely to present source excerpts.

### Milestone 3: Automate and review the artwork edit

Add the browser beat and final terminal verification beat. Teach browser
primitives once as one coherent operation:

- open Tiny Canvas with an explicit readiness boundary
- target the title semantically and replace its text
- drag the sun and tree between semantic DOM targets
- use component-relative percentage positions only as a fallback for pages
  without destination elements
- show a brief click indication and a persistent pressed-pointer state during
  each drag
- save and check the visible result
- export and verify the resulting SVG from the terminal

Build and play the complete silent three-beat video before adding TTS. Show the
original state, the browser edit, and the verified result. This is a required
working gate: narration cannot hide or compensate for an incorrect workflow.

Add beat-targeted watch so the learner and supporting walkthrough can open the
changed beat directly instead of replaying the whole video. Beat ids must be
validated, represented in stable watch URLs, and compose predictably with
autoplay, countdown, and guided playback.

### Milestone 4: Expose and fix narration timing

Configure narration with a voice distinct from the supporting tutorial's
narrator. First add narration without scheduling the existing browser actions
and play the affected beat. The title edit, drags, and save will race ahead of
the words describing them. Then add narration anchors and schedule those same
actions at the intended words.

The Tiny Canvas operations are short and do not need authored narration waits.
Explain briefly that a wait pauses narration for an asynchronous completion;
it does not turn presentation-time capture into realtime.

Use this milestone to teach the shared timing model:

- **presentation time** captures scriptable events that OmegaFlow may schedule
  and retime around narration
- **realtime** preserves an interval's internal elapsed motion and audio; the
  compiler may position that interval but may not stretch, compress, or reorder
  it

Demonstrate the result by playing the narrated sunset-beach video inside the
supporting tutorial. That outer browser interval is realtime and its narration
must pause until nested playback completes. This lesson is gated on generic
browser realtime capture with page audio and exact-once outer mixing; the
current silent dynamic-fragment implementation is insufficient.

### Milestone 5: Guide the viewer

Attach the existing textual guide/checkpoint card to the final terminal beat.
In guided mode it pauses after verification and shows the commands for
exporting the completed SVG and opening it in a browser. Multiple commands use
the plural **Copy commands** label. The same video remains coherent when guided
mode is disabled.

This introduces no new guide type or text recording medium. It demonstrates a
purposeful viewer task using the workflow already authored.

### Milestone 6: Build, publish, and maintain

Validate the completed recording and publish standalone HTML as the tutorial's
portable final outcome. Explain tracked source and public output versus ignored
runtime state. Make a small source change and rebuild to show that OmegaFlow
reuses unchanged capture and narration intermediates.

Docusaurus embedding belongs in a focused publishing guide, not the main
tutorial. The tutorial ends with a real published artifact that can be rebuilt
after source or application changes.

### Supporting walkthrough

- Use one continuous supporting video divided into meaningful beats, not six
  independent capability clips.
- Follow the same materialized workspace and cumulative source states as the
  written tutorial.
- Demonstrate real commands, errors, editor input, browser actions, audio,
  checks, guides, and published output; fabricated `printf` status is forbidden.
- Do not preview the finished video at the beginning. State the outcome briefly
  and reveal the finished playback naturally after the learner builds it.
- Keep only one prominent player on the tutorial page.
- Measure complete narrated runtime before deciding whether the single page or
  continuous video needs substantial chapter navigation.

### Prerequisite capabilities

Implement and prove these capabilities individually before tutorial authoring:

1. Typed `bootstrap=project` and mutual exclusion with `action`.
2. Deterministic recorded-command environments without wholesale host
   inheritance.
3. Private, scoped OmegaFlow TTS environment loading.
4. Correct terminal ANSI character-set playback.
5. Scripted realtime terminal input and synchronized nano editing.
6. Multi-line and disjoint narration-synchronized terminal highlights.
7. Semantic browser drag with component-relative fallback and visible click and
   held-pointer feedback.
8. Beat-targeted watch through the typed CLI and stable URLs.
9. Timing parity for terminal and browser actions, including authored browser
   completion conditions.
10. Realtime browser video plus page-audio capture, fixed-duration compilation,
    seeking, and exact-once mixing with outer narration.
11. Guarded `bootstrap=tutorial` materialization of packaged Tiny Canvas.

Each prerequisite is a separate vertical slice. Its tests must fail first, its
focused and relevant regression suites must pass, and a minimal user-facing
recording or command must visibly demonstrate the behavior before the next
prerequisite begins.

### Topics excluded from the main tutorial

- collections
- complete configuration and option tables
- every terminal and browser action
- detailed authentication, application secrets, and visual redaction flows
- manifest and media-fragment internals
- encoding choices
- run retention and garbage collection
- TTS implementation details
- Docusaurus-specific embedding

These topics remain available through Concepts, Guides, and Reference.

### Decision D4: tutorial project and curriculum

**Resolution:** approved after feedback consolidation. Use the packaged Tiny
Canvas sunset-beach application, a three-beat terminal-browser-terminal learner
artifact, one cumulative written tutorial, and one continuous supporting
walkthrough. Introduce browser primitives once, prove silent playback before
TTS, expose and fix narration timing, add one practical guide, and end with
standalone publishing. Determine any later page or video split from substantive
content and measured narrated runtime rather than a predetermined chapter count.

### Decision D4a: editor and media prerequisites

**Resolution:** approved. Use generic scripted realtime TUI input with styled
`nano` as its first declared client. Add semantic drag and pointer feedback,
beat-targeted review, and semantic timing parity across terminal and browser
actions. Nested narration is gated on generic realtime browser capture with page
audio; do not special-case the OmegaFlow player or claim that current silent
dynamic fragments satisfy the requirement.

## Current-to-target migration

The migration should be one coherent information-architecture change. It
should not preserve duplicate old and new page trees merely to reduce the size
of the first patch.

### Existing top-level and tutorial pages

| Current page | Target | Disposition |
| --- | --- | --- |
| `intro.md` | `/getting-started/` | Replace with the Get Started landing page; remove the old route |
| `quick-start.md` | Homepage demo source link plus `/getting-started/first-video/` | Remove the repository-specific guide, retain the demo asset, and merge reusable instructions into First Video |
| `tutorial/overview.md` | `/tutorial/` | Replace with the approved tutorial overview and prerequisites |
| `tutorial/quickstart.md` | `/getting-started/first-video/` | Merge into the canonical first-video page and remove the duplicate route |
| `tutorial/recording-file.md` | `/tutorial/#read-and-validate-the-starter` | Merge substantive source-authoring material into the single tutorial and remove the old route |
| `tutorial/beat.md` | `/tutorial/#establish-a-repeatable-baseline` | Merge substantive beat material into the single tutorial and link to Concepts and Reference |
| `tutorial/publishing.md` | `/tutorial/#build-publish-and-maintain` | Merge standalone publishing into the final tutorial milestone; move Docusaurus details to a guide |

Browser authoring, narration, timing, guided playback, and publishing are
anchored milestones on the same tutorial page rather than separate micro-pages.

### Existing recording-file documentation

| Current page | Target | Disposition |
| --- | --- | --- |
| `recording-files/overview.md` | `/concepts/` and `/reference/recording-files/` | Split explanatory model from file contract and remove the old route |
| `recording-files/config.md` | `/reference/configuration/recordings/` | Move and update; keep complete schema ownership here |
| `recording-files/beat.md` | Beat, terminal-action, browser-action, narration, guide, and effect reference pages | Split the monolithic page by lookup task and remove the old route |
| `recording-files/publishing-runtime.md` | Publishing guides plus `/reference/output/` | Split procedures from generated-output contracts |

The split pages must cross-link but must not duplicate complete schema tables.

### Existing CLI and configuration documentation

| Current page | Target | Disposition |
| --- | --- | --- |
| `omegaflow.md` | `/reference/cli/` | Replace with a compact CLI reference index and remove the old route |
| `cli/command-syntax.md` | `/reference/cli/syntax/` | Move with light edits |
| `cli/actions/bootstrap.md` | `/reference/cli/bootstrap/` | Move and update links to Getting Started |
| `cli/actions/build-check.md` | `/reference/cli/build-check/` | Move; preserve the relationship between build and check |
| `cli/actions/watch.md` | `/reference/cli/watch/` | Move and document the active source-rebuild loop |
| `cli/actions/list-clean.md` | `/reference/cli/list/` and `/reference/cli/maintenance/` | Split discovery from destructive or retention-oriented operations |
| `cli/actions/runs-inspect-output.md` | `/reference/cli/runs/` | Merge with the durable material from Runs and Troubleshooting |
| `cli/runs-troubleshooting.md` | Operations guides plus `/reference/cli/runs/` | Split task diagnosis from command contracts and remove duplicate run descriptions |
| `cli/overrides-parameters.md` | `/reference/configuration/overrides/` | Move with light edits |
| `cli/option-reference.md` | `/reference/cli/options/` | Move with light edits |
| `configuration.md` | `/reference/configuration/project/` and a short precedence explanation | Split project schema ownership from explanatory configuration layering |
| `video-output.md` | `/reference/output/presentation/` | Move as advanced reference |

### New pages without a current canonical source

- Installation and supported platforms
- First video
- Getting Started next steps
- One cohesive Concepts page covering the mental model, actions and checks,
  timing, build reuse, collections, and publishing lifecycle
- Terminal authoring guides
- Browser authoring and reliability guides
- Narration and synchronization guides
- Guided playback guide
- Docusaurus and standalone publishing guides
- Operational troubleshooting guides

These pages may reuse small, attributed portions of existing reference text,
but each receives content appropriate to its page type.

### Recording and generated-asset migration

| Current artifact | Disposition |
| --- | --- |
| `recordings/quickstart-demo` | Retain as the homepage demonstration; update its visible workflow to `bootstrap=project`, `test-video`, the generated private environment files, and the revised Getting Started path |
| `website/static/omegaflow-videos/quickstart-demo` | Preserve the public asset path and rebuild from the retained source |
| `recordings/tutorial/index.md` | Replace the collection with one continuous supporting walkthrough using the materialized Tiny Canvas workspace |
| `recordings/tutorial/*/index.md` | Remove all placeholder and micro-video members after the continuous walkthrough passes validation |
| `website/static/omegaflow-videos/tutorial` | Regenerate only from the completed continuous walkthrough; do not preserve placeholder output |
| `recordings/browser-recording-reference` | Retain as a browser integration/reference fixture, outside the tutorial curriculum |
| `recordings/browser-recording-narration-smoke` | Retain as an internal smoke fixture, outside public navigation |

The learner's generated `sunset-beach` recording remains distinct from the
repository-owned supporting tutorial walkthrough.

### Replacement and removal policy

Recommended policy:

1. Treat the unreleased website structure as internal and make a clean cut to
   the approved routes without compatibility redirects.
2. Do not keep duplicate Markdown pages at old paths.
3. Do not preserve tutorial recording ids as compatibility aliases;
   replace the collection and recordings atomically with the continuous
   walkthrough.
4. Keep the homepage demo's public asset URL stable.
5. Remove incomplete tutorial navigation during implementation if new milestones
   cannot land atomically; never leave a prominent link pointing to a mixture
   of old placeholders and new material.
6. Preserve internal browser fixtures, but do not present them as tutorial
   chapters.

### Implementation sequencing constraint

The implementation plan must avoid both a half-migrated public site and a batch
of unproven prerequisites:

1. Implement one prerequisite capability.
2. Run its focused and relevant regression tests.
3. Demonstrate it through a minimal user-facing command or recording and show
   what changed and why it works.
4. Review that vertical slice before starting the next prerequisite.
5. Only after all prerequisite gates pass, materialize Tiny Canvas and author
   the cumulative tutorial.
6. Build new website content off-navigation where necessary.
7. Switch navigation, canonical routes, homepage links, generated media, and
   removal of superseded pages in one reviewed migration.

## Repository changes incorporated

The design was checked against repository changes through 2026-07-21. The
following changes affect the website plan:

- the homepage player now starts paused and uses a full-height presentation on
  sufficiently tall desktop viewports
- embedded players coordinate audio ownership so starting one pauses another
  active embed
- `action=watch` monitors recording sources and rebuilds changed recordings
  while the local server is active
- the quickstart demo now runs the real bootstrap and build workflow in an
  isolated environment and validates its results with declarative checks
- player controls consistently use `beat` terminology

These changes reinforce the proposed demo-led homepage and early definition of
`beat`. They also make the watch-and-iterate loop a required tutorial concept.
They do not require a different top-level information architecture.

### Decision D5: migration policy

**Recommendation:** approve the clean migration described above: remove the
unreleased old routes without compatibility shims, remove duplicate pages and
placeholder tutorial recordings, retain the homepage demo asset at its current
suitable location, and keep browser reference fixtures outside the public
tutorial.

The packaged tutorial application uses the guarded bootstrap operation recorded
in D6; that decision does not change the content-migration policy.

**Resolution:** approved. Make a clean cut with no compatibility routes or
recording aliases, remove duplicate and placeholder content, retain internal
browser fixtures outside the tutorial, and keep the homepage demo asset at its
current suitable location without treating that location as a public contract.

## Tutorial bootstrap contract

### Tutorial workspace materialization

Bootstrap is a typed top-level OmegaConf operation selector:

```shell
omegaflow bootstrap=project
omegaflow bootstrap=tutorial
```

`bootstrap` accepts only `project` and `tutorial`. It is mutually exclusive with
`action`; supplying both is an error before any filesystem write. This is a
clean pre-release contract with no compatibility aliases.

`bootstrap=project` creates the minimal project environment and a disposable
`test-video` used by Getting Started. `bootstrap=tutorial` is a guarded
extension: it requires a valid `.omegaflow/config.yaml` and usable configured
recording directory, but it does not require the generated test video or a
populated TTS credential. If the project prerequisite is absent, it fails
without writing and tells the user to run `omegaflow bootstrap=project` first.

The generation contract is:

| Operation | Generated files |
| --- | --- |
| `bootstrap=project` | `.omegaflow/config.yaml`, `.omegaflow/.gitignore`, `.omegaflow/omegaflow-secret.env`, `recordings/config.yaml`, `recordings/.gitignore`, and `recordings/test-video/index.md` |
| `bootstrap=tutorial` | The packaged Tiny Canvas application and immutable draft artwork, an editable `recordings/sunset-beach/index.md`, the styled tutorial nano configuration, and required local tutorial support files |

The project `.omegaflow/.gitignore` contains
`/omegaflow-secret.env`; the leading slash anchors the rule to that directory.
The secret file begins with:

```dotenv
# OPENAI_OMEGAFLOW_API_KEY=
```

Create and validate the ignore rule before creating the restricted-permission
secret file. Refuse unsafe states in which it is tracked or staged. Do not
ignore the rest of `.omegaflow`, because project configuration belongs in
version control.

`recordings/.gitignore` contains `**/app.secret.env`. A recording-local
application secret file may sit beside its `index.md`, but OmegaFlow must still
refuse to load it when it is unignored, tracked, or staged.

All files go through the existing bootstrap writer. A file that already exists
is reported as `exists` and is not changed. `force=true` replaces existing
files, while `dry_run=true` and `dry_run=diff` preview every bootstrap-managed
file without writing. A partially existing workspace is handled file by file,
as it is today; bootstrap does not roll back files that were created
successfully.
The tutorial application creates mutable state only when run and never mutates
the immutable package resource.

### Environment and secret boundaries

Recorded commands do not inherit the host process environment wholesale.
OmegaFlow constructs only owned execution plumbing such as deterministic
command lookup, terminal/color behavior, private coordination variables, and
`OMEGAFLOW_VERSION`. Retain typed `environment.variables` for literal,
deterministic, non-secret application settings and `path_prepend` for explicit
command lookup. OmegaFlow never renders configured values in its own output;
documentation warns that this mapping is not secret storage.

`.omegaflow/omegaflow-secret.env` is private to scoped OmegaFlow service
operations. A `with_env(<allowlisted-name>)` boundary makes one value available
only while that operation runs; initially only TTS may request
`OPENAI_OMEGAFLOW_API_KEY`. Never pass it to recorded terminal or browser
processes, actions, checks, setup, cleanup, or applications. `llm-auth` is not
the default because its repository-root `.env` policy conflicts with valid
OmegaFlow subprojects.

Application secrets are separate. A recording declares required names through
typed `environment.secrets`. Resolve each name from exactly one source:

- local `app.secret.env` only: succeed
- explicitly allowlisted host variable only, for fileless CI: succeed
- both: ambiguity error
- neither: missing-secret error
- undeclared file entry: error

Register resolved values with redaction and publication validation, fail closed
if a recorded application emits one, and disable capture reuse whenever
application secrets are configured rather than fingerprinting secret values.

### Homepage bootstrap demonstration

Update the homepage demonstration atomically with this contract. Its visible
commands, narration, synchronized highlights, guide content, checks, and
generated public asset use `bootstrap=project` and `recording=test-video` and
show the new `.omegaflow/.gitignore` and
`.omegaflow/omegaflow-secret.env` files without displaying or inventing a
credential value.

## Proposed implementation plan

Implementation proceeds through independently reviewable vertical slices. Do
not start the next prerequisite merely because its code is adjacent. After
each slice, show the user the public syntax, the tests that establish its
contract, a real command or recording that demonstrates it, and how to inspect
the result. Stop for review before continuing.

### Per-prerequisite completion gate

Every prerequisite follows the same sequence:

1. Confirm the smallest user-facing contract and forbidden behavior.
2. Add a focused test that fails for the missing behavior.
3. Implement only that capability and preserve unrelated worktree changes.
4. Run focused tests, relevant regression suites, lint/type checks, and the
   installed-distribution path appropriate to the feature.
5. Build a minimal public demonstration using ordinary OmegaFlow source or CLI
   syntax rather than a private test hook.
6. Present the exact command, source excerpt, expected visible behavior, and
   generated artifact or player URL to the user.
7. Review and commit that slice before beginning the next prerequisite.

### Prerequisite 0: installed-distribution demonstration harness

Create the reusable clean-environment harness first. It builds the wheel and
source distribution, installs the wheel with declared extras, invokes the
installed `omegaflow` executable, and stores inspectable demo artifacts under a
temporary workspace. This is test infrastructure rather than a user feature,
but it makes every subsequent proof representative of release behavior.

Demonstration: show package paths, executable resolution, version, and one
successful minimal build from outside the source checkout.

### Prerequisite 1: typed project bootstrap

Implement `bootstrap=project`, mutual exclusion with `action`, the `test-video`
rename, safe existing-file behavior, and the minimal project file tree. Do not
implement tutorial materialization in the same slice.

Demonstration: bootstrap an empty directory, show the generated tree, build and
watch `test-video`, then show that `bootstrap=project action=build` fails before
writing.

### Prerequisite 2: deterministic recorded-command environment

Stop wholesale host-environment inheritance while retaining non-secret
`environment.variables`, `path_prepend`, OmegaFlow-owned terminal plumbing, and
automatic `OMEGAFLOW_VERSION`. This slice does not load a secret file.

Demonstration: an environment-probe recording shows that a configured
non-secret variable and `OMEGAFLOW_VERSION` reach the application while an
arbitrary host variable does not. Show both the recording source and captured
output.

### Prerequisite 3: private OmegaFlow TTS environment

Add `.omegaflow/.gitignore`, restricted
`.omegaflow/omegaflow-secret.env`, and scoped `with_env` access for TTS. Prove
that the credential is unavailable to recorded commands and is never rendered
by OmegaFlow. Keep recording-local application secrets out of this slice.

Demonstration: use a stubbed local TTS operation to prove scoped availability,
then run the environment probe to prove the same name is absent from the
recorded process. Show permissions, ignore behavior, and secret-safe output
without displaying a credential value.

### Prerequisite 4: faithful terminal control-sequence playback

Teach the terminal player to consume ANSI character-set designation sequences
and related control variants instead of rendering bytes such as `ESC ( B`.
Keep this parser correction separate from authoring realtime input.

Demonstration: replay a fixed nano cast containing the previously visible bad
sequence and show clean shortcut/status rendering in the generated player.

### Prerequisite 5: realtime terminal input

Implement generic persistent-PTY readiness, text and named-key input, control
keys, timing, process completion, and failure cleanup. Exercise styled `nano`
as the first client without special-casing it.

Demonstration: a recording opens a file in nano, makes and saves a small edit,
and exits cleanly. Show the source file before and after alongside the generated
video.

### Prerequisite 6: terminal highlight ranges

Extend narration-synchronized terminal highlighting to support multi-line
ranges and several disjoint text spans in one effect, with deterministic
matching and explicit repeated-text behavior.

Demonstration: highlight frontmatter, a beat declaration, and two separate
action fields at narration anchors in a short source walkthrough.

### Prerequisite 7: semantic browser drag and pointer feedback

Add a typed drag action using existing browser targets for `from` and `to`,
center defaults, and optional component-relative percentage positions. Add a
brief ordinary-click indication and a persistent pressed-pointer indication
for the duration of a drag.

Demonstration: drag two SVG objects between semantic targets in a small fixture
and show the generated player at normal and resized layouts. The recording
source must contain no absolute pixels.

### Prerequisite 8: beat-targeted watch

Add a typed watch override and stable URL representation that resolve a named
beat against the presentation snapshot loaded on refresh. Validate unknown ids
with the recording source and valid ids. Define interaction with autoplay,
countdown, and guided playback without adding a second watch implementation.

Demonstration: open the same recording directly at two named beats, show the URL
and CLI form, and prove that a new build does not alter an already playing
snapshot until refresh.

### Prerequisite 9: shared presentation/realtime timing contract

Give terminal and browser executable actions the same timing semantics, with an
optional inherited beat default. A realtime terminal command completes on
process exit; a realtime browser action uses an authored completion condition
such as `until`. The compiler may position a realtime interval but not stretch,
compress, or reorder it.

Demonstration: place a presentation-timed browser action beside a bounded
realtime browser animation, seek through both, and show their fixed versus
retimeable durations in generated metadata and playback.

### Prerequisite 10: realtime browser audio

Capture page audio with the realtime browser interval, preserve audio/video
alignment, mix it into the outer presentation exactly once, and define mute,
seek, playback-rate, failure, and publication behavior. Tests use deterministic
local audio; the public demonstration uses a nested OmegaFlow player with a
voice distinct from the outer narrator.

Demonstration: play a short narrated recording inside a realtime browser beat,
pause outer narration until the authored completion boundary, seek and replay,
and verify audibly and through media inspection that only one synchronized
inner stream exists.

### Prerequisite 11: tutorial materialization and Tiny Canvas

Implement `bootstrap=tutorial` as the guarded extension of an existing project.
Package Tiny Canvas, the draft artwork, styled nano configuration, semantic drag
anchors, starter terminal beat, and checks. Validate missing-project behavior,
package contents, installed materialization, idempotency, and absence of source-
checkout dependencies.

Demonstration: show failure in an unbootstrapped directory, then materialize the
tutorial after `bootstrap=project`, run Tiny Canvas, build the starter beat, and
inspect the generated workspace.

### Tutorial authoring phase

After all prerequisite reviews pass:

1. Author the six cumulative milestones on one written page.
2. Build the one continuous supporting walkthrough from the same workspace.
3. Keep the written instructions complete without video playback.
4. Build and validate the learner's three-beat sunset-beach recording, nested
   narration demonstration, guide, and standalone output through the installed
   distribution.
5. Measure complete narrated runtime and add chapter navigation only if the
   substantive content justifies it.

### Separate release slice: recording-local application secrets

Application secrets are approved product design but are not required by Tiny
Canvas or the main tutorial. Implement them after tutorial authoring as a
separate vertical slice: typed `environment.secrets`, recording-local
`app.secret.env`, fileless CI values, exactly-one-source validation, ignore and
tracked-file enforcement, redaction/publication checks, and disabled capture
reuse.

Demonstration: a local authentication fixture succeeds once from an ignored
file and once from an allowlisted CI variable, fails for both/neither/undeclared
sources, and publishes no secret value.

### Website migration phase

1. Implement the approved homepage and navigation hierarchy, including
   OmegaFlow-aware syntax highlighting and right-aligned GitHub navigation.
2. Build canonical Getting Started, Installation and Platforms, one cohesive
   Concepts page, Guides, Reference, and the tutorial.
3. Restore the updated Mermaid build-flow diagram.
4. Rebuild the homepage demonstration for `bootstrap=project`, `test-video`,
   and the private environment files.
5. Build the production site and generated videos off-navigation where needed.
6. Switch canonical routes and remove superseded pages, tutorial micro-videos,
   helper scripts, and placeholder assets in one reviewed change.
7. Add no compatibility redirects or aliases for the unreleased structure.

### Release validation phase

The implementation is ready for release review only after:

1. Wheel and source distribution build and install cleanly.
2. Both bootstrap operations pass path, safety, ignore, permission,
   idempotency, and installed-resource contracts.
3. Every documented tutorial command runs through the installed CLI.
4. The learner recording and continuous tutorial walkthrough build, check,
   publish, seek, guide, and play nested audio correctly.
5. Production Docusaurus build and desktop/mobile navigation smoke tests pass.
6. Player ownership, autoplay, guided checkpoints, browser-media diagnostics,
   and unsupported-platform messages pass focused browser tests.
7. No tutorial step depends on the source checkout, undeclared dependency, or
   leaked environment/secret value.
8. Repository CI and a final deep review pass.

## Validation criteria

The completed design must make it possible to answer:

1. What is OmegaFlow?
2. Who is it for?
3. What can it record?
4. What is a beat?
5. How do I install it on my platform?
6. How do I build and watch my first video?
7. How do I add terminal actions, browser actions, narration, and checks?
8. How do I publish and rebuild it?
9. Where is the complete contract for a field or command?
10. Which existing pages and recordings will be retained, moved, or removed?
11. When should an author use presentation time or realtime?
12. How are OmegaFlow service credentials separated from recorded application
    environments and secrets?
13. What visible proof must pass before each tutorial prerequisite may be
    considered complete?

The design is ready for prerequisite implementation only after D1 through D7
are recorded, the migration table covers every current public page and tutorial
recording, and the plan preserves both installed-distribution validation and
the one-feature-at-a-time demonstration gates.

## Decision log

| Decision | Status | Resolution |
| --- | --- | --- |
| D1: website job, audience, and product breadth | Approved | Evaluator-first homepage, author-first documentation, and a two-layer identity grounded in the current video product |
| D2: homepage hierarchy | Approved | Demo-led hero, Build your first video primary action, script-to-video proof, compact lifecycle |
| D3: documentation taxonomy | Approved | Four task-oriented navbar destinations plus one initially cohesive Concepts page in the documentation sidebar |
| D4: tutorial project and curriculum | Approved | One cumulative page and continuous walkthrough using packaged Tiny Canvas and a three-beat sunset-beach recording; prove silent browser workflow before narration and end with standalone HTML |
| D4a: editor and media prerequisites | Approved | Styled scripted nano input, semantic drag, beat-targeted watch, terminal/browser timing parity, and generic realtime browser audio are explicit gates before tutorial authoring |
| D5: migration policy | Approved | Clean cut with no compatibility routes; remove duplicate pages and tutorial placeholders; retain the suitable homepage demo asset location without promising compatibility |
| D6: bootstrap and environment contract | Approved | Use mutually exclusive `bootstrap=project|tutorial`; generate `test-video`, scoped OmegaFlow secrets, deterministic command environments, and a guarded packaged Tiny Canvas workspace |
| D7: prerequisite execution policy | Approved | Implement one prerequisite per vertical slice, test it first, prove it through the installed distribution, show its public behavior and artifacts, review and commit it, then start the next |
