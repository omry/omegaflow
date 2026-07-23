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

- [ ] `P1` Replace the placeholder website tutorial with a complete guided
      tutorial. The website currently links prominently to a tutorial, but its
      chapter pages are skeletal, the checked-in tutorial recordings are
      placeholder terminal beats with narration disabled, and their generated
      artifacts are not embedded in the corresponding pages. Acceptance
      checks: define a coherent path from quickstart through recording files,
      beats, and publishing; replace placeholder copy and recordings with
      useful instruction; build and publish each recording into a predictable
      asset location; embed the relevant video in every chapter; keep written
      steps usable without video; verify navigation, production website build,
      and playback from the published site; and remove or relabel any tutorial
      entry point that still leads to incomplete content.

## Post-release

- [ ] `P2` Add sequential playback for recording collections. Collections
      currently provide shortcuts for building and reviewing related videos,
      but viewers must open each member separately. Acceptance checks: preserve
      each member as an independent presentation; add an optional collection
      experience that can continue to the next video in declared order; show
      the current video and upcoming member clearly; preserve direct links,
      guided checkpoints, and normal standalone playback; and cover completion,
      manual next/previous navigation, refresh, and missing-member failures.

- [ ] `P2` Add scripted input and synchronization to realtime terminal
      sessions. Realtime PTY capture already preserves live TUI output, but
      recording scripts cannot yet drive an interactive session or synchronize
      against its changing terminal state. Acceptance checks: inject text,
      Enter, and named control keys; wait for explicit output matches and
      bounded terminal-idle periods; preserve fixed terminal geometry, action
      timing, timeouts, exit validation, and reliable process-tree cleanup; add
      tests for input injection, synchronization success and failure, and
      cleanup; and add a secret-safe reference recording that submits multiple
      turns to a Codex chat and demonstrates an Arbiter-backed operation.

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

- [ ] `P2` Evaluate Reploy-backed recording and processing environments without
      replacing local mode. Produce a short architecture decision that compares
      a nested processing/recording environment with a non-nested
      controller/worker shape, defines the artifact and security boundary, and
      recommends one bounded prototype. Acceptance checks: identify the
      required processing and recording dependencies; account for casts,
      timelines, narration, logs, failure metadata, and published assets;
      validate whether safe nested control is available; prefer a narrow Reploy
      API over broad Docker-socket access; and leave the current local workflow
      and its dependency errors unchanged.

- [ ] Define and implement direct native Windows support, starting with
      browser-only recordings. Playwright makes browser capture useful on
      Windows even though asciinema cannot provide native terminal capture.
      Acceptance checks: run browser-only build, check, watch, narration, and
      publishing workflows on a Windows CI runner; remove or isolate POSIX-only
      assumptions in browser-only capture, lifecycle commands, diagnostics, and
      packaging; publish a Windows wheel that does not claim a bundled terminal
      recorder; fail terminal beats with a targeted capability message; define
      explicit support tiers for native Windows, WSL, and Linux/macOS; and
      evaluate a native terminal recorder separately from the browser milestone.

- [ ] `P2` Explore experimental recording support for Electron applications
      through Playwright. Electron provides a narrower semantic automation
      target than arbitrary native desktop applications and may extend browser
      beats to tools such as VS Code without defining a general desktop-control
      abstraction. Acceptance checks: prototype both a small Electron fixture
      and an isolated VS Code instance; validate launch, window lifecycle,
      semantic renderer and webview actions, pointer capture, and deterministic
      frames; compare direct Electron control with a DevTools-protocol
      connection; document native-menu, OS-dialog, inspection-fuse, graphical
      session, version, and platform limitations; keep the feature explicitly
      experimental; and make a go/no-go recommendation before committing to a
      stable recording contract.

- [ ] Explore recording native desktop applications as a cross-platform
      recording medium alongside terminal and browser beats. Desktop
      applications generally need a real rendered desktop even when nobody is
      watching it, so "headless" may mean an isolated unattended graphical
      session rather than no display server. Acceptance checks: define a common
      semantic action and capture contract for Windows, macOS, and Linux;
      evaluate platform adapters such as Windows UI Automation, macOS
      accessibility APIs, and Linux accessibility protocols without falling
      back to pixel coordinates as the primary interface; compare local virtual
      desktops, sandboxes, remote sessions, and disposable VMs as replaceable
      session backends; capture pixels inside the controlled session rather than
      depending on a remote-display stream; preserve semantic actions, pointer
      state, and deterministic frame output in the existing presentation model;
      document isolation, permissions, credentials, GPU, display-size, focus,
      and session-lifecycle constraints; and recommend one narrow prototype
      without making its platform or automation driver the product abstraction.

- [ ] Export recordings to a standard video file.
      OmegaFlow should produce a shareable video artifact in addition to its
      interactive terminal player. Acceptance checks: define a first format,
      likely H.264 MP4; include terminal playback and narration; preserve player
      timing; use the canonical video asset directory; document dependencies
      such as browser capture or ffmpeg; and validate that output is playable.

## Done

- [x] `P1` Show useful progress throughout video builds.
      Completed: `2026-07-18`. Added one determinate progress surface for
      recording actions, narration, assembly, and publishing. Interactive
      builds keep long active phases visible with elapsed time and a moving
      tracer while completed units remain truthful; cached and non-interactive
      paths stay concise, realtime capture retains the animated TUI, and
      successful, cached, forced, failed, narrow-terminal, and long-action
      paths are covered by tests.

- [x] `P2` Support narration-synchronized text highlighting in terminal beats.
      Completed: `2026-07-18`. Added typed terminal highlight effects timed by
      narration anchors, exact occurrence selection for repeated text,
      deterministic player rendering and clearing, author documentation,
      validation and playback tests, and a quickstart demonstration.

- [x] `P0` Prevent duplicate voiceover when playback starts before the website
      player has fully loaded. Completed: `2026-07-18`. Playback now remains
      disabled until visual media and narration ownership finish initializing,
      preventing an early click from starting a second audio path. Added a
      delayed-readiness regression and retained coverage for pause, seek,
      replay, and narration transitions.

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
