---
id: quickstart
title: Quickstart
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.dir}/${id}.html
---

# Quickstart

This Markdown file is the source for one generated terminal video.

The YAML header names the recording, chooses output paths, and declares where
the finished video can be published. The prose explains the walkthrough for
readers and future maintainers. The fenced `studio-directive` blocks tell
OmegaFlow what to record.

```yaml studio-directive
scene: Quickstart
```

The scene is the title shown by the player. Beats are the steps in the video.
This beat runs a small shell script kept in this video's `scripts/` directory.

```yaml studio-directive
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
        - hello from quickstart
```

Publish surfaces in the header let the same recording write a standalone HTML
page. Add a docs surface when you want the build to update a documentation page.
