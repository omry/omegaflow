#!/usr/bin/env python3
"""Run B7.1 deterministic source-mutation proofs and missing-check controls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime/envoy/inspection/inspection_linux.go"
PATH_SOURCE = ROOT / "runtime/envoy/inspection/path_linux.go"


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError(f"fault site must be unique: {before!r}")
    return text.replace(before, after, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = SOURCE.read_text()
    path_source = PATH_SOURCE.read_text()
    snapshots = replace_once(
        source,
        "second, err = snapshot(selected.file, b, true)",
        "testSnapshotBoundary(p.ResolvedPath)\n"
        "second, err = snapshot(selected.file, b, true)",
    )
    components = replace_once(
        source, "err = selected.verify()",
        "testComponentBoundary(p.ResolvedPath)\nerr = selected.verify()",
    )
    reads = replace_once(
        source, "after, err := state(f)",
        "testReadBoundary(f)\nafter, err := state(f)",
    )
    readlink_error = replace_once(
        path_source, "if errno != 0 {", "errno = syscall.EIO\nif errno != 0 {",
    )
    variants = [
        ("snapshot", snapshots, path_source, False, 8),
        ("component", components, path_source, False, 1),
        ("read", reads, path_source, False, 1),
        ("snapshot", replace_once(snapshots,
                                  "!reflect.DeepEqual(first, second)",
                                  "false && !reflect.DeepEqual(first, second)"),
         path_source, True, 0),
        ("component", components,
         replace_once(path_source, "!reflect.DeepEqual(fresh.chain, s.chain)",
                      "false && !reflect.DeepEqual(fresh.chain, s.chain)"),
         True, 0),
        ("read", replace_once(source, "after != before", "false && after != before"),
         path_source, True, 0),
        ("framing", replace_once(source, 'h.Write([]byte("directory-v2"))',
                                 'h.Write([]byte("directory"))'),
         path_source, True, 0),
        ("readlink-error", source, readlink_error, False, 2),
        ("readlink-error", source,
         replace_once(readlink_error, 'return "", failure("inspection-read", errno)',
                      'return "", errno'), True, 0),
    ]
    results = []
    for index, (mode, body, paths, negative, count) in enumerate(variants):
        case = output / f"{index + 1:02}-{mode}"
        case.mkdir()
        fixture = case / "inspection_linux.go"
        fixture.write_text(body)
        path_fixture = case / "path_linux.go"
        path_fixture.write_text(paths)
        overlay = case / "overlay.json"
        overlay.write_text(json.dumps({"Replace": {
            str(SOURCE): str(fixture), str(PATH_SOURCE): str(path_fixture),
        }}, indent=2) + "\n")
        # The outer selection check also detects a changed file. Isolate the
        # exact read-level guarantee for this removal control instead of
        # falsely requiring every redundant check removal to break Run.
        test = "^TestInspectionBoundaryFaults$"
        if negative and mode == "read":
            test = "^TestStableFileReads/metadata$"
        elif mode == "framing":
            test = "^TestFrozenInspectionDigests/digest-directory-v2-"
        elif mode == "readlink-error":
            test = "^TestReadlinkFailureIsTyped$"
        argv = ["go", "test", "-race", "-buildvcs=false", f"-overlay={overlay}",
                "-count=1", "-timeout=60s", "-v", "./inspection",
                f"-run={test}"]
        started = time.time()
        run = subprocess.run(
            argv, cwd=ROOT / "runtime/envoy", capture_output=True, text=True,
            env={**os.environ, "OMEGAFLOW_INSPECTION_BOUNDARY": mode}, timeout=90,
        )
        log = run.stdout + run.stderr
        (case / "test.log").write_text(log)
        if negative:
            proved = run.returncode != 0 and "want inspection-unstable" in log
            if mode == "framing":
                proved = (run.returncode != 0 and "digest " in log and
                          " want " in log and
                          "--- FAIL: TestFrozenInspectionDigests/" in log)
            elif mode == "readlink-error":
                proved = (run.returncode != 0 and "want inspection-read" in log and
                          "--- FAIL: TestReadlinkFailureIsTyped/" in log)
        else:
            marker = "OBSERVED INSPECTION MUTATION "
            passed = "--- PASS: TestInspectionBoundaryFaults ("
            if mode == "readlink-error":
                marker = "OBSERVED READLINK FAILURE "
                passed = "--- PASS: TestReadlinkFailureIsTyped ("
            proved = (run.returncode == 0 and log.count(marker) == count and passed in log)
        results.append({
            "mode": mode, "missing_check_control": negative,
            "expected_mutations": count, "proved": proved,
            "argv": argv, "returncode": run.returncode,
            "started_unix": started, "completed_unix": time.time(),
            "log": str(case / "test.log"),
            "log_sha256": hashlib.sha256(log.encode()).hexdigest(),
            "overlay_sha256": hashlib.sha256(overlay.read_bytes()).hexdigest(),
        })
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        if not proved:
            raise ValueError(f"inspection proof failed: {case / 'test.log'}")
    print(json.dumps({"passed": True, "observed_mutations": 10,
                      "typed_readlink_failures": 2, "detected_missing_checks": 5,
                      "results": str(output / "results.json")}))


if __name__ == "__main__":
    main()
