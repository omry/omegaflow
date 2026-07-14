---
sidebar_position: 5
sidebar_label: Publishing And Runtime
---

# Publishing And Runtime Output

Publishing config names the places a build can write. Runtime output is the
local state OmegaFlow creates while recording, retiming, generating audio, and
publishing.

## Publish Surfaces

`publish.surfaces` can target docs pages, standalone HTML, or both:

```yaml
publish:
  default: docs
  build_surfaces:
  - docs
  - html
  surfaces:
    docs:
      type: docusaurus_mdx
      file: docs/hello.md
      placeholder: hello-video
      component: VideoPlayer
    html:
      type: standalone_html
      file: ${outputs.asset_dir}/index.html
```

`docusaurus_mdx` replaces a placeholder block in an MDX file. `standalone_html`
writes a complete HTML page containing the presentation player.

Terminal-only, browser, and mixed surfaces use the same component and HTML
custom element with a generated `manifest` attribute:

```mdx
<VideoPlayer
  title="Product walkthrough"
  manifest="/videos/product/presentation/recording.presentation.json"
/>
```

```html
<cast-player-embed
  title="Product walkthrough"
  player="/cast-player.html"
  manifest="/videos/product/presentation/recording.presentation.json">
</cast-player-embed>
```

The bundle and the surface are separate: the atomic bundle lives at
`${outputs.asset_dir}/presentation/`, while a standalone page can remain at
`${outputs.asset_dir}/index.html`.

## Runtime Output

By default, OmegaFlow runtime files are generated under `recordings/.omegaflow/`.
Projects can change this with `studio.data_dir` in
[Project Configuration](../configuration.md).

```text
recordings/.omegaflow/
  runs/
  cache/
  videos/
    hello/
```

Do not edit those files by hand. Commit the authored recording files and public
website assets; leave local runtime output ignored unless a publish surface
explicitly targets a tracked path.

Browser/mixed runs additionally preserve private `capture/`, `diagnostics/`,
and a run-local validated `presentation/` bundle. `action=clean` removes the
published presentation bundle but retains preserved runs, diagnostics, and
narration cache. A failed build never publishes an incomplete capture log.

## Schema

This schema block is generated from `src/omegaflow/studio_config.py`
during the website build.

<details>
<summary>Publishing schema</summary>

<!-- recording-publishing-schema:start -->

```python
@dataclass
class RecordingPublishSurfaceConfig:
    type: str = ""
    file: str = ""
    placeholder: str | None = None
    component: str | None = None


@dataclass
class RecordingPublishConfig:
    default: str | None = None
    on_build: bool = True
    build_surfaces: list[str] | None = None
    surfaces: dict[str, RecordingPublishSurfaceConfig] = field(default_factory=dict)
```

<!-- recording-publishing-schema:end -->

</details>
