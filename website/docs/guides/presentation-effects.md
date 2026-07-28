---
sidebar_label: Presentation Effects
slug: /guides/presentation-effects/
---

import VideoPlayer from "@site/src/components/VideoPlayer";

# Highlight Terminal Output

Terminal highlight effects can call attention to exact text or to dynamic
output matched by a safe regular expression. Multiple targets may be active at
the same time, and a regular expression may span lines.

<VideoPlayer
  title="Highlight Terminal Output"
  manifest="/omegaflow-videos/reference/terminal-highlights/presentation/recording.presentation.json"
/>

Keep the match as narrow as the explanation allows. Exact text is clearest for
stable output; regular expressions are useful when values change between
captures. Use narration anchors to bound the effect instead of estimating
absolute times.

See [Synchronized effects](/reference/recording-files/schema/#highlight-pane-text-during-narration)
for target fields, occurrence selection, and safe-regex limitations.
