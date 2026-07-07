---
id: hello
title: Hello
publish:
  default: html
  surfaces:
    html:
      type: standalone_html
      file: ${outputs.dir}/${id}.html
---

# Hello

```yaml studio-directive
scene: Hello
```

```yaml studio-directive
beat:
  id: hello
  heading: Say Hello
  narration: Print one line in the terminal.
  actions:
  - commands:
    - run_file: hello/hello.sh
      display: bash hello/hello.sh
```
