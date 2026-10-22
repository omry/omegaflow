#!/usr/bin/env python3
"""Exercise B8.2 with actual Envoy owners, Awsh and qualified Bash targets."""
import argparse
from contextlib import contextmanager
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/envoy-session-startup'))
from staging import build_peer, save, sha, stage, verified_rows

SOURCES = [ROOT / 'runtime/envoy/session' / name for name in (
    'idle_terminal_linux.go', 'idle_terminal_linux_test.go',
    'idle_terminal_native_linux_test.go', 'startup_linux.go', 'completion_linux.go',
    'start_linux.go', 'start_linux_test.go', 'failure_linux.go', 'process_cleanup_linux.go', 'private_linux.go')]
SOURCES += [ROOT / 'tests/envoy-terminal-closure' / name
            for name in ('run.py', 'README.md', 'case-map.json')]
CASES = ('shutdown', 'idle-exit', 'shutdown-cross-exit', 'active-zero',
         'active-nonzero', 'active-signal', 'active-descendant',
         'fault-nonzero-awsh', 'fault-signalled-awsh', 'fault-adopted-live',
         'fault-duplicate', 'fault-idle-result', 'output-order')
CASES += tuple('matrix-' + form + '-' + variant
               for form in ('closed', 'shell-exit')
               for variant in ('premature-eof', 'trailing-frame', 'reset', 'signal',
                               'nonzero-reap', 'missing-eof', 'missing-reap'))



@contextmanager
def observe_wait_publication(output):
    """Hold only publication after actual Wait; retain exact test-only overlay."""
    flags = shlex.split(os.environ.get('GOFLAGS', ''))
    overlays = [f.split('=', 1)[1] for f in flags if f.startswith('-overlay=')]
    if len(overlays) > 1:
        raise ValueError('ambiguous native overlay input')
    replacements = {}
    if overlays:
        prior = json.loads(Path(overlays[0]).read_text())
        if set(prior) != {'Replace'}:
            raise ValueError('unsupported native overlay shape')
        replacements.update(prior['Replace'])
    source = ROOT / 'runtime/envoy/session/startup_linux.go'
    if str(source) in replacements:
        raise ValueError('terminal Wait observation conflicts with existing overlay')
    text = source.read_text()
    source_before = sha(source)
    old = 'w.waitErr = w.command.Wait(); close(w.reaped)'
    if text.count(old) != 1:
        raise ValueError('actual Wait observation site changed')
    folder = output / 'wait-publication-observation'
    folder.mkdir()
    observed = folder / source.name
    observed.write_text(text.replace(old,
        'w.waitErr = w.command.Wait(); nativeTerminalWaitPublication(); close(w.reaped)'))
    replacements[str(source)] = str(observed)
    overlay = folder / 'overlay.json'
    save(overlay, {'Replace': replacements})
    save(folder / 'binding.json', {'source': str(source), 'source_sha256': sha(source),
        'observed_source_sha256': sha(observed), 'overlay_sha256': sha(overlay),
        'role': 'Test-only delay after actual command.Wait, before existing reap publication. No fake Wait, status or actor.'})
    previous = os.environ.get('GOFLAGS')
    os.environ['GOFLAGS'] = shlex.join([f for f in flags if not f.startswith('-overlay=')] +
                                      ['-overlay=' + str(overlay)])
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('GOFLAGS', None)
        else:
            os.environ['GOFLAGS'] = previous
        if sha(source) != source_before:
            raise RuntimeError('source changed during native Wait observation')


def prove_controls(root, qualification):
    root.mkdir()
    terminal = ROOT / 'runtime/envoy/session/idle_terminal_linux.go'
    failure = ROOT / 'runtime/envoy/session/failure_linux.go'
    controls = [
        ('crossed-shell-exit', terminal,
         'if m.ActiveOperationID != "" || m.CWD != w.CWD {', 'if true {',
         'shutdown-cross-exit', 'actual requested shutdown closure'),
        ('terminal-eof', terminal, 'err != io.EOF {', 'err != io.EOF && false {',
         'fault-duplicate', 'duplicate terminal was not rejected before reap'),
        ('awsh-reap', terminal, 'if w.waitErr != nil {', 'if w.waitErr != nil && false {',
         'fault-signalled-awsh', 'nonzero/signalled Awsh reap cause was lost'),
        ('reap-receipt', terminal,
         '\tselect {\n\tcase <-w.reaped:\n\t\tif w.waitErr != nil {\n\t\t\treturn fmt.Errorf("terminal Awsh reap: %w", w.waitErr)\n\t\t}\n\tcase <-ctx.Done():\n\t\treturn ctx.Err()\n\t}',
         # Hold the original kernel-Wait boundary so an unrelated live-process
         # census rejection cannot hide the omitted publication/status check.
         'select {\n\tcase <-nativeTerminalHeldWait.observed:\n\tcase <-ctx.Done():\n\t\treturn ctx.Err()\n\t}',
         'matrix-closed-missing-reap', 'fatal terminal matrix emitted public closed'),
        ('adopted-shell-reap', failure, 'census.reapAdoptedShell(ctx)', 'error(nil)',
         'fault-signalled-awsh', 'actual failed terminal actors not disposed'),
        ('terminal-output-eof', terminal,
         'if err := w.channels.terminal.CloseWrite(); err != nil {',
         'if err := error(nil); err != nil {',
         'output-order', 'terminal EOF did not precede final owner release'),
    ]
    rows = []
    for name, source, old, new, case, diagnostic in controls:
        folder = root / name
        folder.mkdir()
        original = source.read_text()
        if original.count(old) != 1:
            raise ValueError('nonunique terminal omission site: ' + name)
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
            raise RuntimeError('source changed during terminal omission: ' + name)
        rows.append({'name': name, 'passed': passed, 'returncode': result.returncode,
                     'expected_diagnostic': diagnostic, 'argv': argv,
                     'started_unix': started, 'completed_unix': time.time(),
                     'source': str(source), 'source_sha256': before,
                     'overlay_sha256': sha(overlay), 'modified_sha256': sha(modified),
                     'native_log': str(native),
                     'native_log_sha256': sha(native) if native.exists() else None})
        save(root / 'progress.json', rows)
        if not passed:
            raise RuntimeError('terminal omission not detected: ' + str(folder / 'command.log'))
    save(root / 'results.json', {'status': 'passed', 'controls': rows})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--release', choices=('5.2.15', '5.2.37', '5.3.9'))
    parser.add_argument('--case', choices=CASES, help='development subset: one native scenario')
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
    with observe_wait_publication(output):
        with build_peer(output, './session') as (binary, awsh):
            for row, entry, actual in verified_rows(args.qualification.resolve(), awsh):
                if args.release and row['release'] != args.release:
                    continue
                iid, context = stage(output, row['release'], entry, actual, binary)
                argv = ['docker', 'run', '--rm', '--network=none', '-e',
                        'OMEGAFLOW_NATIVE_TERMINAL=1']
                if args.case:
                    argv += ['-e', 'OMEGAFLOW_TERMINAL_CASE=' + args.case]
                argv += ['--entrypoint=/omegaflow-runtime/bin/envoy',
                         iid, '-test.run=^TestNativeTerminalClosure$', '-test.v', '-test.failfast', '-test.timeout=230s']
                started = time.time()
                log = context / 'native.log'
                with log.open('w') as stream:
                    result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=250)
                expected_cases = (args.case,) if args.case else CASES
                transcript = log.read_text()
                passed = (result.returncode == 0 and
                          transcript.count('NATIVE TERMINAL ') == len(expected_cases) and
                          all(transcript.count('NATIVE TERMINAL ' + case + ' PROVED') == 1
                              for case in expected_cases))
                rows.append({'release': row['release'], 'passed': passed, 'returncode': result.returncode,
                             'argv': argv, 'started_unix': started, 'completed_unix': time.time(),
                             'qualified_awsh_sha256': awsh, 'qualified_base_image': actual,
                             'scenarios': list(expected_cases),
                             'wait_observation': str(output / 'wait-publication-observation/binding.json'),
                             'test_image': iid, 'test_binary_sha256': sha(binary),
                             'log': str(log), 'log_sha256': sha(log)})
                save(output / 'progress.json', rows)
                if not passed:
                    raise RuntimeError('actual terminal closure case failed: ' + str(log))
    if before != {str(p): sha(p) for p in SOURCES}:
        raise RuntimeError('source changed during proof')
    save(output / 'results.json', {'status': 'passed', 'rows': rows, 'sources': before,
                                 'source_unchanged': True, 'controls': controls,
                                 'scope': ('B8.2 development subset' if args.native_only or args.case or args.release else 'B8.2 full terminal matrix and omission controls'),
                                 'remaining': 'B8.3 deadline crossings and B8.4 full gate remain separate.'})
    print('Actual terminal closure passed: ' + str(len(rows)) + ' genuine amd64 targets')


if __name__ == '__main__':
    main()
