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
    audio: website/static/audio/casts/getting-started.mp3
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
    path_prepend:
    - studio/recordings/getting-started/bin
  audio:
    enabled: true
    provider: openai
    env: OPENAI_OMEGAFLOW_API_KEY
    model: gpt-4o-mini-tts
    voice: marin
    format: mp3
```

Purpose: show a new user the first real loop: install the Studio CLI, create a
tiny recording, build it, play it in the terminal, and inspect the publish
surfaces.

Audience: someone evaluating OmegaFlow Studio for technical walkthroughs,
interactive docs, or reproducible terminal demos.

```yaml studio-directive
beat:
  id: install
  heading: Install The CLI
  narration: >-
    Start with the Studio command line tool. Install the package, then use the
    studio command to create and build recordings from Markdown scripts.
  marker: install
  caption: Install the Studio CLI.
  actions:
  - commands:
    - run: python -m pip install omegaflow-studio
      display: python -m pip install omegaflow-studio
      output:
        mode: fake
        text: |
          Successfully installed omegaflow-studio
  guide:
    commands:
    - python -m pip install omegaflow-studio
    success_hint: The install provides the studio command.
```

```yaml studio-directive
beat:
  id: create
  heading: Create A Small Recording
  narration: >-
    A recording is just Markdown with studio directive blocks. This tiny
    example records one command and declares two publish surfaces.
  marker: create
  caption: Create a one-command Studio recording.
  actions:
  - commands:
    - run: bash studio/recordings/getting-started/create-demo-project.sh
      display: |-
        python - <<'PY'
        from pathlib import Path

        root = Path("/tmp/omegaflow-hello")
        (root / "studio/conf").mkdir(parents=True, exist_ok=True)
        (root / "studio/recordings").mkdir(parents=True, exist_ok=True)
        (root / "docs").mkdir(parents=True, exist_ok=True)
        dollar = "$"
        (root / "studio/conf/config.yaml").write_text(f"""defaults:
          - studio_schema
          - override hydra/job_logging: disabled
          - override hydra/hydra_logging: disabled
          - _self_

        recording: null
        action: build
        load_env_file: false
        studio:
          data_dir: studio
          keep_output_dir: true
        hydra:
          output_subdir: null
          run:
            dir: {dollar}{{studio_run_dir:{{studio.data_dir}},{{action}},{{step}},{{dry_run}},{{recording}},{{now:%Y%m%d-%H%M%S}}}}
          job:
            chdir: false
        """)
        (root / "docs/hello.md").write_text("# Hello Video\n\n<!-- studio:hello-video:start -->\n<!-- studio:hello-video:end -->\n")

        fence = "`" * 3
        (root / "studio/recordings/hello.md").write_text(f"""# Hello Video

        {fence}yaml studio-directive
        scene: Hello Video
        {fence}

        {fence}yaml studio-directive
        recording:
          id: hello
          title: Hello Video
          capture:
            window_size: 72x14
            headless: true
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
                component: OmegaFlowVideo
              html:
                type: standalone_html
                file: site/videos/hello.html
          audio:
            enabled: false
        {fence}

        {fence}yaml studio-directive
        beat:
          id: hello
          heading: Say Hello
          narration: Print one line in the terminal.
          actions:
          - commands:
            - run: printf 'hello from OmegaFlow\\n'
        {fence}
        """)
        PY
      output:
        mode: fake
        text: |
          Wrote /tmp/omegaflow-hello/studio/conf/config.yaml
          Wrote /tmp/omegaflow-hello/studio/recordings/hello.md
          Wrote /tmp/omegaflow-hello/docs/hello.md
    - run: find /tmp/omegaflow-hello -maxdepth 3 -type f | sort
      display: find /tmp/omegaflow-hello -maxdepth 3 -type f | sort
      output:
        mode: fake
        text: |
          /tmp/omegaflow-hello/docs/hello.md
          /tmp/omegaflow-hello/studio/conf/config.yaml
          /tmp/omegaflow-hello/studio/recordings/hello.md
    - run: sed -n '1,70p' /tmp/omegaflow-hello/studio/recordings/hello.md
      display: sed -n '1,70p' /tmp/omegaflow-hello/studio/recordings/hello.md
  guide:
    commands:
    - sed -n '1,70p' /tmp/omegaflow-hello/studio/recordings/hello.md
    success_hint: The script defines one beat and publish surfaces.
```

```yaml studio-directive
beat:
  id: record
  heading: Record The Video
  narration: >-
    Now build the recording. Studio records a baseline cast, retimes it for
    viewing, checks alignment, and writes the selected publish surface.
  marker: record
  caption: Build the tiny video from the script.
  actions:
  - commands:
    - run: bash studio/recordings/getting-started/build-demo-project.sh
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
    - run: bash studio/recordings/getting-started/play-demo-project.sh
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
    - run: bash studio/recordings/getting-started/inspect-demo-project.sh
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
