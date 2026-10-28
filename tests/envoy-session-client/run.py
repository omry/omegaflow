#!/usr/bin/env python3
"""C3.3 actual-local-actor matrix using the existing qualified native staging."""

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/envoy-session-startup"))
from staging import artifact_tool, build_peer, save, sha, stage, stage_artifact, verified_rows


def source_binding():
    paths = [ROOT / "src/omegaflow" / name for name in (
        "envoy_controller.py", "envoy_client.py", "envoy_transport.py", "envoy_codecs.py",
        "controller_session.py", "controller_lifecycle.py", "reploy_codecs.py")]
    paths += [Path(__file__), Path(__file__).with_name("native.py"),
              ROOT / "runtime/envoy/cmd/omegaflow-envoy/main.go",
              ROOT / "tests/fixtures/reploy-codecs/client-v1-events.jsonl"]
    paths += [p for p in (ROOT / "runtime/envoy").rglob("*")
              if p.is_file() and p.suffix in {".go", ".s", ".mod", ".sum"}]
    paths += [ROOT / "runtime/envoy/etc" / name for name in ("awsh-bashrc", "inputrc")]
    return {str(p.relative_to(ROOT)): {"sha256": sha(p), "mode": p.stat().st_mode & 0o777}
            for p in sorted(set(paths))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, help="execute this complete qualified artifact unchanged")
    parser.add_argument("--release", choices=("5.2.15", "5.2.37", "5.3.9"),
                        help="development subset;cannot satisfy the complete C3.3 matrix")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = source_binding()
    artifact = args.runtime
    artifact_before = None
    if artifact is not None:
        tool = artifact_tool()
        manifest, payload = tool.read_artifact(artifact)
        tool.verify_qualified(artifact, manifest["source_revision"])
        artifact_before = manifest, payload
    peer = (nullcontext((artifact / "bin/envoy", sha(artifact / "bin/awsh")))
            if artifact is not None else build_peer(output, "./cmd/omegaflow-envoy", production=True))
    rows = []
    with peer as (binary, awsh):
        for row, entry, actual in verified_rows(args.qualification.resolve(), awsh):
            if args.release and row["release"] != args.release:
                continue
            if artifact is None:
                iid, context = stage(output, row["release"], entry, actual, binary)
            else:
                if entry != tool.bash_builds.read(artifact / "etc/bash-builds.json")["builds"].get(entry["candidate"]["executable_sha256"]):
                    raise ValueError("qualification matrix differs from packaged entry")
                iid, context = stage_artifact(output, row["release"], actual, artifact)
            evidence = context / "native-evidence"
            evidence.mkdir()
            command = ["docker", "run", "--rm", "--network=none", "--entrypoint=/usr/bin/python3",
                       "-e", "PYTHONPATH=/c3-src", "-e", "PYTHONDONTWRITEBYTECODE=1",
                       "-v", str(ROOT / "src") + ":/c3-src:ro",
                       "-v", str(ROOT / "tests") + ":/c3-tests:ro",
                       "-v", str(evidence) + ":/evidence:rw", iid,
                       "/c3-tests/envoy-session-client/native.py", "--envoy",
                       "/omegaflow-runtime/bin/envoy", "--output", "/evidence"]
            started = time.time()
            log = context / "native.log"
            with log.open("w") as stream:
                result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=480)
            results = [json.loads(p.read_text()) for p in sorted(evidence.glob("*/result.json"))]
            expected = {"ordinary-state", "source-rejection", "inspection", "gate-input", "cancel",
                        "finalize", "finalize-inspection", "split", "input", "reserved-input", "resize", "raw-barrier",
                        "inspection-crossed-cancel", "finalize-inspection-crossed-cancel",
                        "crossed-cancel", "crossed-finalize", "crossed-cancel-continued",
                        "crossed-finalize-continued", "crossed-cancel-interrupted",
                        "crossed-finalize-interrupted", "entry-idle-cancel", "entry-idle-finalize",
                        "entry-idle-discard", "completion-idle-discard", "entry-selected-eof", "entry-signal"}
            passed = (result.returncode == 0 and {v["scenario"] for v in results} == expected
                      and len(results) == len(expected) and all(v["passed"] and v["envoy_reaped"] for v in results))
            rows.append({"release": row["release"], "passed": passed, "returncode": result.returncode,
                         "argv": command, "started_unix": started, "completed_unix": time.time(),
                         "qualified_awsh_sha256": awsh, "base_image": actual, "test_image": iid,
                         "envoy_sha256": sha(binary), "entrypoint": "./cmd/omegaflow-envoy",
                         "log": str(log), "log_sha256": sha(log),
                         "results": results})
            save(output / "progress.json", rows)
            if not passed:
                raise RuntimeError("actual C3 Python client failed: " + str(log))
    assert source_binding() == before, "C3 source or mode changed during native matrix"
    if artifact is not None:
        assert tool.read_artifact(artifact) == artifact_before, "runtime artifact changed during native matrix"
    if not args.release:
        assert {row["release"] for row in rows} == {"5.2.15", "5.2.37", "5.3.9"}
    save(output / "results.json", {"status": "passed", "rows": rows, "sources": before,
         "source_unchanged": True, "scope": "development subset" if args.release else "complete C3.3 actual-local-actor matrix",
         "Reploy_invocation": "not claimed;C8.2 owns public invocation and termination"})
    print("Actual Python/Envoy/Awsh/Bash matrix passed: " + str(len(rows)) + " genuine amd64 targets")


if __name__ == "__main__":
    main()
