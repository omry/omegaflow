---
sidebar_label: Narration And Synchronization
slug: /guides/narration-synchronization/
---

# Add Narration And Synchronize Actions

Add narration after the silent workflow is already correct.

1. Configure the narration provider and private service credential.
2. Add spoken text to a beat or a named take spanning beats.
3. Build and listen before scheduling actions.
4. Add anchors at meaningful spoken moments.
5. Join actions to those anchors with `after`.
6. Add an authored narration wait only when speech must wait for asynchronous
   work to complete.

The build reuses unchanged take audio. `step=narration` focuses the build on
narration and its dependent presentation output while you revise speech.

See [Narration, anchors, and waits](/reference/recording-files/schema/#synchronizing-narration-and-commands)
for the exact syntax and [Concepts](/concepts/#presentation-time-and-realtime)
for the timing model.
