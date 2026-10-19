#!/usr/bin/env python3
"""Measure native cleanup against the existing genuine Bash qualification rows."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests' / 'envoy-session-startup'))
from staging import build_peer, save, sha, stage, verified_rows  # noqa: E402


def run_faults(output):
    """Inject syscalls into disposable test builds, never the checkout."""
    source = ROOT / 'runtime/envoy/session/process_cleanup_linux.go'
    before = source.read_bytes()
    signal = 'syscall.Syscall6(424, uintptr(r.fd), uintptr(sig), 0, 0, 0, 0)'
    wait = 'syscall.Wait4(r.pid, &status, syscall.WNOHANG, nil)'
    effects = {
        'signal-late-adoption': 'if cleanupFaultSequence.Add(1)==1 { cleanupSignalBarrier() }',
        'signal-once-eintr': 'if cleanupFaultSequence.Add(1)==1 { return 0,0,syscall.EINTR }',
        'signal-always-eintr': 'cleanupFaultSequence.Add(1); return 0,0,syscall.EINTR',
        'signal-denied': 'cleanupFaultSequence.Add(1); return 0,0,syscall.EPERM',
        'signal-badfd': 'cleanupFaultSequence.Add(1); return 0,0,syscall.EBADF',
        'signal-noop': 'cleanupFaultSequence.Add(1); return 0,0,0',
        'wait-once-eintr': 'if cleanupFaultSequence.Add(1)==1 { return 0,syscall.EINTR }',
        'wait-always-eintr': 'cleanupFaultSequence.Add(1); return 0,syscall.EINTR',
        'wait-not-child': 'cleanupFaultSequence.Add(1); return 0,syscall.ECHILD',
        'wait-noop': 'cleanupFaultSequence.Add(1); return 0,nil',
    }
    results = []
    try:
        for mode, effect in effects.items():
            context = output / mode
            context.mkdir()
            text = before.decode()
            if text.count('"context"') != 1:
                raise ValueError('cleanup import site changed')
            text = text.replace('"context"', '"context"\n "sync/atomic"', 1)
            if mode.startswith('signal-'):
                original = signal
                replacement = signal.replace('syscall.Syscall6', 'cleanupFaultSignal')
                wrapper = ('func cleanupFaultSignal(trap,a1,a2,a3,a4,a5,a6 uintptr)'
                           '(uintptr,uintptr,syscall.Errno){\n' + effect +
                           '\n return syscall.Syscall6(trap,a1,a2,a3,a4,a5,a6)\n}\n')
            else:
                original = wait
                replacement = wait.replace('syscall.Wait4', 'cleanupFaultWait')
                wrapper = ('func cleanupFaultWait(pid int,status *syscall.WaitStatus,'
                           'options int,usage *syscall.Rusage)(int,error){\n' + effect +
                           '\n return syscall.Wait4(pid,status,options,usage)\n}\n')
            if text.count(original) != 1:
                raise ValueError('cleanup syscall site changed: ' + mode)
            text = text.replace(original, replacement)
            text += '\nvar cleanupFaultSequence atomic.Int32\n' + wrapper
            overlay_source = context / source.name
            overlay_source.write_text(text)
            overlay = context / 'overlay.json'
            save(overlay, {'Replace': {str(source): str(overlay_source)}})
            argv = ['go', '-C', str(ROOT / 'runtime/envoy'), 'test', '-race',
                    '-buildvcs=false', '-overlay', str(overlay), '-run',
                    '^TestProcessCleanupFaultOverlay$', '-count=1', '-timeout=20s',
                    '-v', './session']
            log = context / 'native.log'
            started = time.time()
            with log.open('w') as stream:
                result = subprocess.run(
                    argv, env={**os.environ, 'OMEGAFLOW_CLEANUP_FAULT': mode},
                    stdout=stream, stderr=subprocess.STDOUT, timeout=120)
            proved = ('CLEANUP fault PROVED' in log.read_text() and
                      '--- PASS: TestProcessCleanupFaultOverlay' in log.read_text())
            results.append({'mode': mode, 'argv': argv, 'returncode': result.returncode,
                            'proved': proved, 'started_unix': started,
                            'completed_unix': time.time(),
                            'source_sha256': sha(source),
                            'overlay_source_sha256': sha(overlay_source),
                            'overlay_sha256': sha(overlay),
                            'log_path': str(log), 'log_sha256': sha(log),
                            'scope': 'Explicit test-only syscall overlay; '
                                     'not a naturally unkillable kernel task.'})
            save(output / 'results.json', results)
            if result.returncode or not proved:
                raise ValueError('cleanup fault proof failed: ' + str(log))
    finally:
        if source.read_bytes() != before:
            raise ValueError('production source changed during fault proofs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path)
    parser.add_argument('--faults-only', action='store_true',
                        help='run the ten syscall-overlay cases without the Bash matrix')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.faults_only and args.qualification is None:
        parser.error('--qualification is required unless --faults-only is set')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    faults = output / 'faults'
    faults.mkdir()
    run_faults(faults)
    if args.faults_only:
        print('Cleanup fault evidence: ' + str(faults))
        return
    with build_peer(output, './session') as (binary, current_awsh):
        results = []
        for row, entry, actual in verified_rows(args.qualification, current_awsh):
            iid, context = stage(output, row['release'], entry, actual, binary)
            with (context / 'native.log').open('w') as log:
                result = subprocess.run(
                    ['docker', 'run', '--rm', '--network=none',
                     '-e', 'OMEGAFLOW_NATIVE_PROCESS_CLEANUP=1',
                     '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                     '-test.run=^TestNativeProcessCleanup$', '-test.v',
                     '-test.timeout=120s'],
                    stdout=log, stderr=subprocess.STDOUT, timeout=135)
            results.append({'release': row['release'],
                            'qualified_awsh_sha256': current_awsh,
                            'receipt_sha256': row['receipt_sha256'],
                            'qualified_base_image': actual,
                            'native_image': iid,
                            'test_binary_sha256': sha(binary),
                            'returncode': result.returncode,
                            'log_path': str(context / 'native.log'),
                            'log_sha256': sha(context / 'native.log'),
                            'scope': 'Real qualified Bash-authored cleanup; '
                                     'Awsh/Envoy parent roles are test fixtures.'})
            save(output / 'results.json', results)
            if result.returncode:
                raise ValueError('native cleanup failed: ' + str(context / 'native.log'))
        if len(results) != 3:
            raise ValueError('all three genuine amd64 qualification rows are required')
    print('Native Bash cleanup evidence: ' + str(output))


if __name__ == '__main__':
    main()
