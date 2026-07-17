#!/usr/bin/env python3
"""Update generated recording schema snippets in the website docs."""

from __future__ import annotations

import argparse
import ast
import difflib
import sys
from pathlib import Path


SCHEMA_GROUPS = {
    "config": [
        "RecordingCaptureConfig",
        "RecordingStyleConfig",
        "RecordingOutputsConfig",
        "RecordingTimingConfig",
        "RecordingEnvironmentConfig",
        "RecordingAudioBillingConfig",
        "RecordingAudioTranscriptionConfig",
        "RecordingAudioConfig",
        "BrowserViewportConfig",
        "BrowserContextConfig",
        "BrowserAuthConfig",
        "BrowserTimeoutsConfig",
        "BrowserRedactionConfig",
        "BrowserRecordingConfig",
        "BrowserWindowPresentationConfig",
        "BrowserChromePresentationConfig",
        "BrowserTransitionsPresentationConfig",
        "BrowserPointerPresentationConfig",
        "BrowserTypingPresentationConfig",
        "BrowserPresentationConfig",
        "RecordingPresentationConfig",
        "RecordingFailureAnimationConfig",
        "RecordingFailureSummaryConfig",
        "RecordingRequirementsConfig",
        "RecordingDefaults",
        "RecordingSourceSpec",
    ],
    "beat": [
        "RecordingExpectationConfig",
        "RecordingCommandConfig",
        "RecordingStepConfig",
        "BrowserTargetConfig",
        "BrowserUrlMatcherConfig",
        "BrowserResponseMatcherConfig",
        "BrowserConditionConfig",
        "BrowserOpenPageConfig",
        "BrowserClickConfig",
        "BrowserViewportPointConfig",
        "BrowserMovePointerConfig",
        "BrowserSecretConfig",
        "BrowserFillConfig",
        "BrowserTypeKeysConfig",
        "BrowserPressConfig",
        "BrowserScrollOffsetConfig",
        "BrowserScrollConfig",
        "BrowserWaitForConfig",
        "BrowserActionConfig",
        "BrowserTextCheckConfig",
        "BrowserCountCheckConfig",
        "BrowserCheckConfig",
        "RecordingActionConfig",
        "RecordingCheckConfig",
        "RecordingGuideConfig",
        "RecordingBeatConfig",
    ],
    "publishing": [
        "RecordingPublishSurfaceConfig",
        "RecordingPublishConfig",
    ],
}

DOC_TARGETS = [
    {
        "group": "config",
        "path": ("website", "docs", "recording-files", "config.md"),
        "start": "<!-- recording-config-schema:start -->",
        "end": "<!-- recording-config-schema:end -->",
        "summary": "<summary>Config schema</summary>\n\n",
    },
    {
        "group": "beat",
        "path": ("website", "docs", "recording-files", "beat.md"),
        "start": "<!-- recording-beat-schema:start -->",
        "end": "<!-- recording-beat-schema:end -->",
        "summary": "<summary>Beat schema</summary>\n\n",
    },
    {
        "group": "publishing",
        "path": ("website", "docs", "recording-files", "publishing-runtime.md"),
        "start": "<!-- recording-publishing-schema:start -->",
        "end": "<!-- recording-publishing-schema:end -->",
        "summary": "<summary>Publishing schema</summary>\n\n",
    },
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def schema_source(source_path: Path, class_names: list[str]) -> str:
    source = source_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    module = ast.parse(source, filename=str(source_path))
    class_nodes = {
        node.name: node for node in module.body if isinstance(node, ast.ClassDef)
    }
    missing = [name for name in class_names if name not in class_nodes]
    if missing:
        raise RuntimeError("missing schema class(es): " + ", ".join(missing))

    chunks: list[str] = []
    for name in class_names:
        node = class_nodes[name]
        if node.end_lineno is None:
            raise RuntimeError(f"cannot locate end of schema class: {name}")
        start_lineno = node.lineno
        if node.decorator_list:
            start_lineno = min(decorator.lineno for decorator in node.decorator_list)
        chunks.append("\n".join(lines[start_lineno - 1 : node.end_lineno]))
    return "\n\n\n".join(chunks)


def generated_block(schema: str, *, start: str, end: str) -> str:
    return f"{start}\n\n```python\n{schema}\n```\n\n{end}"


def replace_schema_block(
    document: str,
    block: str,
    *,
    start_marker: str,
    end_marker: str,
    summary: str,
) -> str:
    if start_marker in document or end_marker in document:
        start = document.index(start_marker)
        end = document.index(end_marker, start) + len(end_marker)
        return document[:start] + block + document[end:]

    summary_start = document.index(summary) + len(summary)
    fence_start = document.index("```python\n", summary_start)
    fence_end = document.index("\n```", fence_start + len("```python\n")) + len("\n```")
    return document[:fence_start] + block + document[fence_end:]


def update_document(*, check: bool) -> int:
    root = repo_root()
    source_path = root / "src" / "omegaflow" / "studio_config.py"
    changed = False
    diffs: list[str] = []

    for target in DOC_TARGETS:
        docs_path = root.joinpath(*target["path"])
        current = docs_path.read_text(encoding="utf-8")
        schema = schema_source(source_path, SCHEMA_GROUPS[target["group"]])
        updated = replace_schema_block(
            current,
            generated_block(
                schema,
                start=target["start"],
                end=target["end"],
            ),
            start_marker=target["start"],
            end_marker=target["end"],
            summary=target["summary"],
        )

        if updated == current:
            continue
        changed = True
        if check:
            diffs.extend(
                difflib.unified_diff(
                    current.splitlines(),
                    updated.splitlines(),
                    fromfile=str(docs_path),
                    tofile=f"{docs_path} (generated)",
                    lineterm="",
                )
            )
        else:
            docs_path.write_text(updated, encoding="utf-8")
            print(f"Updated {docs_path.relative_to(root)}")

    if check and changed:
        print("\n".join(diffs), file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the generated schema docs are out of date",
    )
    args = parser.parse_args()
    return update_document(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
