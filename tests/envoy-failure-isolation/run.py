#!/usr/bin/env python3
"""Exercise B8.1 with actual Envoy owners, Awsh and qualified Bash targets."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/envoy-session-startup'))
from staging import build_peer, save, sha, stage, verified_rows

SOURCES = [ROOT / 'runtime/envoy/session' / name for name in (
    'failure_linux.go', 'start_linux.go', 'failure_linux_test.go',
    'failure_native_linux_test.go', 'transport_linux.go', 'private_linux.go', 'startup_linux.go')]


def prove_controls(root, qualification):
    root.mkdir()
    startup = ROOT / 'runtime/envoy/session/startup_linux.go'
    failure = ROOT / 'runtime/envoy/session/failure_linux.go'
    start = ROOT / 'runtime/envoy/session/start_linux.go'
    socket_leak = '''raw, rawErr := s.telemetry.SyscallConn()
    if rawErr != nil { err = rawErr; return }
    var leak *os.File
    if err = raw.Control(func(fd uintptr) {
        copyFD, _, _ := syscall.Syscall(syscall.SYS_FCNTL, fd, syscall.F_DUPFD_CLOEXEC, 0)
        leak = os.NewFile(copyFD, "inherited-test-socket")
    }); err != nil { return }
    defer leak.Close()
    w.command.ExtraFiles = []*os.File{readControl, writeResult, slave, leak}'''
    controls = [
        ('socket-handoff', startup,
         'w.command.ExtraFiles = []*os.File{readControl, writeResult, slave}',
         socket_leak, 'isolation', 'Awsh inherited Envoy socket', 1),
        ('descendant-cleanup', failure, 'census.Cleanup(ctx, 0)', 'error(nil)',
         'public-malformed', 'failed actors not fully disposed', 2),
        ('original-cause', start, 'cause = errors.Join(cause, c.w.channels.Err())',
         '// omitted original transport cause', 'public-partial-eof',
         'partial frame lost stable EOF class', 1),
        ('split-resources', failure, 'c.releaseResources(ctx)', 'error(nil)',
         'split-public-malformed', 'failed split resources remain owned', 1),
    ]
    rows = []
    for name, source, old, new, case, diagnostic, count in controls:
        folder = root / name
        folder.mkdir()
        original = source.read_text()
        if original.count(old) != count:
            raise ValueError('nonunique omission-control site: ' + name)
        modified = folder / source.name
        modified.write_text(original.replace(old, new))
        overlay = folder / 'overlay.json'
        save(overlay, {'Replace': {str(source): str(modified)}})
        before = sha(source)
        argv = [sys.executable, str(Path(__file__).resolve()), '--qualification',
                str(qualification), '--native-only', '--release', '5.2.15',
                '--case', case, '--output', str(folder / 'native')]
        started = time.time()
        with (folder / 'command.log').open('w') as log:
            result = subprocess.run(argv, env=os.environ | {
                'CHGDISABLE': '1', 'GOFLAGS': '-overlay=' + str(overlay)},
                stdout=log, stderr=subprocess.STDOUT, timeout=180)
        native = folder / 'native/5.2.15/native.log'
        passed = result.returncode != 0 and native.exists() and diagnostic in native.read_text()
        if sha(source) != before:
            raise RuntimeError('source changed during omission control: ' + name)
        rows.append({'name': name, 'passed': passed, 'returncode': result.returncode,
                     'expected_diagnostic': diagnostic, 'argv': argv,
                     'started_unix': started, 'completed_unix': time.time(),
                     'source': str(source), 'source_sha256': before,
                     'overlay_sha256': sha(overlay), 'modified_sha256': sha(modified),
                     'native_log': str(native),
                     'native_log_sha256': sha(native) if native.exists() else None})
        save(root / 'progress.json', rows)
        if not passed:
            raise RuntimeError('omission control not detected: ' + str(folder / 'command.log'))
    save(root / 'results.json', {'status': 'passed', 'controls': rows})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--release', choices=('5.2.15', '5.2.37', '5.3.9'))
    parser.add_argument('--case', help='development subset: one native scenario')
    parser.add_argument('--native-only', action='store_true', help='development subset: omit controls')
    parser.add_argument('--proof-only', action='store_true', help='development subset: omit positive matrix')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if args.native_only and args.proof_only:
        parser.error('choose one development subset')
    if args.proof_only and (args.case or args.release):
        parser.error('--proof-only requires the complete omission-control set')
    before = {str(p): sha(p) for p in SOURCES}
    controls = [] if args.native_only or args.case or args.release else prove_controls(output / 'controls', args.qualification.resolve())
    if args.proof_only:
        if before != {str(p): sha(p) for p in SOURCES}:
            raise RuntimeError('source changed during proof')
        save(output / 'results.json', {'status': 'passed', 'scope': 'omission controls only', 'controls': controls})
        return
    rows = []
    with build_peer(output, './session') as (binary, awsh):
        for row, entry, actual in verified_rows(args.qualification.resolve(), awsh):
            if args.release and row['release'] != args.release:
                continue
            iid, context = stage(output, row['release'], entry, actual, binary)
            argv = ['docker', 'run', '--rm', '--network=none', '-e',
                    'OMEGAFLOW_NATIVE_FAILURE=1']
            if args.case:
                argv += ['-e', 'OMEGAFLOW_FAILURE_CASE=' + args.case]
            argv += ['--entrypoint=/omegaflow-runtime/bin/envoy',
                     iid, '-test.run=^TestNativeFailureIsolation$', '-test.v', '-test.failfast', '-test.timeout=230s']
            started = time.time()
            log = context / 'native.log'
            with log.open('w') as stream:
                result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=250)
            passed = result.returncode == 0 and log.read_text().count('NATIVE FAILURE ') == (1 if args.case else 11)
            rows.append({'release': row['release'], 'passed': passed, 'returncode': result.returncode,
                         'argv': argv, 'started_unix': started, 'completed_unix': time.time(),
                         'qualified_awsh_sha256': awsh, 'qualified_base_image': actual,
                         'test_image': iid, 'test_binary_sha256': sha(binary),
                         'log': str(log), 'log_sha256': sha(log)})
            save(output / 'progress.json', rows)
            if not passed:
                raise RuntimeError('actual failure/isolation case failed: ' + str(log))
    if before != {str(p): sha(p) for p in SOURCES}:
        raise RuntimeError('source changed during proof')
    save(output / 'results.json', {'status': 'passed', 'rows': rows, 'sources': before,
                                 'source_unchanged': True, 'controls': controls,
                                 'scope': 'B8.1 isolation/channel failure; B8.2/B8.3 crossings and B8.4 full gate remain separate.'})
    print('Actual failure/isolation passed: ' + str(len(rows)) + ' genuine amd64 targets')


if __name__ == '__main__':
    main()
