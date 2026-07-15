# Changelog

Notable user-visible changes to OmegaFlow are documented here.

<!-- towncrier release notes start -->

## 0.9.0 (2026-07-15)

### Features

- Generate a self-contained bootstrap quickstart with an inline command,
  expected output validation, narration synchronization guidance, and no
  support script.
- Open `action=watch` playback in an available system browser with autoplay.
  WSL uses the Windows host browser so playback uses the host audio stack.
- Present terminal and browser beats in one responsive player with framed
  browser windows, scalable recorded chrome, keyboard playback controls,
  bidirectional speed selection, and countdown-aware scrubbing.
- Publish each recording as one closed presentation bundle containing its
  player, timeline, narration metadata, terminal and browser payloads, and
  media assets.
- Record deterministic browser workflows alongside terminal commands, including
  navigation, clicks, typing, keyboard shortcuts, waits, assertions, and mixed
  terminal/browser beats synchronized to one narration track.
- Ship platform-specific Linux and macOS wheels with the recorder bundled,
  while keeping browser recording dependencies available through the `browser`
  extra.

### Fixes

- Clean up expired recording runs automatically while protecting the active
  run, with configurable retention and dry-run support.
- Improve playback reliability at boundaries: nested progress reaches
  completion, scrubbing cancels the autoplay countdown, narration resumes
  without inserted mid-sentence holds, and closed watch clients no longer print
  broken-pipe traces.

### Documentation

- Add the OmegaFlow logo and mascot across the website, package documentation,
  player, favicon, and social metadata.
- Expand the README, quickstart, recording-file reference, and synchronization
  documentation for narration anchors, action timing, expected output, browser
  recording, playback, and publishing.

### Maintenance

- Align the Python module, command entry point, tests, generated documentation,
  and package metadata on the canonical `omegaflow` name.
- Use `nox -s ci` as the canonical Python validation entrypoint for tests,
  schema documentation, and release notes, while keeping package builds,
  browser and media dependencies, and generated quickstart coverage explicit
  in GitHub Actions.
