#!/usr/bin/env python3
"""Measure B7.3 with actual qualified Bash, Awsh, census and inspection worker."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/envoy-session-startup'))
from staging import build_peer, save, sha, stage, verified_rows

SOURCES = [ROOT / "runtime/envoy/session" / name for name in (
    "inspection_linux.go", "completion_linux.go", "dispatch_linux.go",
    "start_linux.go", "inspection_linux_test.go", "inspection_native_linux_test.go")]


def prove_controls(output):
    output.mkdir()
    inspection, completion = SOURCES[:2]
    controls = [
        ("early-commit", inspection, "return d.selectWrite(c)", "return nil",
         "^TestInspectionJoinedResultCommitsWhileTelemetryBlocked$", "wrong blocked-publication outcome"),
        ("cancel-discard", inspection, 'if d.life.kind != "cancel" {', "if true {",
         "^TestInspectionCancelAcceptedFirstDiscardsResultAndJoinsReap$", "unaccepted result or duplicate termination"),
        ("joined-reap", inspection, "c.work(ctx, false, worker.Reap)",
         "c.work(ctx, false, func(context.Context) error { return nil })",
         "^TestInspectionCancelAcceptedFirstDiscardsResultAndJoinsReap$", "inspection reap lacks joined completion"),
        ("cancel-bound", ROOT / "runtime/envoy/session/start_linux.go", "case <-c.inspectionDeadline():",
         "case <-chanStructNever():", "^TestInspectionBlockedReapIsFatalWithinOriginalFiveSeconds$", "unbounded worker disposal"),
        ("cancel-retirement", inspection, "if d.inspection != nil && d.inspection.worker != nil && d.inspection.stop != nil {",
         "if d.inspection != nil && d.inspection.stop != nil {",
         "^TestInspectionReapRetiresCancelDeadlineDuringBlockedResize$",
         "inspection cancellation deadline survived successful worker reap"),
        ("cleanup-retirement", completion, "d.cleanup, d.endCleanup = nil, nil", "// omitted cleanup retirement",
         "^TestInspectionRunsBeyondRetiredCleanupEpoch$", "inspection inherited retired cleanup deadline"),
        ("typed-failure", inspection, "if d.inspection != nil && d.inspection.failure != nil {", "if false {",
         "^TestInspectionWorkerErrorsAndExceptionalDisposal/typed$", "typed failure exposed partial or killed session"),
        ("plan-correlation", inspection, "if len(request) != len(resolved) {", "if false {",
         "^TestInspectionActualWorkerCompletionFailureAndPlanCorrelation/wrong-count$", "mismatched plan accepted"),
    ]
    records = []
    for name, source, old, new, test, diagnostic in controls:
        if source.read_text().count(old) != 1:
            raise ValueError("nonunique proof site: " + name)
        folder = output / name
        folder.mkdir()
        text = source.read_text().replace(old, new, 1)
        if name == "cancel-bound":
            text += "\nfunc chanStructNever() <-chan struct{} { return nil }\n"
        modified = folder / source.name
        modified.write_text(text)
        overlay = folder / "overlay.json"
        save(overlay, {"Replace": {str(source): str(modified)}})
        argv = ["go", "-C", str(ROOT / "runtime/envoy"), "test", "-race",
                "-buildvcs=false", "-count=1", "-timeout=30s", "-overlay", str(overlay),
                "-run", test, "./session"]
        started = time.time()
        log = folder / "proof.log"
        with log.open("w") as stream:
            result = subprocess.run(argv, env=os.environ | {"CHGDISABLE": "1",
                "GOCACHE": "/tmp/omegaflow-go-cache"}, stdout=stream, stderr=subprocess.STDOUT, timeout=90)
        passed = result.returncode != 0 and diagnostic in log.read_text()
        records.append({"name": name, "passed": passed, "argv": argv,
            "returncode": result.returncode, "expected_diagnostic": diagnostic,
            "started_unix": started, "completed_unix": time.time(), "log": str(log),
            "log_sha256": sha(log), "source_sha256": sha(source),
            "overlay_sha256": sha(overlay), "changed_source_sha256": sha(modified)})
        save(output / "progress.json", records)
        if not passed:
            raise RuntimeError("omission control did not detect missing behavior: " + str(log))
    save(output / "results.json", {"status": "passed", "controls": records})
    return records



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path)
    parser.add_argument('--proof-only', action='store_true', help='development subset: omit native matrix')
    parser.add_argument('--native-only', action='store_true', help='development subset: omit omission controls')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release', choices=('5.2.15', '5.2.37', '5.3.9'))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if args.proof_only and args.native_only:
        parser.error("choose one development subset")
    before = {str(p): sha(p) for p in SOURCES}
    controls = [] if args.native_only else prove_controls(output / "controls")
    if args.proof_only:
        assert before == {str(p): sha(p) for p in SOURCES}, 'source changed during proof'
        save(output / "results.json", {"status": "passed", "scope": "omission controls only", "controls": controls})
        return
    if args.qualification is None:
        parser.error("--qualification is required for actual target validation")
    rows = []
    with build_peer(output, './session') as (binary, awsh):
        for row, entry, actual in verified_rows(args.qualification, awsh):
            if args.release and args.release != row['release']:
                continue
            iid, context = stage(output, row['release'], entry, actual, binary)
            argv = ['docker', 'run', '--rm', '--network=none', '-e',
                    'OMEGAFLOW_NATIVE_INSPECTION=1', '--entrypoint=/omegaflow-runtime/bin/envoy',
                    iid, '-test.run=^TestNativeInspectionLifecycle$', '-test.v', '-test.timeout=150s']
            started = time.time()
            log = context / 'native.log'
            with log.open('w') as stream:
                result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=170)
            passed = result.returncode == 0 and log.read_text().count('NATIVE INSPECTION ') == 6
            rows.append({'release': row['release'], 'passed': passed, 'returncode': result.returncode,
                         'argv': argv, 'started_unix': started, 'completed_unix': time.time(),
                         'qualified_awsh_sha256': awsh, 'base_image': actual, 'test_image': iid,
                         'test_binary_sha256': sha(binary), 'log': str(log), 'log_sha256': sha(log)})
            save(output / 'progress.json', rows)
            if not passed:
                raise RuntimeError('actual inspection lifecycle failed: ' + str(log))
    assert before == {str(p): sha(p) for p in SOURCES}, 'source changed during proof'
    save(output / 'results.json', {'status': 'passed', 'rows': rows, 'controls': controls,
         'sources': before, 'source_unchanged': True,
         'scope': 'B7.3 actual local assembly;B8.4 full actual-actor gate remains separate.'})
    print('Actual inspection lifecycle passed: ' + str(len(rows)) + ' genuine amd64 targets')


if __name__ == '__main__':
    main()
