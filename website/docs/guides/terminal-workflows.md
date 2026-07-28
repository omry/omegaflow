---
sidebar_label: Terminal Workflows
slug: /guides/terminal-workflows/
---

# Record a Terminal Workflow

Use a terminal beat when the meaningful work is expressed as commands, output,
or a terminal interface.

1. Put repeatable preparation in recording setup rather than a visible action.
2. Add a terminal beat with one or more `commands`.
3. Give important actions stable `id` values.
4. Add `expect` clauses for output or exit status that proves success.
5. Use `display` only when the audience-facing command should be simpler than
   the deterministic implementation.
6. Build, watch, and check the recording.

Use presentation-time commands for discrete work that can be retimed. Use a
realtime terminal action for a TUI or interval whose internal timing must be
preserved.

The complete terminal action and check fields are in the
[recording schema](/reference/recording-files/schema/#terminal-beats).
