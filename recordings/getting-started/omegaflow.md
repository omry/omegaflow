---
id: getting-started
title: Getting Started With OmegaFlow
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
  audio: website/static/audio/casts/getting-started.mp3
publish:
  default: docusaurus
  surfaces:
    docusaurus:
      type: docusaurus_mdx
      file: website/docs/quick-start.md
      placeholder: getting-started
      component: VideoPlayer
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
  path_prepend:
  - recordings/getting-started/bin
audio:
  enabled: true
  provider: openai
  env_file: .env
  env: OPENAI_OMEGAFLOW_API_KEY
  model: gpt-4o-mini-tts
  voice: marin
  format: mp3
---

# Getting Started With OmegaFlow

```yaml studio-directive
scene: Getting Started With OmegaFlow
```

Purpose: show a new user the first real loop: install the OmegaFlow CLI, create a
tiny recording, build it, play it in the terminal, and inspect the publish
surfaces.

Audience: someone evaluating OmegaFlow for technical walkthroughs,
interactive docs, or reproducible terminal demos.

```yaml studio-directive
beat:
  id: install
  heading: Install The CLI
  narration: >-
    Start with the OmegaFlow command line tool. Install the package, then use the
    studio command to create and build recordings from Markdown scripts.
  marker: install
  caption: Install the OmegaFlow CLI.
  actions:
  - commands:
    - run: python -m pip install omegaflow
      display: python -m pip install omegaflow
      output:
        mode: fake
        text: |
          Successfully installed omegaflow
  guide:
    commands:
    - python -m pip install omegaflow
    success_hint: The install provides the studio command.
```

```yaml studio-directive
beat:
  id: create
  heading: Create A Small Recording
  narration: >-
    A recording is Markdown with a YAML header, explanatory prose, and studio
    directive blocks. This small example records one support script and declares
    two publish surfaces.
  marker: create
  caption: Create a tiny but complete recording.
  actions:
  - commands:
    - run: bash recordings/getting-started/scripts/create-demo-project.sh
      display: |-
        python - <<'PY'
        from pathlib import Path

        root = Path("/tmp/omegaflow-hello")
        (root / "recordings/hello/scripts").mkdir(parents=True, exist_ok=True)
        (root / "docs").mkdir(parents=True, exist_ok=True)
        (root / "recordings/config.yaml").write_text("""capture:
          window_size: 72x14
          headless: true
          baseline_compressed: true
        audio:
          enabled: false
          provider: openai
          env: OPENAI_API_KEY
          model: gpt-4o-mini-tts
          voice: marin
          format: mp3
        """)
        (root / "docs/hello.md").write_text("# Hello Video\n\n<!-- studio:hello-video:start -->\n<!-- studio:hello-video:end -->\n")
        (root / "recordings/hello/scripts/hello.sh").write_text("""#!/usr/bin/env bash
        set -euo pipefail

        printf 'hello from OmegaFlow\\n'
        """)
        (root / "recordings/hello/scripts/hello.sh").chmod(0o755)

        fence = "`" * 3
        (root / "recordings/hello/omegaflow.md").write_text(f"""---
        id: hello
        title: Hello Video
        outputs:
          cast: site/videos/hello.cast
        publish:
          default: docs
          build_surfaces:
          - docs
          - html
          surfaces:
            docs:
              type: docusaurus_mdx
              file: docs/hello.md
              placeholder: hello-video
              component: VideoPlayer
            html:
              type: standalone_html
              file: site/videos/hello.html
        ---

        # Hello Video

        This Markdown file is the source for one generated terminal video.

        The YAML header names the recording, chooses output paths, and declares where
        the finished video can be published. The prose explains the walkthrough for
        readers and future maintainers. The fenced `studio-directive` blocks tell
        OmegaFlow what to record.

        {fence}yaml studio-directive
        scene: Hello Video
        {fence}

        The scene is the title shown by the player. Beats are the steps in the video.
        This beat runs a small shell script kept in this video's `scripts/` directory.

        {fence}yaml studio-directive
        beat:
          id: hello
          heading: Say Hello
          narration: Run the support script and verify the terminal output.
          caption: A one-command terminal recording.
          actions:
          - commands:
            - run_file: scripts/hello.sh
              display: bash scripts/hello.sh
              expect:
                output_contains:
                - hello from OmegaFlow
        {fence}

        Publish surfaces in the header let the same recording update docs or write a
        standalone HTML page.
        """)
        PY
      output:
        mode: fake
        text: |
          Wrote /tmp/omegaflow-hello/recordings/config.yaml
          Wrote /tmp/omegaflow-hello/recordings/hello/omegaflow.md
          Wrote /tmp/omegaflow-hello/recordings/hello/scripts/hello.sh
          Wrote /tmp/omegaflow-hello/docs/hello.md
    - run: find /tmp/omegaflow-hello -maxdepth 4 -type f | sort
      display: find /tmp/omegaflow-hello -maxdepth 4 -type f | sort
      output:
        mode: fake
        text: |
          /tmp/omegaflow-hello/docs/hello.md
          /tmp/omegaflow-hello/recordings/config.yaml
          /tmp/omegaflow-hello/recordings/hello/omegaflow.md
          /tmp/omegaflow-hello/recordings/hello/scripts/hello.sh
    - run: sed -n '1,95p' /tmp/omegaflow-hello/recordings/hello/omegaflow.md
      display: sed -n '1,95p' /tmp/omegaflow-hello/recordings/hello/omegaflow.md
  guide:
    commands:
    - sed -n '1,95p' /tmp/omegaflow-hello/recordings/hello/omegaflow.md
    success_hint: The file shows config, prose, directives, and publish surfaces.
```

```yaml studio-directive
beat:
  id: record
  heading: Record The Video
  narration: >-
    Now build the recording. OmegaFlow records a baseline cast, retimes it for
    viewing, checks alignment, and writes the selected publish surface.
  marker: record
  caption: Build the tiny video from the script.
  actions:
  - commands:
    - run: bash recordings/getting-started/scripts/build-demo-project.sh
      display: studio recording=hello action=build
      output:
        mode: fake
        text: |
          pass wrote recording: site/videos/hello.cast
          pass wrote retimed cast: site/videos/hello.retimed.cast
          pass wrote publish surface: docs/hello.md
          pass wrote publish surface: site/videos/hello.html
      expect:
        file_exists:
        - /tmp/omegaflow-hello/site/videos/hello.retimed.cast
        - /tmp/omegaflow-hello/docs/hello.md
        - /tmp/omegaflow-hello/site/videos/hello.html
  guide:
    commands:
    - studio recording=hello action=build
    success_hint: The build writes a retimed cast and publish surfaces.
```

```yaml studio-directive
beat:
  id: play
  heading: Play It In The Terminal
  narration: >-
    You can review the generated video without opening a browser. The play
    action replays the retimed asciinema cast directly in the terminal.
  marker: play
  caption: Play the generated cast in the terminal.
  actions:
  - commands:
    - run: bash recordings/getting-started/scripts/play-demo-project.sh
      display: studio recording=hello action=play
      output:
        mode: fake
        text: |
          hello from OmegaFlow
      expect:
        output_contains:
        - hello from OmegaFlow
  guide:
    commands:
    - studio recording=hello action=play
    success_hint: The terminal should replay the generated cast.
```

```yaml studio-directive
beat:
  id: publish
  heading: Understand Publish Surfaces
  narration: >-
    Publish surfaces describe where the finished recording is embedded. The same
    recording can update Docusaurus MDX for docs and write standalone HTML for
    a direct browser page.
  marker: publish
  caption: Inspect the configured publish surfaces.
  actions:
  - commands:
    - run: bash recordings/getting-started/scripts/inspect-demo-project.sh
      display: studio recording=hello action=build dry_run=true
      expect:
        output_contains:
        - "Publish surfaces:"
        - "type: docusaurus_mdx"
        - "type: standalone_html"
  guide:
    commands:
    - studio recording=hello action=build dry_run=true
    success_hint: The dry run lists the Docusaurus and standalone HTML surfaces.
```
