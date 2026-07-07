---
sidebar_position: 5
sidebar_label: Publishing And Runtime
---

# Publishing And Runtime Output

Publishing config names the places a build can write. Runtime output is the
local state Studio creates while recording, retiming, generating audio, and
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
      component: OmegaFlowVideo
    html:
      type: standalone_html
      file: ${outputs.dir}/${id}.html
```

`docusaurus_mdx` replaces a placeholder block in an MDX file. `standalone_html`
writes a complete HTML page containing the cast player.

## Runtime Output

By default, Studio runtime files are generated under `recordings/.omegaflow/`.
Projects can change this with `studio.data_dir` in
[Studio Configuration](../studio-configuration.md).

```text
recordings/.omegaflow/
  runs/
  cache/
  videos/
```

Do not edit those files by hand. Commit the authored recording files and public
website assets; leave local runtime output ignored unless a publish surface
explicitly targets a tracked path.

## Schema

This schema block is generated from `src/omegaflow_studio/studio_config.py`
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
