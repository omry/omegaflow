#!/usr/bin/env python3
"""Prove the actual B7.2 worker and detect omitted isolation/result/reap checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / "runtime/envoy/inspection" / name for name in (
    "worker_linux.go", "worker_codec_linux.go",
    "worker_process_linux_test.go", "worker_codec_linux_test.go",
)]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError(f"non-unique worker proof site: {before!r}")
    return text.replace(before, after, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    original = {str(p): sha(p) for p in SOURCES}
    records = []
    environment = os.environ | {"CHGDISABLE": "1", "GOCACHE": "/tmp/omegaflow-go-cache", "GOMAXPROCS": "2"}

    def run(name, argv, *, expect_failure=None, timeout=120):
        log = output / (name + ".log")
        started = time.time()
        process = subprocess.Popen(argv, cwd=ROOT / "runtime/envoy", env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            data, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)  # Only this owned test process group.
            data, _ = process.communicate()
        log.write_bytes(data)
        passed = not timed_out and ((process.returncode != 0 and expect_failure.encode() in data)
                                   if expect_failure else process.returncode == 0)
        record = {"name": name, "argv": [str(x) for x in argv], "started_unix": started,
                  "completed_unix": time.time(), "returncode": process.returncode, "timed_out": timed_out,
                  "expected_failure": expect_failure, "passed": passed, "log": str(log), "log_sha256": sha(log)}
        records.append(record)
        (output / "progress.json").write_text(json.dumps(records, indent=2) + "\n")
        if not passed:
            raise RuntimeError(f"worker proof failed: {name}: {log}")

    def build(folder, overlay=None):
        folder.mkdir(parents=True, exist_ok=True)
        binary = folder / "inspection-worker.test"
        argv = ["go", "test", "-c", "-race", "-buildvcs=false", "-o", str(binary)]
        if overlay:
            argv += ["-overlay", str(overlay)]
        run("build-" + folder.name, argv + ["./inspection"])
        return binary

    baseline = build(output / "baseline")
    run("normal", [str(baseline), "-test.run=^TestWorker", "-test.timeout=60s"], timeout=70)
    fixtures = ["malformed", "trailing", "large", "mismatch", "nonzero", "late-exit", "block"]
    for fixture in fixtures:
        binary = baseline.parent / ("b7-worker-" + fixture)
        shutil.copy2(baseline, binary)
        assert sha(binary) == sha(baseline)
        run("fixture-" + fixture, [str(binary), "-test.run=^TestWorkerFixture$", "-test.timeout=20s"], timeout=25)

    worker, codec, process_tests = SOURCES[:3]
    controls = [
        ("test-parent-setup", process_tests, "if err := protectTestParentDescriptors(); err != nil {",
         "if err := error(nil); err != nil {", None, "^TestWorkerInheritedTestParentDescriptor$",
         "inherited test-parent descriptor prevented worker launch"),
        ("environment", worker, 'cmd.Dir, cmd.Env = "/", []string{}', 'cmd.Dir, cmd.Env = "/", nil',
         None, "^TestWorkerActualInspection$", "actual inspection:"),
        ("inheritance", worker, "if err = protectedWorkerDescriptors(); err == nil {",
         "if err = error(nil); err == nil {", None, "^TestWorkerSessionDescriptorIsolation$",
         "broken inheritance protection did not reject before fork"),
        ("correlation", codec, "result.InspectionID != plan.InspectionID", "false", "mismatch",
         "^TestWorkerFixture$", "invalid child result accepted by b7-worker-mismatch"),
        ("validation", codec, "result, err := decodeWorkerResult(entry)",
         "var result protocol.InspectionResult\n\t\t\terr := json.Unmarshal(entry, &result)", None, "^TestWorkerRejectsMalformedReplies$",
         'accepted malformed result "duplicate-field"'),
        ("reap", worker, "waitErr := cmd.Wait()", "waitErr := error(nil)\n\t\tgo cmd.Wait()", "late-exit",
         "^TestWorkerFixture$", "caller bound falsely proved reap"),
        ("stream-bound", worker, "io.LimitReader(readResult, protocol.MaxFrameBytes+1)", "readResult", "large",
         "^TestWorkerFixture$", "context deadline exceeded"),
    ]
    for name, source, before, after, fixture, test, diagnostic in controls:
        folder = output / ("control-" + name)
        folder.mkdir()
        modified = folder / source.name
        modified.write_text(replace_once(source.read_text(), before, after))
        overlay = folder / "overlay.json"
        overlay.write_text(json.dumps({"Replace": {str(source): str(modified)}}, indent=2) + "\n")
        binary = build(folder, overlay)
        assert sha(binary) != sha(baseline), "removal control built an unchanged baseline binary"
        if fixture:
            renamed = folder / ("b7-worker-" + fixture)
            shutil.copy2(binary, renamed)
            binary = renamed
        run("control-" + name, [str(binary), "-test.run=" + test, "-test.timeout=20s"],
            expect_failure=diagnostic, timeout=25)

    assert {str(p): sha(p) for p in SOURCES} == original, "worker source changed during proof"
    result = {"status": "passed", "source_unchanged": True, "sources": original,
              "actual_fault_fixtures": fixtures, "negative_controls": [v[0] for v in controls],
              "checks": records,
              "coverage_limit": "Proof binaries omit coverage instrumentation. Outer Go coverage measures parent execution; actual restricted child execution is established by process fixtures and detected controls.",
              "scope": "B7.2 isolated worker only; B7.3 owns lifecycle winner serialization and fatal cancellation timeout."}
    (output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": "passed", "fixtures": len(fixtures), "negative_controls": len(controls)}))


if __name__ == "__main__":
    main()
