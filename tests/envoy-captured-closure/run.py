#!/usr/bin/env python3
"""Run B8.3.1 native captured-closure development with retained observations."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/envoy-session-startup'))
from staging import build_peer, save, sha, stage, verified_rows


def read_map():
    path = Path(__file__).with_name('case-map.json')
    mapping = json.loads(path.read_text())
    if mapping['schema_version'] != 1 or mapping['owning_leaf'] != 'B8.3.1':
        raise ValueError('unexpected captured-closure map owner')
    inventory = [json.loads(line) for line in
                 (ROOT / 'tests/fixtures/envoy-protocol-v1/cases.jsonl').read_text().splitlines()]
    required = {row['id'] for row in inventory
                if row['implementation_leaf'] == 'B8.3' and
                row['requirement'] in {'INV-%03d' % i for i in (*range(36, 45), 53, 54)}}
    if set(mapping['cases']) != required or len(required) != 67:
        raise ValueError('captured-closure map does not cover its exact67 assigned cases')
    return mapping['cases']


@contextmanager
def observe_boundaries(output):
    """Freeze existing observations; preserve actual codecs, writes and Wait."""
    flags = shlex.split(os.environ.get('GOFLAGS', ''))
    overlays = [flag.split('=', 1)[1] for flag in flags if flag.startswith('-overlay=')]
    if len(overlays) > 1 or any(flag == '-overlay' for flag in flags):
        raise ValueError('native observations require an unambiguous overlay')
    folder = output / 'observations'
    folder.mkdir()
    replacements, bindings = {}, []
    if overlays:
        prior = json.loads(Path(overlays[0]).read_text())
        if set(prior) != {'Replace'} or not isinstance(prior['Replace'], dict):
            raise ValueError('unsupported omission overlay')
        replacements.update(prior['Replace'])

    def retain(name, edits):
        source = ROOT / 'runtime/envoy/session' / name
        effective = Path(replacements.get(str(source), source))
        original = effective.read_text()
        text = original
        for old, new in edits:
            if text.count(old) != 1:
                raise ValueError('nonunique native observation: ' + name + ': ' + old)
            text = text.replace(old, new)
        target = folder / name
        target.write_text(text)
        replacements[str(source)] = str(target)
        bindings.append({'source': str(source), 'source_sha256': sha(source),
                         'effective_input': str(effective), 'effective_sha256': sha(effective),
                         'observed_source': str(target), 'observed_sha256': sha(target)})

    retain('start_linux.go', [
        ('done := make(chan struct{})\n\tvar workErr error',
         'done := make(chan struct{})\n\tnativeCapturedTrackWork(ctx, done)\n\tvar workErr error'),
        ('return w.DeadlineWriter.Write(data)', 'return nativeCapturedWrite(w, data)'),
        ('return WriteControl(job, executeWriter{c.w.Control, job, c.active}, frame)',
         'return nativeCapturedExecute(job, executeWriter{c.w.Control, job, c.active}, frame)'),
        ('m, err := c.result(phase)\n\tif err != nil {',
         'm, err := c.result(phase)\n\tif err == nil { nativeCapturedBoundary(phase, "submit") }\n\tif err != nil {'),
        ('m, err = c.result(phase)\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.phase = 0\n\tif request.ExecutionShape',
         'm, err = c.result(phase)\n\tif err == nil { nativeCapturedBoundary(phase, "start-prepared") }\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.phase = 0\n\tif request.ExecutionShape'),
        ('m, err = c.result(phase)\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.phase = 0\n\terr = c.work',
         'nativeCapturedBoundary(phase, "start-release-written")\n\tm, err = c.result(phase)\n\tif err == nil { nativeCapturedBoundary(phase, "started") }\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.phase = 0\n\terr = c.work'),
        ('\tc.phase = awaitReleased\n',
         '\tnativeCapturedBoundary(phase, "public-operation-started")\n\tc.phase = awaitReleased\n'),
        ('m, err = c.result(phase)\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.active.releasedAt',
         'nativeCapturedBoundary(phase, "started-ack")\n\tm, err = c.result(phase)\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.active.releasedAt'),
        ('m, consumed, err := c.privateFact(ctx, interrupt, result)',
         'm, consumed, err := c.privateFact(ctx, interrupt, result)\n\t\tnativeCapturedFactObserved(m)'),
    ])
    retain('private_linux.go', [
        ('result := privateResult{m, err}',
         'result := privateResult{m, err}\n\t\t\tnativeCapturedPrivate(m, err)'),
        ('\t\t\t}\n\t\t\tp.mu.Lock()',
         '\t\t\t}\n\t\t\tnativeCapturedPrivatePublished(m)\n\t\t\tp.mu.Lock()'),
    ])
    retain('transport_linux.go', [
        ('\t\tif !s.emit(Event{Message: m}) {\n\t\t\treturn\n\t\t}\n',
         '\t\tif !s.emit(Event{Message: m}) {\n\t\t\treturn\n\t\t}\n\t\tnativeCapturedControlPublished(m)\n'),
    ])
    retain('dispatch_linux.go', [
        ('\td.continuation = true\n',
         '\td.continuation = true\n\tnativeCapturedContinueBarrier(barrier)\n'),
        ('err = c.work(barrier, false, func(job context.Context) error {\n\t\t\treturn c.w.channels.WaitGateInput(job, g.request.OperationID, g.request.InputThrough)\n\t\t})',
         'err = c.work(barrier, false, func(job context.Context) error {\n\t\t\tdefer nativeCapturedWorkExit(job)\n\t\t\treturn c.w.channels.WaitGateInput(job, g.request.OperationID, g.request.InputThrough)\n\t\t})'),
        ('if d.life.shellExit != nil || d.life.timeout != "" {\n\t\treturn errLifecycleEnd\n\t}',
         'if d.life.shellExit != nil || d.life.timeout != "" {\n\t\tnativeCapturedOwnerStep()\n\t\treturn errLifecycleEnd\n\t}'),
        ('return d.continueGateError(c, g, err)',
         'outcome := d.continueGateError(c, g, err)\n\t\tif errors.Is(outcome, errLifecycleEnd) { nativeCapturedOwnerStep() }\n\t\treturn outcome'),
    ])
    retain('startup_linux.go', [
        ('w.waitErr = w.command.Wait(); close(w.reaped)',
         'w.waitErr = w.command.Wait(); nativeCapturedWaitPublication(); close(w.reaped)'),
    ])
    retain('captured_closure_linux.go', [
        ('\tif failure := capturedClosureFailure(time.Now(),',
         '\tnativeCapturedClosureDecision(out.ShellExitDeadline, drain)\n\tif failure := capturedClosureFailure(time.Now(),'),
        ('\tendClosure() // Closure and rollback completed. Only final drain remains.',
         '\tendClosure() // Closure and rollback completed. Only final drain remains.\n\tnativeCapturedDrainBoundary(drain)'),
    ])
    overlay = folder / 'overlay.json'
    save(overlay, {'Replace': replacements})
    save(folder / 'binding.json', {'bindings': bindings, 'overlay_sha256': sha(overlay),
                                  'role': 'Test-only scheduling, positive partial writes and original decoder observation. Actual Envoy owners, Awsh, Bash and Wait remain.'})
    previous = os.environ.get('GOFLAGS')
    os.environ['GOFLAGS'] = shlex.join([flag for flag in flags if not flag.startswith('-overlay=')] +
                                     ['-overlay=' + str(overlay)])
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('GOFLAGS', None)
        else:
            os.environ['GOFLAGS'] = previous
        if any(sha(Path(row['source'])) != row['source_sha256'] for row in bindings):
            raise RuntimeError('owning source changed during native observation')


def prove_controls(root, qualification):
    root.mkdir()
    captured = ROOT / 'runtime/envoy/session/captured_closure_linux.go'
    controls = [
        ('captured-deadline', captured,
         'context.WithDeadline(drain, out.ShellExitDeadline)', 'context.WithCancel(drain)',
         'B1-C040-terminal-input-barrier-captured-expiry', 'captured closure was extended through final drain'),
        ('captured-priority', captured, 'if !now.Before(captured) {', 'if false {',
         'B1-C040-terminal-input-barrier-both-expire', 'captured expiry lost same-turn priority or phase'),
        ('retired-captured-context', captured,
         'c.w.finishTerminalDrain(drain, "shell_ended")', 'c.w.finishTerminalDrain(closure, "shell_ended")',
         'B1-C040-operation-start-success', 'successful closure did not retire only its captured epoch'),
        ('continue-deadline', ROOT / 'runtime/envoy/session/start_linux.go',
         'c.exitedDeadline = c.running.continueDeadline', 'c.exitedDeadline = time.Time{}',
         'B1-C036-start-released-pty-cancel-false-watermark-outstanding',
         'continue shell exit did not retain its original barrier through closure'),
        ('cancelled-gate-entry', ROOT / 'runtime/envoy/session/gate_linux.go',
         '\tg.mu.Lock()\n\tif err := ctx.Err(); err != nil {\n\t\tg.mu.Unlock()\n\t\treturn err\n\t}',
         '\tg.mu.Lock()', None, 'cancelled shell-end wait became an ownership failure'),
        ('continue-closure-bound', ROOT / 'runtime/envoy/session/dispatch_linux.go',
         'context.WithDeadline(phase, c.exitedDeadline)', 'context.WithCancel(phase)',
         'extra-continue-reap-bound', 'continuation closure was extended to later cleanup deadline'),
    ]
    rows = []
    for name, source, old, new, case, diagnostic in controls:
        folder = root / name
        folder.mkdir()
        text = source.read_text()
        if text.count(old) != 1:
            raise ValueError('nonunique captured omission site: ' + name)
        modified = folder / source.name
        modified.write_text(text.replace(old, new))
        overlay = folder / 'overlay.json'
        save(overlay, {'Replace': {str(source): str(modified)}})
        before = sha(source)
        if case is None:
            argv = ['go', '-C', str(ROOT / 'runtime/envoy'), 'test', '-race', '-buildvcs=false',
                    '-count=1', '-timeout=30s', '-run', '^TestCapturedClosureCancelledGateWaitAfterInputReset$', './session']
            log = folder / 'command.log'
        else:
            argv = [sys.executable, str(Path(__file__).resolve()), '--qualification', str(qualification),
                    '--native-only', '--release', '5.2.15', '--case', case, '--output', str(folder / 'native')]
            log = folder / 'native/5.2.15/native.log'
        started = time.time()
        with (folder / 'command.log').open('w') as stream:
            result = subprocess.run(argv, env=os.environ | {
                'CHGDISABLE': '1', 'GOFLAGS': '-overlay=' + str(overlay)},
                stdout=stream, stderr=subprocess.STDOUT, timeout=180)
        passed = result.returncode != 0 and log.exists() and diagnostic in log.read_text()
        if sha(source) != before:
            raise RuntimeError('owning source changed during omission: ' + name)
        rows.append({'name': name, 'passed': passed, 'returncode': result.returncode,
                     'expected_diagnostic': diagnostic, 'argv': argv,
                     'started_unix': started, 'completed_unix': time.time(),
                     'source': str(source), 'source_sha256': before,
                     'modified_sha256': sha(modified), 'overlay_sha256': sha(overlay),
                     'log': str(log), 'log_sha256': sha(log) if log.exists() else None})
        save(root / 'progress.json', rows)
        if not passed:
            raise RuntimeError('captured omission not detected: ' + str(folder / 'command.log'))
    save(root / 'results.json', {'status': 'passed', 'controls': rows})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release', choices=('5.2.15', '5.2.37', '5.3.9'))
    parser.add_argument('--case', action='append', help='explicit development subset')
    parser.add_argument('--native-only', action='store_true', help='development subset: omit controls')
    parser.add_argument('--proof-only', action='store_true', help='development subset: controls only')
    args = parser.parse_args()
    if args.native_only and args.proof_only:
        parser.error('choose one development subset')
    if args.proof_only and (args.case or args.release):
        parser.error('--proof-only requires all omission controls')
    mapping = read_map()
    mapping['extra-continue-reap-bound'] = 'continue/pty/hold-reap'
    selected = args.case or list(mapping)
    if len(selected) != len(set(selected)) or set(selected) - set(mapping):
        parser.error('duplicate or unmapped development case')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = sorted((ROOT / 'runtime/envoy/session').glob('*.go')) + [
        Path(__file__).resolve(), Path(__file__).with_name('case-map.json').resolve()]
    before = {str(path): sha(path) for path in sources}
    controls = [] if args.native_only or args.case or args.release else prove_controls(output / 'controls', args.qualification.resolve())
    if args.proof_only:
        if before != {str(path): sha(path) for path in sources}:
            raise RuntimeError('source changed during omission controls')
        save(output / 'results.json', {'status': 'passed', 'scope': 'Omission controls only',
                                      'controls': controls, 'native_acceptance_complete': False})
        return
    rows = []
    with observe_boundaries(output):
        with build_peer(output, './session') as (binary, awsh):
            for row, entry, actual in verified_rows(args.qualification.resolve(), awsh):
                if args.release and row['release'] != args.release:
                    continue
                iid, context = stage(output, row['release'], entry, actual, binary)
                argv = ['docker', 'run', '--rm', '--network=none', '-e',
                        'OMEGAFLOW_NATIVE_CAPTURED_CLOSURE=1', '-e',
                        'OMEGAFLOW_CAPTURED_CASES=' + json.dumps(selected, separators=(',', ':')),
                        '-e', 'OMEGAFLOW_CAPTURED_MAP=' + json.dumps(mapping, separators=(',', ':')),
                        '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                        '-test.run=^TestNativeCapturedClosureDevelopment$', '-test.v',
                        '-test.failfast', '-test.timeout=300s']
                owner_order = os.environ.get('OMEGAFLOW_CAPTURED_OWNER_ORDER')
                if owner_order:
                    argv[argv.index('--entrypoint=/omegaflow-runtime/bin/envoy'):argv.index('--entrypoint=/omegaflow-runtime/bin/envoy')] = [
                        '-e', 'OMEGAFLOW_CAPTURED_OWNER_ORDER=' + owner_order]
                started = time.time()
                log = context / 'native.log'
                with log.open('w') as stream:
                    result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=320)
                transcript = log.read_text()
                passed = (result.returncode == 0 and
                          all(transcript.count('NATIVE CAPTURED ' + case + ' PROVED') == 1 for case in selected))
                rows.append({'release': row['release'], 'passed': passed, 'returncode': result.returncode,
                             'argv': argv, 'started_unix': started, 'completed_unix': time.time(),
                             'qualified_awsh_sha256': awsh, 'qualified_base_image': actual,
                             'cases': selected, 'test_image': iid, 'test_binary_sha256': sha(binary),
                             'log': str(log), 'log_sha256': sha(log)})
                save(output / 'progress.json', rows)
                if not passed:
                    raise RuntimeError('native captured case failed: ' + str(log))
    if before != {str(path): sha(path) for path in sources}:
        raise RuntimeError('source changed during native captured run')
    save(output / 'results.json', {'status': 'passed', 'rows': rows, 'sources': before,
                                  'source_unchanged': True,
                                  'controls': controls,
                                  'owning_corpus_cases': 67,
                                  'additional_proofs': ['extra-continue-reap-bound'],
                                  'scope': ('B8.3.1 full native matrix and omission controls' if controls else 'Development native subset'),
                                  'native_acceptance_complete': len(rows) == 3 and len(selected) == 68 and len(controls) == 6})
    print('Native captured development passed: ' + str(len(selected)) + ' cases per target')


if __name__ == '__main__':
    main()
