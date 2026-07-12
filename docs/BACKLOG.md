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

- [ ] `P2` Create an OmegaFlow logo and mascot direction.
      The website currently relies on text-only branding, which makes the
      navbar and public package surfaces feel unfinished. Acceptance checks:
      propose a small set of logo/mascot concepts that fit rebuildable terminal
      demos and generated voiceover; choose one direction; create source assets
      for the website navbar, docs favicon/social preview, and README/PyPI
      surfaces; verify the mark works on dark backgrounds and at small sizes;
      and document basic usage so future pages stay visually consistent.

## Release backlog

- [ ] `P2` Evaluate browser recording support with Playwright.
      OmegaFlow may eventually support browser demos alongside terminal demos.
      The design should preserve the core value of generated narration,
      synchronization markers, and rebuildable scripts. Acceptance checks:
      evaluate whether Playwright's video, trace, screenshots, or a custom
      event timeline is the right capture layer; sketch a browser-action schema
      for navigation, clicks, typing, keyboard shortcuts, waits, and assertions;
      define how narration anchors and waits synchronize with browser actions;
      explore realistic mouse movement and keyboard pacing instead of terminal
      typing animation; identify requirements for deterministic viewport,
      network, assets, auth, and secrets handling; and recommend whether this
      should share the existing player/publish surfaces or use a dedicated
      browser-demo rendering path.

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

- [ ] `P1` Document narration anchors, command `after`, and audio wait markers.
      The Beat page only mentions `@anchor@`, `@wait:name+1s@`, and `after` in
      field tables. Authors need a short explanation of how narration anchors
      line up command starts, how waits pause narration until a command id
      finishes, and why YAML values like `after: "@install@"` must be quoted.
      Acceptance checks: add an authoring example with one anchor, one command
      id, one `after`, and one wait; explain that markers are removed from
      spoken narration; document supported wait units (`ms`, `s`); and note the
      current YAML quoting requirement.

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

- [ ] `P1` Let Space pause and resume terminal recording playback.
      The embedded player should support the standard video-player keyboard
      habit of toggling play/pause with Space. Acceptance checks: pressing Space
      toggles playback when the player has focus; text inputs or other editable
      controls are not hijacked; the visual play/pause affordance still appears;
      and tests cover the keyboard behavior.

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

## Post-release

- [ ] Explore Reploy-backed recording environments without replacing local
      mode. Reploy can give OmegaFlow a reproducible recording environment with
      `asciinema` and other demo dependencies, but it also brings a Docker-backed
      workflow that may be too heavy for many projects. Acceptance checks:
      design an optional Reploy blueprint or environment path for managed
      recording dependencies; keep the current lightweight local mode fully
      supported; make mode selection explicit in tool config and CLI output;
      document when to use managed versus local recording; and ensure
      missing-dependency errors remain clear in local mode.

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
