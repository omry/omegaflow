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
- Include brief context and concrete acceptance checks.
- Move completed items out instead of keeping a long archive.
- After each focused phase, run the relevant OmegaFlow tests and, when the player
  or generated artifacts are affected, rebuild or check the affected recording.

## Now

- [ ] `P1` Rename the Python module from `omegaflow_studio` to `omegaflow`.
      The public tool and package are moving to OmegaFlow, but the importable
      module still carries the old Studio name. Acceptance checks: rename
      `src/omegaflow_studio` to the canonical `omegaflow` module; update entry
      points, imports, tests, generated schema/docs paths, and packaging
      metadata; search the repository for remaining `omegaflow_studio`
      references; and either remove or deliberately explain any leftover
      compatibility surface.

- [ ] `P1` Improve handling for missing `asciinema`.
      Recording and terminal playback currently fail with a clear but bare
      requirement error when `asciinema` 3.x is unavailable. First-run users
      need a better path forward. Acceptance checks: report the missing or
      incompatible `asciinema` version with an actionable install command for
      common environments; document the dependency in quickstart and install
      docs; consider whether OmegaFlow can vendor, bundle, or install a known
      compatible `asciinema` binary/package as the preferred path; make the
      tradeoff explicit if vendoring is not practical; and add tests for missing
      command, old version, and happy-path version detection.

- [ ] `post-release` Explore Reploy-backed recording environments without
      replacing local mode. Reploy can give OmegaFlow a reproducible recording
      environment with `asciinema` and other demo dependencies, but it also
      brings a Docker-backed workflow that may be too heavy for many projects.
      Acceptance checks: design an optional Reploy blueprint or environment
      path for managed recording dependencies; keep the current lightweight
      local mode fully supported; make mode selection explicit in tool config
      and CLI output; document when to use managed versus local recording; and
      ensure missing-dependency errors remain clear when users stay in local
      mode.

- [ ] `P1` Add automatic garbage collection for old recording runs.
      `recordings/.omegaflow/runs` can accumulate stale successful and failed
      runs quickly. Run retention should be controlled by the Studio/tool
      configuration, not by each recording script. Acceptance checks: add
      tool-config fields for enabling run GC and choosing retention limits such
      as count and/or age; apply GC after successful builds without deleting the
      current run; preserve enough failed-run data for debugging according to
      the configured policy; provide dry-run/reporting output for what would be
      removed; document the defaults; and add tests that cover successful runs,
      failed runs, current-run protection, and disabled GC.

- [ ] `P1` Polish the generated recording viewing experience.
      The current videos are pretty, but the pacing and terminal surface can
      feel messy: some waits are too long, some command output is not useful to
      viewers, and status/progress text can compete with the teaching content.
      Acceptance checks: review a generated demo end to end and identify every
      long pause, redundant line, noisy status block, and confusing transition;
      decide which output should be hidden, summarized, or rendered as progress;
      tune timing defaults or per-recording timing fields where the problem is
      systemic; keep real-time output only where it adds trust or visual value;
      update the quickstart demo as the primary fixture; and add regression
      checks or docs so future recordings do not drift back into noisy output.

- [ ] `P2` Bolster the README for the public OmegaFlow package.
      The README should be ready for people arriving from GitHub or PyPI.
      Acceptance checks: add useful badges, a concise product description,
      install and quickstart commands, links to docs and the repo, and a short
      explanation of generated videos; investigate whether the generated video
      can be represented directly in GitHub Markdown, and if GitHub blocks
      embedded video/HTML, choose the best fallback such as a thumbnail link,
      GIF, SVG/terminal cast preview, or docs-site link.

- [ ] `P2` Create an OmegaFlow logo and mascot direction.
      The website currently relies on text-only branding, which makes the
      navbar and public package surfaces feel unfinished. Acceptance checks:
      propose a small set of logo/mascot concepts that fit rebuildable terminal
      demos and generated voiceover; choose one direction; create source assets
      for the website navbar, docs favicon/social preview, and README/PyPI
      surfaces; verify the mark works on dark backgrounds and at small sizes;
      and document basic usage so future pages stay visually consistent.

- [x] `P1` Organize publishing output into one asset directory per video.
      Published surfaces should not scatter a video's cast, audio, player data,
      HTML, and support assets across unrelated paths. Acceptance checks: define
      a canonical output directory for each recording/video id; write all
      generated publish assets for that video under that directory; update
      publish surface defaults and docs to point at the new layout; avoid
      preserving duplicate legacy output paths unless a concrete compatibility
      need is identified; and add tests that prove two videos do not overwrite
      or share generated assets accidentally.

- [ ] `post-release` Export recordings to a standard video file.
      OmegaFlow should be able to produce a shareable video artifact in addition
      to the interactive terminal player surfaces. Acceptance checks: define a
      first supported format, likely H.264 MP4 for broad compatibility; include
      terminal playback and generated narration/audio when present; keep timing
      consistent with the interactive player; place the exported file inside
      the video's canonical asset directory; document any required system
      dependencies such as browser capture or ffmpeg; and add a smoke test or
      fixture-level validation that proves the export path produces a playable
      media file.

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
