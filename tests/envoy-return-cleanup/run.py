#!/usr/bin/env python3
"""Prove actual ordinary-return cleanup on existing genuine Bash targets."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests' / 'envoy-session-startup'))
from staging import build_peer, save, sha, stage, verified_rows  # noqa: E402

FAULTS = ('helper-identity', 'removal', 'late-census', 'blocked-drain')


def matrix(output, rows, binary, current_awsh, fault=''):
    results = []
    for row, entry, actual in rows:
        iid, context = stage(output, row['release'], entry, actual, binary)
        argv = ['docker', 'run', '--rm', '--network=none',
                '-e', 'OMEGAFLOW_NATIVE_RETURN_CLEANUP=1',
                '-e', 'OMEGAFLOW_RETURN_FAULT=' + fault,
                '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                '-test.run=^TestNativeReturnCleanup$', '-test.v', '-test.timeout=180s']
        started = time.time()
        log = context / 'native.log'
        with log.open('w') as stream:
            result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=200)
        text = log.read_text()
        count = 1 if fault else 11
        proved = (text.count('ACTUAL RETURN ') == count and
                  '--- PASS: TestNativeReturnCleanup (' in text)
        results.append({'release': row['release'], 'fault_overlay': fault or None,
                        'qualified_awsh_sha256': current_awsh,
                        'receipt_sha256': row['receipt_sha256'],
                        'qualified_base_image': actual, 'native_image': iid,
                        'test_binary_sha256': sha(binary), 'argv': argv,
                        'started_unix': started, 'completed_unix': time.time(),
                        'returncode': result.returncode, 'proved': proved,
                        'cases': count, 'log_path': str(log), 'log_sha256': sha(log),
                        'scope': 'Actual Envoy session/relay/coordinator, Awsh and Bash. '
                                 'Fault runs use explicitly retained test-only Go overlays.'})
        save(output / 'results.json', results)
        if result.returncode or not proved:
            raise ValueError('actual ordinary cleanup failed: ' + str(log))


def overlay(output, source, relay):
    completion = source.decode()
    sites = {
        'proposal.CompletionHelperPID); err != nil {':
            'returnFaultHelper(proposal.CompletionHelperPID)); err != nil {',
        'return c.finishSplit(job)':
            'if err := returnFaultStep(job,c,"before-split"); err != nil { return err }; '
            'return c.finishSplit(job)',
        'if err := census.ProveIdle(job); err != nil {':
            'if err := returnFaultStep(job,c,"before-idle"); err != nil { return err }; '
            'if err := census.ProveIdle(job); err != nil {',
    }
    for original, replacement in sites.items():
        if completion.count(original) != 1:
            raise ValueError('ordinary return overlay site changed: ' + original)
        completion = completion.replace(original, replacement, 1)
    drain = relay.decode()
    original = 'raw, e := r.w.PTY.SyscallConn()'
    if drain.count(original) != 1:
        raise ValueError('final drain overlay site changed')
    drain = drain.replace(original, 'if err := returnFaultDrain(ctx); err != nil { return r.w.OutputThrough,err }; ' + original, 1)
    replacements = {}
    for name, content in (('completion_linux.go', completion), ('relay_linux.go', drain)):
        target = output / name
        target.write_text(content)
        replacements[str(ROOT / 'runtime/envoy/session' / name)] = str(target)
    path = output / 'overlay.json'
    save(path, {'Replace': replacements})
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release', choices=('5.2.15', '5.2.37', '5.3.9'))
    parser.add_argument('--faults-only', action='store_true')
    parser.add_argument('--fault', choices=FAULTS, help='focused overlay proof only')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = [ROOT / 'runtime/envoy/session' / name
               for name in ('completion_linux.go', 'relay_linux.go')]
    before = [path.read_bytes() for path in sources]
    try:
        with build_peer(output, './session') as (binary, current_awsh):
            rows = [row for row in verified_rows(args.qualification, current_awsh)
                    if not args.release or row[0]['release'] == args.release]
            if not rows:
                raise ValueError('no selected genuine target')
            if not args.faults_only and not args.fault:
                matrix(output, rows, binary, current_awsh)
            # Failure wiring is measured on the first selected genuine target.
            # The ordinary matrix above covers every selected qualified build.
            for mode in ((args.fault,) if args.fault else FAULTS):
                context = output / 'faults' / mode
                context.mkdir(parents=True)
                path = overlay(context, *before)
                argv = ['go', '-C', str(ROOT / 'runtime/envoy'), 'test', '-buildvcs=false',
                        '-c', '-overlay', str(path), '-o', str(binary), './session']
                subprocess.run(argv, check=True, env={**os.environ, 'CGO_ENABLED': '0',
                                                    'GOOS': 'linux', 'GOARCH': 'amd64'})
                save(context / 'build-request.json', {'argv': argv, 'overlay_sha256': sha(path)})
                matrix(context, rows[:1], binary, current_awsh, mode)
    finally:
        if any(path.read_bytes() != data for path, data in zip(sources, before)):
            raise ValueError('production source changed during disposable overlay proofs')
    print('Actual ordinary-cleanup evidence: ' + str(output))


if __name__ == '__main__':
    main()
