---
sidebar_label: Bootstrap
slug: /reference/cli/bootstrap/
---

# Bootstrap

Bootstrap creates an OmegaFlow project layout:

```bash
omegaflow bootstrap=project
```

`bootstrap` is a typed setup operation, separate from recording `action`
values. Do not combine `bootstrap` and `action` in one invocation.

By default it writes:

```text
.omegaflow/
  .gitignore
  config.yaml
  omegaflow-secret.env
recordings/
  .gitignore
  config.yaml
  test-video/
    index.md
```

The project config records the workspace and local data paths. The recording
workspace config supplies starter capture, style, and audio defaults. The
test video is a small, self-contained two-beat recording that also exposes
the generated player's section and playback controls. It belongs to the project
and can be edited or removed.

The generated `.gitignore` files protect the placeholder OmegaFlow service
environment and recording-local application secret files. Bootstrap
refuses a service secret that is tracked, staged, or symlinked, and refuses any
generated-file target that is a symbolic link. When an ignore file already
exists, bootstrap preserves its rules and adds only the missing OmegaFlow rule.

When narration needs `OPENAI_OMEGAFLOW_API_KEY`, OmegaFlow first accepts an
explicit parent-process value for CI and otherwise reads
`.omegaflow/omegaflow-secret.env`. The value is passed directly to the audio
client without changing the process environment or exposing it to recorded
commands.

An application used by a recording may need its own secret. Declare its name
under `environment.secrets` in the recording and put the local value in
`app.secret.env` beside that recording's `index.md`. CI can provide the same
declared name directly in the parent process environment. These application
secrets are separate from OmegaFlow's narration credential; see
[Recording Configuration](../configuration/recordings.md#application-secrets).

## Choose the workspace

`workspace` changes the recording workspace:

```bash
omegaflow bootstrap=project workspace=demos
```

When `workspace` is omitted, it defaults to `studio.recording_dir`. The
generated example id is always `test-video`.

## Preview before writing

List the files bootstrap would create:

```bash
omegaflow bootstrap=project dry_run=true
```

Show their content as unified diffs:

```bash
omegaflow bootstrap=project dry_run=diff
```

Both preview modes leave the filesystem unchanged.
The private `omegaflow-secret.env` contents are never included in diff output.

## Existing files

Bootstrap preserves files that already exist. Use `force=true` only when you
intend to replace generated bootstrap targets:

```bash
omegaflow bootstrap=project force=true
```

Preview the diff first when running against an existing project.
`force=true` does not replace `omegaflow-secret.env` or discard existing
`.gitignore` rules.

After bootstrap, build the example named in its output:

```bash
omegaflow recording=test-video
```
