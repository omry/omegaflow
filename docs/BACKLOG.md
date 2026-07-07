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
