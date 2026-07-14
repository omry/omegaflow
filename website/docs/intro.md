---
sidebar_position: 1
sidebar_label: Overview
---

# OmegaFlow

OmegaFlow turns scripted terminal workflows into website-ready videos.

The project is for demos, guides, and technical walkthroughs where the
recording should be tied to source control instead of trapped inside a one-off
screen capture. An OmegaFlow script describes the story, terminal
commands, visible captions, optional narration, expected outputs, and publish
targets. The `omegaflow` CLI uses [Hydra](https://hydra.cc/) for configuration and
builds the generated media from that source.

## Built for changing demos

Terminal demos age quickly. Commands change, setup steps move, and screenshots
stop matching the product. OmegaFlow treats a walkthrough as a compiled
artifact: keep the script in the repository, rebuild the video when the workflow
changes, and publish the resulting assets with the documentation.

## What it produces

- A seekable presentation manifest spanning terminal and browser beats.
- Beat-local terminal casts and deterministic browser payloads.
- Optional voiceover audio and timing metadata in the presentation bundle.
- Validated static website assets that can be embedded in Docusaurus.

The repository is on GitHub: [omry/omegaflow](https://github.com/omry/omegaflow).
