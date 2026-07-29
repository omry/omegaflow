# Changelog

Notable user-visible changes to OmegaFlow are documented here.

<!-- towncrier release notes start -->

## 0.9.0 (2026-07-29)

### Features

- Author rebuildable videos as typed Markdown sources containing terminal,
  browser, visualization, and synchronized multi-pane beats.
- Script semantic browser navigation, clicks, drags, typing, key presses,
  assertions, waits, audio capture, and terminal-to-browser handoffs.
- Record terminal commands in presentation time or realtime, including
  fullscreen TUI interaction, scripted input, cursor state, and
  narration-synchronized text highlighting.
- Generate and reuse narration takes, align actions to narration anchors, and
  synchronize actions across independent pane streams.
- Initialize projects with `bootstrap=project`, then add the packaged Tiny
  Canvas learning workspace with `bootstrap=tutorial`.
- Run recorded commands in a deterministic environment, load private narration
  credentials separately, and declare recording-local application secrets
  explicitly.
- Build, inspect, and watch individual recordings or collections; target a
  named beat, pin playback to one immutable build, or serve without opening a
  browser.
- Publish stable standalone and Docusaurus presentation assets with guided
  checkpoints, responsive playback controls, section previews, and reusable
  media signatures.
- Show one truthful progress surface across capture, narration, assembly, and
  publishing, while retaining the completed result and reporting every updated
  or unchanged publish surface.
- Ship Linux and macOS wheels with the terminal recorder bundled, with browser
  recording available through the `browser` extra.

### Fixes

- Preserve narration words and intended pause boundaries across generated
  takes, browser delays, seeking, replay, and early player interaction.
- Encode browser motion as fast-start H.264 MP4 and preserve continuous frames,
  exact completion states, fades, and backward scrubbing without flicker or
  long-GOP stalls.
- Prevent abandoned VS Code player documents and early embedded-player clicks
  from starting overlapping voiceover.
- Render terminal control sequences, DEC line graphics, fullscreen updates,
  and cursor visibility faithfully.
- Keep compact narration focused on the active word and preserve the complete
  transport and 100% timeline on short and mobile players.
- Keep the recorded pointer visible on light content and honor per-beat browser
  and window chrome settings.
- Reject unsafe tracked secret files while degrading unavailable Git or
  Sapling inspection to a warning.
- Retain bounded recording history while protecting the current build and
  latest failure.

### Documentation

- Reorganize the website around Getting Started, one cumulative Tiny Canvas
  tutorial, Concepts, task-oriented Guides, and complete Reference material.
- Add installation and supported-platform guidance, written fallbacks for
  videos, and focused reference media for presentation effects.
- Refresh the homepage demonstration and brand system across the website,
  player, package documentation, favicon, and social metadata.

### Maintenance

- License OmegaFlow under the Mozilla Public License 2.0 while preserving
  bundled third-party license notices.
- Use `nox -s ci` as the canonical local and GitHub Actions validation entry
  point, with guarded release builds, trusted PyPI publishing, and GitHub
  Release artifacts.
