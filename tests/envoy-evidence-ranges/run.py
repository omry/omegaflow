#!/usr/bin/env python3
"""Measure B6.4 exclusive ranges and typed outcomes on genuine Bash targets."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests' / 'envoy-session-startup'))
from staging import build_peer, save, sha, stage, verified_rows  # noqa: E402

CASES = ('pty-zero', 'pty-seven', 'pty-empty', 'split-real', 'suppress',
         'replace', 'finalize', 'cancel', 'finalize-after-return', 'cleanup-failure')


def measure(output, rows, binary, awsh, *, case=None, failure=None):
    results = []
    for row, entry, actual in rows:
        iid, context = stage(output, row['release'], entry, actual, binary)
        argv = ['docker', 'run', '--rm', '--network=none',
                '-e', 'OMEGAFLOW_NATIVE_EVIDENCE=1',
                '-e', 'OMEGAFLOW_EVIDENCE_CASE=' + (case or ''),
                '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                '-test.run=^TestNativeEvidenceRanges$', '-test.v', '-test.timeout=240s']
        log = context / 'native.log'
        started = time.time()
        with log.open('w') as stream:
            result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=260)
        text = log.read_text()
        if failure:
            proved = result.returncode != 0 and failure in text
        else:
            proved = (result.returncode == 0 and
                      text.count('EVIDENCE RANGE ') == (1 if case else len(CASES)) and
                      '--- PASS: TestNativeEvidenceRanges (' in text)
        results.append({'release': row['release'], 'selected_case': case,
                        'cases': 1 if case else len(CASES),
                        'expected_failure': failure, 'proved': proved,
                        'returncode': result.returncode, 'argv': argv,
                        'started_unix': started, 'completed_unix': time.time(),
                        'qualified_awsh_sha256': awsh,
                        'qualification_receipt_sha256': row['receipt_sha256'],
                        'qualified_base_image': actual, 'native_image': iid,
                        'test_binary_sha256': sha(binary),
                        'log_path': str(log), 'log_sha256': sha(log)})
        save(output / 'results.json', results)
        if not proved:
            raise ValueError('evidence proof failed: ' + str(log))


def counterproof(output, binary, rows, awsh, name, source, old, new, case, diagnostic):
    output.mkdir(parents=True)
    text = source.read_text()
    if text.count(old) < 1:
        raise ValueError('counterproof target changed: ' + str(source))
    replacement = output / source.name
    replacement.write_text(text.replace(old, new, 1))
    overlay = output / 'overlay.json'
    save(overlay, {'Replace': {str(source): str(replacement)}})
    argv = ['go', '-C', str(ROOT / 'runtime/envoy'), 'test', '-buildvcs=false',
            '-c', '-overlay', str(overlay), '-o', str(binary), './session']
    subprocess.run(argv, check=True, env={**os.environ, 'CGO_ENABLED': '0',
                                        'GOOS': 'linux', 'GOARCH': 'amd64'})
    save(output / 'build-request.json', {'name': name, 'argv': argv,
                                       'overlay_sha256': sha(overlay),
                                       'source_sha256': sha(source),
                                       'replacement_sha256': sha(replacement)})
    measure(output, rows[:1], binary, awsh, case=case, failure=diagnostic)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release', choices=('5.2.15', '5.2.37', '5.3.9'),
                        help='partial development matrix; not full qualification')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    completion = ROOT / 'runtime/envoy/session/completion_linux.go'
    dispatch = ROOT / 'runtime/envoy/session/dispatch_linux.go'
    before = [path.read_bytes() for path in (completion, dispatch)]
    try:
        with build_peer(output, './session') as (binary, awsh):
            rows = [row for row in verified_rows(args.qualification, awsh)
                    if not args.release or row[0]['release'] == args.release]
            if not rows:
                raise ValueError('no selected genuine target')
            measure(output, rows, binary, awsh)
            counterproof(output / 'counterproofs' / 'wrong-start', binary, rows, awsh,
                         'wrong-start', completion,
                         'result.OutputStart, result.OutputThrough = c.active.outputStart, closingOffset',
                         'result.OutputStart, result.OutputThrough = c.active.outputStart + 1, closingOffset',
                         'pty-seven', 'evidence range start mismatch')
            counterproof(output / 'counterproofs' / 'synthetic-status', binary, rows, awsh,
                         'synthetic-status', dispatch,
                         'd.result = protocol.OperationFinalized{OperationID: m.OperationID, Cwd: m.PhysicalCWD, Reason: reason, InspectionResults: d.inspectionResults()}',
                         'd.result = protocol.OperationCompleted{OperationID: m.OperationID, Status: m.Status, Cwd: m.PhysicalCWD, InspectionResults: d.inspectionResults()}',
                         'finalize', 'planned finalization or cancellation claimed natural exit')
            counterproof(output / 'counterproofs' / 'finalized-bytes', binary, rows, awsh,
                         'finalized-bytes',
                         ROOT / 'runtime/envoy/session/evidence_native_linux_test.go',
                         'raw := e.sink.bytes()',
                         'raw := e.sink.bytes(); if i := bytes.Index(raw, []byte("OPEN-é")); i >= 0 { raw[i] = \'X\' }',
                         'finalize', 'PTY evidence did not retain exact terminal bytes')
    finally:
        if [path.read_bytes() for path in (completion, dispatch)] != before:
            raise ValueError('counterproof changed production source')
    print('Native B6.4 evidence: ' + str(output))


if __name__ == '__main__':
    main()
