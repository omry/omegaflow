---
sidebar_label: Bootstrap
slug: /omegaflow/actions/bootstrap
---

# Bootstrap

Bootstrap creates an OmegaFlow project layout:

```bash
omegaflow action=bootstrap
```

By default it writes:

```text
.omegaflow/
  config.yaml
recordings/
  config.yaml
  quickstart/
    index.md
    scripts/
      hello.sh
```

The project config records the workspace and local data paths. The recording
workspace config supplies starter capture, style, and audio defaults. The
quickstart is a small working recording that belongs to the project and can be
edited or removed.

## Choose the workspace and recording id

`workspace` changes the recording workspace. `recording` changes the generated
example id and supports nested ids:

```bash
omegaflow action=bootstrap workspace=demos recording=tutorial/hello
```

When `workspace` is omitted, it defaults to `studio.recording_dir`.

## Preview before writing

List the files bootstrap would create:

```bash
omegaflow action=bootstrap dry_run=true
```

Show their content as unified diffs:

```bash
omegaflow action=bootstrap dry_run=diff
```

Both preview modes leave the filesystem unchanged.

## Existing files

Bootstrap preserves files that already exist. Use `force=true` only when you
intend to replace generated bootstrap targets:

```bash
omegaflow action=bootstrap force=true
```

Preview the diff first when running against an existing project.

After bootstrap, build the example named in its output:

```bash
omegaflow recording=quickstart
```
