# OmegaFlow Backlog

## Agent instructions

When helping with OmegaFlow backlog work, treat this file as the active planning
surface for media recording, retiming, audio, playback, and publishing tooling.
Keep items concrete and operator-facing. Prefer small fixes that improve the
authoring or viewing workflow over broad rewrites.

Use this backlog for OmegaFlow-specific work. Use the main Arbiter backlog for
server, client, plugin, deployment, and product-security work.

## How to use this file

- Keep each item small enough for one focused change.
- Put only the most urgent OmegaFlow items in `Now`.
- Keep pre-release work that is not currently active in `Release backlog`, and
  work explicitly deferred until after release in `Post-release`.
- Include brief context and concrete acceptance checks.
- When an item is completed, move it to `Done` and add its completion date in
  `YYYY-MM-DD` form. Do not leave checked items in `Now`.
- Done items are a short-term changelog, not a permanent archive. Remove entries
  whose completion date is more than one month old.
- After each focused phase, run the relevant OmegaFlow tests and, when the player
  or generated artifacts are affected, rebuild or check the affected recording.

## Now

No active release blockers.

## Release backlog

- [ ] `P1` Show useful progress throughout video builds.
      Long-running builds should not appear stalled while OmegaFlow records the
      workflow, generates narration audio and timestamps, or performs other
      expensive processing. Acceptance checks: show clear progress for the
      recording and audio phases; fold any other meaningfully slow build phases
      into the same progress experience; report the current operation and
      determinate totals when they are known; preserve readable non-interactive
      logs; avoid long silent periods; and add coverage for successful, cached,
      forced, and failed builds.

## Post-release

- [ ] `P2` Support narration-synchronized text highlighting in terminal beats.
      Recording authors should be able to emphasize existing terminal text at
      a narration cue so the viewer can follow the exact output being
      discussed. Acceptance checks: declare the target text and timing from the
      recording script; show and clear the highlight without changing terminal
      output; keep highlighting deterministic across playback, seeking, and
      replay; report missing or ambiguous targets clearly; and add player and
      synchronization tests.

- [ ] `P2` Add scripted interactive terminal capture for TUI sessions.
      OmegaFlow should be able to record a real interactive terminal program,
      including a Codex chat that invokes an installed Arbiter capability,
      without reducing the session to buffered command output. Acceptance
      checks: run the target command in a child PTY with the recording's fixed
      terminal geometry; let recording scripts inject text, Enter, and named
      control keys; stream ANSI and cursor updates into the existing asciinema
      capture as they happen; provide deterministic synchronization using
      explicit output matches and bounded terminal-idle waits; preserve action
      timing, timeouts, exit validation, and reliable process-tree cleanup; add
      tests for input injection, streaming output, synchronization failure, and
      cleanup; and add a secret-safe reference recording that runs Codex with
      inline terminal output, submits more than one chat turn, and demonstrates
      a real Arbiter-backed operation.

- [ ] `P2` Add selectable player color themes.
      Recordings should be able to choose a named color theme for the generated
      player instead of every player using the same palette. This is especially
      important when one recording shows another OmegaFlow player: the recorded
      inner player should use a contrasting theme so its controls and frame are
      visually distinct from the outer player. Acceptance checks: define the
      theme setting at the recording/player level; preserve the current palette
      as the default; apply themes consistently to player chrome, controls,
      terminal, and browser presentation surfaces; update the homepage combined
      demo to use different outer and recorded-player themes; add configuration
      and rendering tests; and manually verify contrast and readability.

- [ ] `P2` Add support for selecting a thumbnail frame.
      Published videos need a stable preview image for docs, GitHub fallbacks,
      social cards, and standard video exports. Acceptance checks: add a
      recording-level field for choosing the thumbnail frame by timestamp,
      marker, or beat id; generate the thumbnail into the video's canonical
      asset directory; use it in supported publish surfaces; provide a sensible
      default when no thumbnail is configured; and add tests or fixture checks
      that prove the selected frame is deterministic.

- [ ] `P1` Support publishing OmegaFlow videos to GitHub surfaces.
      Authors should have a GitHub-friendly way to share a generated recording,
      ideally preserving the embedded OmegaFlow player where GitHub allows it.
      Acceptance checks: identify which GitHub surfaces can host or link the
      interactive player, such as GitHub Pages, release assets, PR comments, or
      README Markdown; define the generated artifact layout and URLs for that
      surface; provide the best README/Markdown fallback when GitHub strips
      embedded HTML or JavaScript, such as a thumbnail link, GIF, or exported
      MP4; document the tradeoffs clearly; and add a sample publish surface that
      can be validated without requiring secrets in normal tests.

- [ ] `P2` Explore a YAML-safe sync marker syntax.
      The current `@anchor@` syntax is compact in narration, but problematic
      when reused as a YAML scalar (`after: @install@` is invalid unless
      quoted). Consider a clearer split: inline narration markers such as
      `[cue:install]` and `[wait:install_command +300ms]`, plus plain YAML ids
      such as `after: install`. Acceptance checks: compare readability against
      current `@anchor@` syntax; decide whether `after` should accept bare ids;
      define migration behavior for existing recordings; update docs and tests
      only after the syntax decision is made.

- [ ] `P2` Design a short hello-recording tutorial curriculum.
      The current `hello` recording is useful as a tiny fixture, but a separate
      tutorial track could use it to explain how OmegaFlow recordings are
      authored. Acceptance checks: outline a small lesson sequence covering the
      recording file, scene, beat, support script, output expectation, and
      publish surface; keep it separate from the default hello script until the
      tutorial direction is approved; and identify which parts belong in a
      future video versus written docs.

- [ ] `P1` Add a generic prompt system for OmegaFlow recordings.
      The recorder currently treats `$` as the canonical prompt, with a small
      virtualenv-prefix special case. Recordings should be able to declare the
      prompt they want, and the recorder, retimer, alignment checks, and player
      should agree on it. Acceptance checks: add a recording-level prompt
      setting; use it when printing visible prompts and synthetic retimed
      prompts; update prompt detection to avoid hard-coded `$` matching; keep
      the current `$` behavior as the default; and add tests for a non-`$`
      prompt.

- [ ] `P2` Avoid treating first focus-click as a playback toggle when possible.
      Clicking inside the video can move focus from the surrounding page into
      the embedded player. Ideally, that first focus click should not also pause
      or resume playback, because the user's intent may only be to focus the
      player. This is subtle and should not make click behavior flaky.
      Acceptance checks: evaluate whether browser focus state can distinguish a
      focus-only click reliably; if reliable, suppress playback toggle only for
      that first focus-transfer click; preserve normal click-to-toggle behavior
      once the player already has focus; add tests for the supported case; and
      defer explicitly if the behavior is too browser-dependent to make robust.

- [ ] Explore Reploy-backed recording and processing environments without
      replacing local mode. Reploy could provide two complementary isolation
      layers: a reusable processing environment containing OmegaFlow,
      `asciinema`, Chromium, fonts, `ffmpeg`, and `ffprobe`; and a clean,
      disposable environment per recording containing the demo's declared
      dependencies, persistent recording shell, and only the minimal injected
      recorder tooling needed by the selected backend. Evaluate one-shot
      processing for CI and release builds plus reusable sessions for local
      iteration.
      Acceptance checks: define the artifact boundary between the environments,
      including casts, timelines, narration, logs, failure metadata, and
      published video; evaluate whether a Reploy processing environment can
      safely create and control a nested Reploy recording environment; define a
      non-nested controller/worker fallback if nesting is unavailable; compare
      recording a remote PTY with copying a platform-compatible `asciinema`
      binary and minimal OmegaFlow runner into the recording environment; keep
      the current lightweight local mode fully supported; avoid requiring broad
      Docker-socket access where a narrower Reploy API is available; make mode
      and lifecycle selection explicit in tool config and CLI output; document
      when to use managed versus local execution; and keep missing-dependency
      errors clear in local mode.

- [ ] Decide the Windows support shape.
      Native Windows cannot currently record terminal sessions through
      asciinema, but OmegaFlow can still support artifact-processing workflows
      such as watching existing recordings, updating audio, retiming casts, and
      publishing surfaces. Acceptance checks: inventory recorder-free commands;
      define support tiers for native Windows, WSL, and Linux/macOS; evaluate a
      cross-platform recorder when practical; make supported commands explicit;
      and document the recommended Windows path.

- [ ] Export recordings to a standard video file.
      OmegaFlow should produce a shareable video artifact in addition to its
      interactive terminal player. Acceptance checks: define a first format,
      likely H.264 MP4; include terminal playback and narration; preserve player
      timing; use the canonical video asset directory; document dependencies
      such as browser capture or ffmpeg; and validate that output is playable.

## Done

- [x] `P1` Add Towncrier release notes and GitHub Releases to publishing.
      Completed: `2026-07-15`. Added generated and validated release notes,
      duplicate-publication protection, exact version/tag checks, trusted PyPI
      publishing, GitHub Release creation with the built distributions, and a
      maintainer release procedure.

- [x] `P2` Evaluate browser recording support with Playwright.
      Completed: `2026-07-15`. Implemented semantic browser capture with
      deterministic actions, narration synchronization, mixed terminal/browser
      playback, dedicated rendering, and closed presentation bundles. Physical
      iOS/Android validation remains deferred.

- [x] `P1` Update bootstrap quickstart so it does not create `hello.sh`.
      Completed: `2026-07-15`. Replaced the generated support script with a
      self-contained inline command and expected-output check, added concise
      narration synchronization guidance, and updated tests, documentation,
      and the rebuilt homepage demo.

- [x] `P1` Let Space pause and resume terminal recording playback.
      Completed: `2026-07-13`. Space now toggles playback when the player surface
      has focus, preserves native behavior for editable and interactive
      controls, ignores modified or repeated shortcuts, and shows the existing
      play/pause feedback. Added keyboard behavior tests.

- [x] `P2` Create and integrate the OmegaFlow logo and mascot direction.
      Completed: `2026-07-12`. Added the Night Studio logo and mascot system,
      integrated it into the website navbar, favicon, homepage, social metadata,
      README/PyPI surface, and recording player, and documented usage.

- [x] `P1` Document narration anchors, command `after`, and audio wait markers.
      Completed: `2026-07-12`. Added a complete synchronization example and
      documented anchor timing, command ids, waits, supported units, marker
      removal, audio requirements, and YAML quoting.

- [x] `P2` Bolster the README for the public OmegaFlow package.
      Completed: `2026-07-12`. Added a public-facing product description,
      badges, quickstart, documentation and demo links, and moved repository
      development details into a maintainer guide.

- [x] `P1` Add automatic garbage collection for old recording runs.
      Completed: `2026-07-11`. Added configurable, age-based run cleanup with
      dry-run support, current-run protection, documentation, and tests.

- [x] `P1` Polish the generated recording viewing experience.
      Completed: `2026-07-11`. Shortened and reviewed the homepage demo,
      reduced output noise, and fixed narration seeking while scrubbing.

- [x] `P1` Publish Linux and macOS wheels with a bundled recorder.
      Completed: `2026-07-09`. Added platform wheels with a bundled asciinema
      recorder, runtime selection, packaging validation, documentation, and
      tests.

- [x] `P1` Rename the Python module to `omegaflow`.
      Completed: `2026-07-09`. Aligned the package, import path, entry point,
      tests, generated documentation, and packaging metadata on the canonical
      OmegaFlow name.

- [x] `P1` Organize publishing output into one asset directory per video.
      Completed: `2026-07-08`. Consolidated each video's generated player,
      cast, audio, metadata, and support files into one canonical asset
      directory with collision coverage.
