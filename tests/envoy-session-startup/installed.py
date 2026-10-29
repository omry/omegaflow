#!/usr/bin/env python3
"""Run existing native acceptance against the actual installed C5.1 stage.

Invoke with an isolated artifact-bearing installation's Python and -I. The
separate --python interpreter runs the existing repository-owned native tools.
"""
import argparse
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

from omegaflow import bash_builds, runtime_artifact, runtime_staging


ROOT = Path(__file__).resolve().parents[2]
RELEASES = {"5.2.15", "5.2.37", "5.3.9"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root):
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError("nonregular installed-stage evidence: " + str(path))
        result[str(path.relative_to(root))] = {
            "mode": stat.S_IMODE(info.st_mode), "device": info.st_dev,
            "inode": info.st_ino, "size": info.st_size,
            "sha256": sha(path) if stat.S_ISREG(info.st_mode) else None}
    return result


def installed_modules():
    result = {}
    for module in (bash_builds, runtime_artifact, runtime_staging):
        path = Path(module.__file__).resolve(strict=True)
        expected = ROOT / "src/omegaflow" / path.name
        if not path.is_relative_to(Path(sys.prefix).resolve()) or sha(path) != sha(expected):
            raise ValueError("native stage must use the exact installed module: " + str(path))
        result[module.__name__] = {"path": str(path), "sha256": sha(path)}
    return result


def commands(python, acquisition, qualification, output, stage):
    return [
        [python, str(ROOT / "tests/envoy-bash-qualification/run.py"),
         "--acquisition", str(acquisition), "--output", str(output / "qualification"),
         "--runtime", str(stage)],
        [python, str(ROOT / "tests/envoy-session-client/run.py"),
         "--qualification", str(qualification), "--output", str(output / "client"),
         "--runtime", str(stage), "--asset-controls"]]


def verify_results(output, qualification, assets):
    rows = json.loads((output / "qualification/results.json").read_text())
    assert len(rows) == 3 and {r["release"] for r in rows} == RELEASES
    for row in rows:
        assert row["returncode"] == 0 and row["qualified"]
        current = json.loads((output / "qualification" / row["release"] / "receipt.json").read_text())
        original = json.loads((qualification / row["release"] / "receipt.json").read_text())
        assert current["repetitions"] == 2
        assert len(current["result"]["cases"]) == 258
        assert set(current["result"]["cases"]) == set(original["result"]["cases"])
    clients = json.loads((output / "client/results.json").read_text())
    assert clients["status"] == "passed" and clients["source_unchanged"]
    assert len(clients["rows"]) == 3 and {r["release"] for r in clients["rows"]} == RELEASES
    for row in clients["rows"]:
        assert row["passed"] and len(row["results"]) == 26
        assert all(r["passed"] and r["envoy_reaped"] for r in row["results"])
        controls = row["asset_controls"]
        assert len(controls) == len(assets) and {r["asset"] for r in controls} == assets
        assert all(r["passed"] for r in controls)
    return {"qualification_case_ids": 258, "qualification_observations": 1548,
            "actual_client_observations": 78, "asset_controls": len(assets) * 3}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True, help="repository tool interpreter")
    parser.add_argument("--acquisition", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True,
                        help="retained admitted profiles matching the installed table")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    modules = installed_modules()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = Path(files("omegaflow") / "runtime/linux-amd64")
    assert source.resolve().is_relative_to(Path(sys.prefix).resolve())
    before = snapshot(source)
    report = {"status": "running", "started_at": time.time(), "prefix": sys.prefix,
              "cwd": os.getcwd(), "modules": modules, "source": str(source),
              "source_before": before, "commands": []}
    parent = None
    passed = False
    try:
        with runtime_staging.stage_runtime() as stage:
            assert not stage.is_relative_to(ROOT)
            parent = stage.parent
            assert stat.S_IMODE(parent.stat().st_mode) == 0o700
            report["private_parent_0700"] = True
            report["stage"] = str(stage)
            report["stage_before"] = snapshot(stage)
            manifest = json.loads((stage / "manifest.json").read_text())
            assets = {entry["path"] for entry in manifest["files"]} | {"manifest.json"}
            assert len(assets) == 19
            try:
                for index, command in enumerate(commands(str(args.python.absolute()),
                        args.acquisition.resolve(), args.qualification.resolve(), output, stage)):
                    log = output / f"native-{index}.log"
                    started = time.time()
                    with log.open("x") as stream:
                        result = subprocess.run(command, cwd=ROOT, stdout=stream,
                                                stderr=subprocess.STDOUT, umask=0o022)
                    report["commands"].append({"argv": command, "returncode": result.returncode,
                        "elapsed_seconds": time.time() - started, "log": str(log), "sha256": sha(log)})
                    (output / "installed-stage.json").write_text(json.dumps(report, indent=2) + "\n")
                    if result.returncode:
                        raise RuntimeError("native stage acceptance failed: " + str(log))
                report["coverage"] = verify_results(output, args.qualification.resolve(), assets)
            finally:
                report["stage_after"] = snapshot(stage)
                assert report["stage_after"] == report["stage_before"]
        passed = True
    finally:
        report["source_after"] = snapshot(source)
        report["source_unchanged"] = report["source_after"] == before
        report["allocation_removed"] = parent is not None and not parent.exists()
        report["finished_at"] = time.time()
        report["status"] = ("passed" if passed and report["source_unchanged"]
                            and report["allocation_removed"] else "failed")
        (output / "installed-stage.json").write_text(json.dumps(report, indent=2) + "\n")
    assert report["status"] == "passed", report
    print("Installed-stage native acceptance passed: " + str(output))


if __name__ == "__main__":
    main()
