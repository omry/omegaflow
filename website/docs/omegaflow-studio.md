---
sidebar_position: 3
sidebar_label: Studio CLI
---

# OmegaFlow Studio

OmegaFlow Studio is the authoring tool and CLI for scripted terminal and video
flows.

The current package is `omegaflow-studio`, and it installs a `studio` command.
The CLI composes recording configuration with Hydra, runs scripted terminal
actions, stores per-run artifacts, retimes casts for human playback, manages
optional narration audio, and publishes website-ready outputs.

## Recording scripts

Recording scripts are Markdown files with `studio-directive` YAML blocks. The
Markdown keeps the human-readable walkthrough close to the machine-readable
instructions that build it.

A recording can define:

- capture settings such as terminal size and headless mode
- beats with captions, narration, commands, and guide text
- output paths for casts, audio, and metadata
- publish surfaces such as Docusaurus MDX or standalone HTML
- retiming rules for typing speed and pauses
- environment variables used while recording

## Build pipeline

```mermaid
%%{init: {"themeVariables": {"fontSize": "18px"}}}%%
flowchart TB
    Script["Recording script<br/>Markdown + directives"]

    Record["Record<br/>baseline cast + timeline"]
    AudioGenerate["Generate audio<br/>cached TTS fragments"]
    AudioPublish["Publish audio<br/>voiceover + timing metadata"]

    Retime["Retime<br/>terminal cast + audio timing"]
    Align["Check alignment<br/>before publishing"]
    Publish["Publish<br/>Docusaurus MDX or HTML"]

    Script --> Record
    Script -. optional narration .-> AudioGenerate
    AudioGenerate -. when audio is enabled .-> AudioPublish

    Record --> Retime
    AudioPublish -. timing metadata .-> Retime

    Retime --> Align
    Align --> Publish
    AudioPublish -. voiceover asset .-> Publish

    classDef main fill:#282a36,stroke:#8be9fd,stroke-width:2px,color:#f8f8f2
    classDef optional fill:#1f2335,stroke:#6272a4,stroke-width:2px,color:#f8f8f2
    class Script,Record,Retime,Align,Publish main
    class AudioGenerate,AudioPublish optional
```

Audio steps are skipped when `audio.enabled: false`. Build reuses fresh
artifacts when it can; use `action=check` separately to validate recording,
audio, retiming, and alignment freshness.

## Repository

The source lives at [github.com/omry/omegaflow](https://github.com/omry/omegaflow).
