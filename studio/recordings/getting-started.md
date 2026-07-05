# Getting Started With OmegaFlow Studio

```yaml studio-directive
scene: Getting Started With OmegaFlow Studio
```

```yaml studio-directive
recording:
  id: getting-started
  title: Getting Started With OmegaFlow Studio
  capture:
    window_size: 90x24
    headless: true
    baseline_compressed: true
  style:
    color: true
    typing: true
    typing_min_delay: 0.02
    typing_max_delay: 0.06
    typing_space_delay: 0.03
    typing_punctuation_delay: 0.05
    typing_newline_delay: 0.12
    typing_seed: 5
  outputs:
    cast: website/static/omegaflow-videos/getting-started/getting-started.cast
  publish:
    default: docusaurus
    surfaces:
      docusaurus:
        type: docusaurus_mdx
        file: website/docs/quick-start.md
        placeholder: getting-started
        component: OmegaFlowVideo
      standalone_html:
        type: standalone_html
        file: website/static/omegaflow-videos/getting-started/index.html
  retime:
    typing_char_delay: 0.03
    typing_space_delay: 0.02
    typing_punctuation_delay: 0.04
    typing_newline_delay: 0.0
    post_enter_pause: 0.25
    post_command_pause: 0.55
    minimum_section_spacing: 0.6
  environment:
    working_directory: .
  audio:
    enabled: false
    provider: openai
    env: OPENAI_API_KEY
    model: gpt-4o-mini-tts
    voice: marin
    format: mp3
```

Purpose: show a new user the shape of OmegaFlow Studio: source script in,
website-ready OmegaFlow Video out.

Audience: someone evaluating OmegaFlow Studio for technical walkthroughs,
interactive docs, or reproducible terminal demos.

```yaml studio-directive
beat:
  id: overview
  heading: Start With A Script
  narration: >-
    OmegaFlow Studio treats a recording like a compiled artifact. You keep a
    script in the repository, then rebuild the video when the workflow changes.
    Start by looking at the project layout.
  marker: overview
  caption: Start from a versioned Studio script.
  actions:
  - commands:
    - run: find studio -maxdepth 2 -type f | sort
  guide:
    commands:
    - find studio -maxdepth 2 -type f | sort
    success_hint: The getting-started script should appear under studio/recordings.
```

```yaml studio-directive
beat:
  id: build
  heading: Build An OmegaFlow Video
  narration: >-
    The studio command composes config, checks the script, records terminal
    actions, and publishes website assets. A dry run shows the
    build shape without recording anything yet.
  marker: build
  caption: Use the Studio CLI to build or inspect video outputs.
  actions:
  - commands:
    - run: >-
        python -c "print('build plan: record -> retime -> publish')"
      display: studio recording=getting-started action=build dry_run=true
  guide:
    commands:
    - studio recording=getting-started action=build dry_run=true
    success_hint: The command should print the build plan for this recording.
```
