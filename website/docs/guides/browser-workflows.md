---
sidebar_label: Browser Workflows
slug: /guides/browser-workflows/
---

# Record a Browser Workflow

Browser beats capture semantic application states and interactions rather than
arbitrary screen pixels.

1. Start the application in setup or hand a URL from a terminal action to a
   named browser pane.
2. Open the page with an explicit readiness condition.
3. Prefer stable semantic targets such as `test_id`, role, label, or text.
4. Use browser primitives such as `click`, `type_text`, `drag`,
   `move_pointer`, and `reload_page`.
5. Wait for the state caused by an interaction before continuing.
6. Add checks for the visible or persisted result.

Use component-relative percentages only when the page has no meaningful DOM
destination. Authentication values belong in a recording-local
`app.secret.env` or declared CI source; do not put them in visible command
configuration.

See the [recording schema](/reference/recording-files/schema/#browser-beats)
for exact browser targets, completion conditions, and timing fields.

Use `reload_page` when the workflow republishes content at the current URL.
With full browser chrome, the presentation activates the refresh control and
then shows the reloaded state.
