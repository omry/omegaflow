---
sidebar_label: Publishing
slug: /guides/publishing/
---

# Publish And Embed a Player

Configure a publish surface in recording defaults or one recording, then run
the normal build. Publishing is part of the build rather than a separate
capture workflow.

For Docusaurus, publish the validated bundle beneath `website/static/` and
embed the manifest with `VideoPlayer`. For a portable local result, publish
standalone HTML.

Keep the player responsive. Give it enough vertical space for narration,
content, and controls, and test the compact layout on a narrow mobile viewport.
Do not copy runtime runs or private capture state into a public directory.

The [output reference](/reference/output/) defines publish surfaces and bundle
contents. The [presentation contract](/reference/output/presentation/) covers
the player manifest and media types.
