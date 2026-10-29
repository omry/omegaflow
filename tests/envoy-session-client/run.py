#!/usr/bin/env python3
"""C3.3 actual-local-actor matrix using the existing qualified native staging."""

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4


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


def native_command(image, evidence, *, mounts=(), envoy="/omegaflow-runtime/bin/envoy"):
    command = ["docker", "run", "--rm", "--network=none", "--entrypoint=/usr/bin/python3",
               "-e", "PYTHONPATH=/c3-src", "-e", "PYTHONDONTWRITEBYTECODE=1",
               "-v", str(ROOT / "src") + ":/c3-src:ro",
               "-v", str(ROOT / "tests") + ":/c3-tests:ro",
               "-v", str(evidence) + ":/evidence:rw"]
    for mount in mounts:
        command += ["-v", mount]
    return command + [image, "/c3-tests/envoy-session-client/native.py",
                      "--envoy", envoy, "--output", "/evidence"]


def corrupt_copy(source, destination, asset):
    """Modify one file in a disposable copy, retaining its expected mode."""
    shutil.copytree(source, destination)
    target = destination / asset
    mode = target.stat().st_mode & 0o777
    target.chmod(mode | 0o200)
    with target.open("ab") as stream:
        stream.write(b"\nC5.2 corrupt-copy negative control\n")
    target.chmod(mode)


def asset_controls(output, context, image, artifact):
    tool = artifact_tool()
    before = tool.read_artifact(artifact)
    assets = sorted([*before[1], "manifest.json"])
    evidence_root = context / "asset-controls"
    evidence_root.mkdir()
    rows = []
    for index, asset in enumerate(assets):
        evidence = evidence_root / f"{index:02d}"
        evidence.mkdir()
        # The actual installed stage is never writable or used as a fault target.
        with TemporaryDirectory(prefix=".asset-control-", dir=output) as temporary:
            mutated = Path(temporary) / "runtime"
            corrupt_copy(artifact, mutated, asset)
            mounts = [str(mutated) + ":/omegaflow-runtime:ro"]
            envoy = "/omegaflow-runtime/bin/envoy"
            if asset == "bin/envoy":
                # Execute the pristine production verifier against the corrupt
                # fixed-root fixture, rather than executing damaged code.
                envoy = "/c5-pristine-envoy"
                mounts.append(str(artifact / "bin/envoy") + ":" + envoy + ":ro")
            command = native_command(image, evidence, mounts=mounts, envoy=envoy)
            container = "omegaflow-c5-asset-" + uuid4().hex
            command[2:2] = ["--name", container]
            command += ["--expect-asset-rejection", asset]
            log = evidence / "native.log"
            returncode = None
            timed_out = False
            cleanup = None
            with log.open("w") as stream:
                try:
                    result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                            timeout=45)
                    returncode = result.returncode
                except subprocess.TimeoutExpired:
                    timed_out = True
                    try:
                        removed = subprocess.run(["docker", "rm", "-f", container],
                                                 capture_output=True, timeout=10)
                        cleanup_log = evidence / "cleanup.log"
                        cleanup_log.write_bytes(removed.stdout + removed.stderr)
                        cleanup = {"returncode": removed.returncode,
                                   "log": str(cleanup_log), "log_sha256": sha(cleanup_log)}
                    except (subprocess.TimeoutExpired, OSError) as error:
                        cleanup = {"error": type(error).__name__, "removed": False}
        path = evidence / "asset-rejection/result.json"
        proof = json.loads(path.read_text()) if not timed_out and path.exists() else None
        passed = (not timed_out and returncode == 0 and proof is not None and proof["passed"]
                  and proof["asset"] == asset and proof["envoy_reaped"]
                  and proof["executed_envoy_sha256"] == sha(artifact / "bin/envoy"))
        rows.append({"case_id": "C5.2-asset:" + asset, "asset": asset, "passed": passed,
                     "argv": command, "returncode": returncode,
                     "container": container, "timed_out": timed_out, "cleanup": cleanup,
                     "pristine_verifier_fixture": asset == "bin/envoy",
                     "log": str(log), "log_sha256": sha(log), "result": proof})
        save(evidence_root / "results.json", rows)
        if not passed:
            raise ValueError("native asset refusal failed: " + str(log))
        assert tool.read_artifact(artifact) == before, "installed stage changed in asset control"
    assert {r["asset"] for r in rows} == set(assets) and len(rows) == len(assets)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, help="execute this complete qualified artifact unchanged")
    parser.add_argument("--asset-controls", action="store_true",
                        help="also reject a disposable corrupt copy of every packaged asset")
    parser.add_argument("--release", choices=("5.2.15", "5.2.37", "5.3.9"),
                        help="development subset;cannot satisfy the complete C3.3 matrix")
    args = parser.parse_args()
    if args.asset_controls and args.runtime is None:
        parser.error("--asset-controls requires --runtime")
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
            command = native_command(iid, evidence)
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
            if args.asset_controls:
                rows[-1]["asset_controls"] = asset_controls(output, context, iid, artifact)
                save(output / "progress.json", rows)
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
