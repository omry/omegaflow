#!/usr/bin/env python3
"""Develop B8.3.2 against original Envoy owners and genuinely qualified actors."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import re
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/envoy-session-startup'))
from staging import build_peer, save, sha, stage, verified_rows

VARIANTS = ('valid', 'malformed', 'unknown-code', 'stalled-EOF', 'reset',
            'zero-reap', 'nonzero-reap', 'signalled-reap', 'missing-EOF',
            'unreaped', 'expiry')
PHASES = ('launch', 'ready', 'idle', 'execute-input-barrier', 'continue-input-barrier',
          'partial-execute', 'complete-execute', 'submit', 'start-prepared',
          'start-release-written', 'started', 'public-operation-started',
          'started-ack', 'start-released', 'running', 'gated', 'continuing',
          'grace', 'cleanup', 'live-inspection', 'inspection-cancellation', 'drain')
EXTRA = ('execute-input-barrier/late-closure', 'continuing/sender-timer')


def regression_omission_specs():
    return {
        'startup-cancellation-classifier': (
            'startup_linux.go', 'if !privateIOCancelled(ctx, e) {', 'if true {', {
                'TestNativePrivateFailureOwnerCancellation': 'owned cancellation manufactured private failure',
                'TestNativePrivateFailureOutputOverflow': 'actual output overflow lost its required diagnostic',
            })
    }


def omission_specs():
    # Counterfactual sources exist only in retained test overlays. A negative
    # run must fail its specific invariant; it cannot qualify a target.
    return {
        'channel-loss-drain': ('private_failure_linux.go',
            'return c.w.drainPrivateFailure(ctx)',
            'return nil, c.private.ctx.Err()',
            'execute-input-barrier', 'stalled-EOF', 'actual EOF was not retained'),
        'original-epoch': ('start_linux.go',
            'context.WithDeadline(context.WithoutCancel(ctx), c.privateFailure.deadline)',
            'context.WithTimeout(context.WithoutCancel(ctx), finalDrainBudget)',
            'execute-input-barrier', 'valid', 'fatal fact changed phase, deadline or result'),
        'actual-EOF': ('private_failure_linux.go',
            '_, err := next(ctx)', '_, err := func() (protocol.PrivateMessage, error) { return nil, io.EOF }()',
            'execute-input-barrier', 'missing-EOF', 'missing actual EOF was accepted'),
        'unreaped-evidence': ('private_failure_linux.go',
            'f.cause = errors.Join(f.cause, fmt.Errorf("unreaped Awsh under original %s epoch", f.phase))',
            '_ = f.phase', 'execute-input-barrier', 'unreaped', 'missing original Wait became a reap receipt'),
        'bounded-cause': ('private_failure_linux.go',
            'fmt.Errorf("private protocol_error %s: %s", report.Code, report.Message)',
            'fmt.Errorf("omitted protocol_error cause %s", report.Code)',
            'ready', 'unknown-code', 'bounded native cause lost'),
        'crossed-resize': ('resize_linux.go', ' && c.privateFailure == nil', '',
            'execute-input-barrier', 'valid', 'fatal barrier did not resolve its outstanding resize'),
        'fallback-epoch': ('private_failure_linux.go', 'return "", time.Time{}',
            'deadline, _ := ctx.Deadline(); return "final-drain", deadline',
            'continuing', 'sender-timer', 'fallback inherited a caller or sender deadline'),
    }


def read_map():
    mapping = json.loads(Path(__file__).with_name('case-map.json').read_text())
    inventory = [json.loads(x) for x in (ROOT / 'tests/fixtures/envoy-protocol-v1/cases.jsonl').read_text().splitlines()]
    traces = {x['id']: x for x in map(json.loads, (ROOT / 'tests/fixtures/envoy-protocol-v1/traces.jsonl').read_text().splitlines())}
    prior = json.loads((ROOT / 'tests/envoy-captured-closure/case-map.json').read_text())
    required = {x['id'] for x in inventory if x['implementation_leaf'] == 'B8.3'}
    owned, predecessor = set(mapping['cases']), set(prior['cases'])
    if mapping['schema_version'] != 1 or mapping['owning_leaf'] != 'B8.3.2' or len(owned) != 263 or len(predecessor) != 67 or owned & predecessor or owned | predecessor != required or len(required) != 330:
        raise ValueError('native map must retain exactly263 assigned cases and the cumulative330 partition')
    epochs = {}
    matrix = set()
    for key in owned:
        entry = mapping['cases'][key]
        if set(entry) != {'native_cases', 'checks'} or not (entry['native_cases'] or entry['checks']):
            raise ValueError('unproved empty case mapping: ' + key)
        if key.startswith('B1-C055-') and not key.endswith('-contract'):
            trace = traces[key]
            phase, variant = trace['input']['phase'], trace['input']['variant']
            if phase not in PHASES or variant not in VARIANTS or entry['native_cases'] != [phase + '/' + variant]:
                raise ValueError('case mapping changed phase or fault: ' + key)
            epoch = trace['epoch']
            expected = trace['expected']
            if epoch['owner'] != 'envoy' or epoch['budget_ms'] != (10000 if phase == 'launch' else 5000) or epoch['reset'] or not expected['fatal'] or expected['operation_result'] is not None or expected['reap_is_orderly']:
                raise ValueError('trace no longer describes the approved fatal epoch: ' + key)
            if phase in epochs and epochs[phase] != epoch['name']:
                raise ValueError('inconsistent phase oracle: ' + phase)
            epochs[phase] = epoch['name']
            matrix.add(phase + '/' + variant)
    if set(epochs) != set(PHASES) or len(matrix) != 242:
        raise ValueError('incomplete22phase by11variant inventory')
    native = {x for entry in mapping['cases'].values() for x in entry['native_cases']}
    if len(native) != 250:
        raise ValueError('native inventory must include242matrix,6crossed and2timing scenarios')
    return mapping, epochs, native


@contextmanager
def observe_boundaries(output, omission=None, regressions_only=False):
    flags = shlex.split(os.environ.get('GOFLAGS', ''))
    if any(x.startswith('-overlay') for x in flags):
        raise ValueError('development runner requires an unambiguous retained overlay')
    folder = output / 'observations'
    folder.mkdir()
    replacements, bindings = {}, []
    edits = {
        'start_linux.go': (
            ('barrier, endBarrier := context.WithTimeout(ctx, inputBarrierBudget)',
             'barrier, endBarrier := context.WithTimeout(ctx, inputBarrierBudget)\n'
             '\tnativePrivateBoundary(barrier, c.w, "execute-input-barrier")'),
            ('return w.DeadlineWriter.Write(data)', 'return nativePrivateExecuteWrite(w, data)'),
            ('\tvar finished bool', '\tnativePrivateOwnerHold(c)\n\tvar finished bool'),
            ('m, err := c.result(phase)\n\tif err != nil {',
             'm, err := c.result(phase)\n\tif err == nil { nativePrivateBoundary(phase, c.w, "submit") }\n\tif err != nil {'),
            ('m, err = c.result(phase)\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.phase = 0\n\tif request.ExecutionShape',
             'm, err = c.result(phase)\n\tif err == nil { nativePrivateBoundary(phase, c.w, "start-prepared") }\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.phase = 0\n\tif request.ExecutionShape'),
            ('m, err = c.result(phase)\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.phase = 0\n\terr = c.work',
             'nativePrivateBoundary(phase, c.w, "start-release-written")\n\tm, err = c.result(phase)\n\tif err == nil { nativePrivateBoundary(phase, c.w, "started") }\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.phase = 0\n\terr = c.work'),
            ('\tc.phase = awaitReleased\n', '\tnativePrivateBoundary(phase, c.w, "public-operation-started")\n\tc.phase = awaitReleased\n'),
            ('m, err = c.result(phase)\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.active.releasedAt',
             'nativePrivateBoundary(phase, c.w, "started-ack")\n\tm, err = c.result(phase)\n\tif err != nil {\n\t\treturn out, err\n\t}\n\tc.active.releasedAt'),
            ('\tc.active.releasedAt = time.Now()', '\tc.active.releasedAt = time.Now()\n\tnativePrivateBoundary(phase, c.w, "start-released")'),
            ('\tif proposal, ok := result.message.(*protocol.PrivateInputClose); ok && c.expected(proposal) {',
             '\tif c.running != nil && c.running.cleanup != nil { nativePrivateBoundary(c.running.cleanup, c.w, "cleanup") }\n\tif proposal, ok := result.message.(*protocol.PrivateInputClose); ok && c.expected(proposal) {'),
        ),
        'startup_linux.go': (
            ('\tif err = w.command.Start(); err != nil {',
             '\tif err = nativePrivateLaunchSeed(writeResult); err != nil { return }\n\tif err = w.command.Start(); err != nil {'),
            ('if privateFatal != nil && w != nil {',
             'if privateFatal != nil && w != nil { nativePrivateLaunchAccepted()'),
            ('launched.waitErr = launched.command.Wait(); close(launched.reaped)',
             'nativePrivateBeforeWait(launched); launched.waitErr = launched.command.Wait(); nativePrivateAfterWait(); close(launched.reaped)'),
            ('\treadControl.Close()\n\twriteResult.Close()',
             '\tnativePrivateBoundary(ctx, w, "launch")\n\treadControl.Close()\n\twriteResult.Close()'),
        ),
        'completion_linux.go': (
            ('\tc.phase = awaitInputClose\n', '\tnativePrivateBoundary(ctx, c.w, "running")\n\tc.phase = awaitInputClose\n'),
            ('phase, end := context.WithTimeout(context.Background(), finalDrainBudget)',
             'phase, end := context.WithTimeout(context.Background(), finalDrainBudget)\n\tnativePrivateBoundary(phase, c.w, "drain")'),
        ),
        'dispatch_linux.go': (
            ('\td.continuation = true\n', '\td.continuation = true\n\tnativePrivateBoundary(barrier, c.w, "continue-input-barrier")\n'),
            ('\tif errors.Is(err, errWorkInterrupted) {\n\t\treturn nil\n\t}\n\treturn err\n}',
             '\tif err == nil { nativePrivateBoundary(ctx, c.w, "continuing") }\n\tif errors.Is(err, errWorkInterrupted) {\n\t\treturn nil\n\t}\n\treturn err\n}'),
            ('\t\tif d.life.kind == "cancel" && !d.life.committed {\n\t\t\td.stopInspection()\n\t\t}',
             '\t\tif d.life.grace != nil { nativePrivateBoundary(d.life.grace, c.w, "grace") }\n\t\tif d.life.kind == "cancel" && !d.life.committed {\n\t\t\td.stopInspection()\n\t\t\tif d.inspection != nil && d.inspection.stop != nil { nativePrivateBoundary(d.inspection.stop, c.w, "inspection-cancellation") }\n\t\t}'),
            ('\t\td.gateEvents[m] = d.life.kind == "" && d.life.timeout == ""\n\tcase *protocol.PrivateGateContinued:',
             '\t\td.gateEvents[m] = d.life.kind == "" && d.life.timeout == ""\n\t\tnativePrivateBoundary(d.ctx, c.w, "gated")\n\tcase *protocol.PrivateGateContinued:'),
        ),
        'inspection_linux.go': (
            ('\td.inspection = i\n', '\td.inspection = i\n\tnativePrivateBoundary(ctx, c.w, "live-inspection")\n'),
        ),
        'private_failure_linux.go': (
            ('\t\t\tf.eof = true', '\t\t\tf.eof = true\n\t\t\tnativePrivateEOFObserved()'),
            ('\t\tc.privateFailure = f', '\t\tc.privateFailure = f\n\t\tnativePrivateAccepted()'),
        ),
        'control.go': (
            ('return WriteControl(ctx, w, frame)', 'return WriteControl(ctx, nativePrivateSender(w, ctx, m), frame)'),
        ),
    }
    if regressions_only:
        startup = list(edits['startup_linux.go'])
        old, new = startup[0]
        startup[0] = (old,
                      'if os.Getenv("OMEGAFLOW_PRIVATE_FAILURE_OUTPUT_OVERFLOW") == "1" { '
                      'if _, e := slave.Write([]byte(strings.Repeat("X", 4097))); e != nil { return nil, e } }\n'
                      + new)
        startup.append((
            '\tpump, err = pumpStartup(ctx, w.PTY, cancel)\n\tif err != nil {\n\t\treturn\n\t}',
            '\tpump, err = pumpStartup(ctx, w.PTY, cancel)\n\tif err != nil {\n\t\treturn\n\t}\n'
            'if os.Getenv("OMEGAFLOW_PRIVATE_FAILURE_OWNER_CANCEL") == "1" { cancel(); stopIO() }\n'
            'if os.Getenv("OMEGAFLOW_PRIVATE_FAILURE_OUTPUT_OVERFLOW") == "1" { <-pump.done; stopIO() }'))
        edits['startup_linux.go'] = tuple(startup)
    spec = omission_specs().get(omission) or regression_omission_specs().get(omission)
    if spec:
        edits.setdefault(spec[0], ())
    for name, changes in edits.items():
        source = ROOT / 'runtime/envoy/session' / name
        text = source.read_text()
        if spec and name == spec[0]:
            if text.count(spec[1]) != 1:
                raise ValueError('nonunique omission site: ' + omission)
            text = text.replace(spec[1], spec[2])
        for old, new in changes:
            if text.count(old) != 1:
                raise ValueError('nonunique observation site: ' + name)
            text = text.replace(old, new)
        observed = folder / name
        observed.write_text(text)
        replacements[str(source)] = str(observed)
        bindings.append({'source': str(source), 'source_sha256': sha(source),
                         'observed': str(observed), 'observed_sha256': sha(observed)})
    overlay = folder / 'overlay.json'
    save(overlay, {'Replace': replacements})
    save(folder / 'binding.json', {'bindings': bindings, 'overlay_sha256': sha(overlay), 'omission': omission,
                                  'role': 'Pause original Envoy acceptance or original Wait before its kernel call. No replacement process, decoder or successful peer.'})
    previous = os.environ.get('GOFLAGS')
    os.environ['GOFLAGS'] = shlex.join(flags + ['-overlay=' + str(overlay)])
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('GOFLAGS', None)
        else:
            os.environ['GOFLAGS'] = previous
        if any(sha(Path(x['source'])) != x['source_sha256'] for x in bindings):
            raise RuntimeError('source changed during native observations')


def prove_controls(root, qualification):
    root.mkdir()
    rows = []
    for name, spec in omission_specs().items():
        folder = root / name
        argv = [sys.executable, str(Path(__file__).resolve()), '--qualification', str(qualification),
                '--output', str(folder), '--release', '5.2.15', '--phase', spec[3],
                '--variant', spec[4], '--omission', name]
        if name == 'crossed-resize':
            argv = argv[:argv.index('--phase')] + ['--crossed-only', '--omission', name]
        started = time.time()
        with (root / (name + '.log')).open('w') as stream:
            result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
        log = folder / '5.2.15/native.log'
        passed = result.returncode != 0 and log.exists() and spec[5] in log.read_text()
        rows.append({'name': name, 'passed': passed, 'returncode': result.returncode,
                     'expected_diagnostic': spec[5], 'argv': argv, 'started_unix': started,
                     'completed_unix': time.time(), 'log': str(log),
                     'log_sha256': sha(log) if log.exists() else None})
        save(root / 'progress.json', rows)
        if not passed:
            raise RuntimeError('private-failure omission not detected: ' + str(root / (name + '.log')))
    save(root / 'results.json', {'status': 'passed', 'controls': rows})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--release', choices=('5.2.15', '5.2.37', '5.3.9'))
    parser.add_argument('--phase', choices=PHASES)
    parser.add_argument('--variant', choices=VARIANTS + ('late-closure', 'sender-timer'))
    parser.add_argument('--crossed-only', action='store_true')
    parser.add_argument('--extra-only', action='store_true')
    parser.add_argument('--check-map-only', action='store_true')
    parser.add_argument('--native-only', action='store_true', help='development subset: omit omission controls')
    parser.add_argument('--proof-only', action='store_true', help='development subset: omission controls only')
    parser.add_argument('--regressions-only', action='store_true', help='run only the two separate native startup regressions')
    parser.add_argument('--omission', choices=tuple(omission_specs()) + tuple(regression_omission_specs()), help='negative control; never acceptance')
    args = parser.parse_args()
    mapping, epochs, required_native = read_map()
    if args.check_map_only:
        print(json.dumps({'assigned': len(mapping['cases']), 'predecessor': 67,
                          'cumulative': 330, 'native_scenarios': len(required_native),
                          'status': 'mapping-valid; runtime acceptance not asserted'}))
        return
    if args.crossed_only and (args.phase or args.variant):
        parser.error('--crossed-only requires the complete six crossed-control cases')
    if args.extra_only and (args.phase or args.variant or args.crossed_only):
        parser.error('--extra-only requires the complete two additional timing cases')
    if args.proof_only and (args.native_only or args.phase or args.variant or args.release or args.omission or args.crossed_only or args.extra_only):
        parser.error('--proof-only requires the complete six omission controls')
    if args.regressions_only and (args.native_only or args.proof_only or args.phase or args.variant or args.release or args.crossed_only or args.extra_only):
        parser.error('--regressions-only cannot be combined with a matrix selection')
    if args.omission in regression_omission_specs() and not args.regressions_only:
        parser.error('startup regression omissions require --regressions-only')
    if args.omission in omission_specs() and (not args.release or not (args.phase or args.crossed_only)):
        parser.error('an omission requires a bounded negative native selection')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if args.regressions_only:
        inputs = [*sorted((ROOT / 'runtime/envoy/session').glob('*.go')), Path(__file__).resolve(), Path(__file__).with_name('case-map.json')]
        before = {str(p): sha(p) for p in inputs}
        specs = (
            ('OMEGAFLOW_PRIVATE_FAILURE_OWNER_CANCEL', 'TestNativePrivateFailureOwnerCancellation'),
            ('OMEGAFLOW_PRIVATE_FAILURE_OUTPUT_OVERFLOW', 'TestNativePrivateFailureOutputOverflow'),
        )
        spec = regression_omission_specs().get(args.omission)
        rows = []
        with observe_boundaries(output, omission=args.omission, regressions_only=True), build_peer(output, './session') as (binary, awsh):
            for row, entry, actual in verified_rows(args.qualification.resolve(), awsh):
                iid, folder = stage(output, row['release'], entry, actual, binary)
                for env_name, test_name in specs:
                    argv = ['docker', 'run', '--rm', '--network=none', '-e', env_name + '=1',
                            '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                            '-test.run=^' + test_name + '$', '-test.v', '-test.failfast', '-test.timeout=30s']
                    started = time.time()
                    log = folder / (test_name + '.log')
                    with log.open('w') as stream:
                        result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=60)
                    text = log.read_text()
                    if args.omission:
                        expected_diagnostic = spec[3][test_name]
                        passed = (result.returncode != 0 and
                                  ('--- FAIL: ' + test_name) in text and
                                  expected_diagnostic in text)
                    else:
                        passed = result.returncode == 0 and ('--- PASS: ' + test_name) in text
                    rows.append({'release': row['release'], 'regression': test_name, 'passed': passed,
                                 'returncode': result.returncode, 'argv': argv,
                                 'started_unix': started, 'completed_unix': time.time(),
                                 'qualified_awsh_sha256': awsh, 'qualified_base_image': actual,
                                 'test_image': iid, 'test_binary_sha256': sha(binary),
                                 'log': str(log), 'log_sha256': sha(log),
                                 **({'expected_diagnostic': expected_diagnostic} if args.omission else {})})
                    save(output / 'progress.json', rows)
                    if not passed:
                        raise RuntimeError('native startup regression: ' + str(log))
        if before != {str(p): sha(p) for p in inputs}:
            raise RuntimeError('candidate changed during native startup regressions')
        save(output / 'results.json', {
            'status': 'passed', 'rows': rows,
            'scope': 'Two separately identified startup regressions; original 250-case mapping unchanged.',
            'omission': args.omission,
            'complete_native_selection': False, 'case_map_sha256': sha(Path(__file__).with_name('case-map.json')),
            'sources': before})
        print('Native startup regressions passed: %d genuine amd64 targets x 2 cases' % (len(rows) // 2))
        return
    cases = [phase + '/' + v for phase in ((args.phase,) if args.phase else PHASES)
             for v in ((args.variant,) if args.variant else VARIANTS)]
    crossed = [phase + '/' + v + '-cancel-resize'
               for phase in ('execute-input-barrier', 'continue-input-barrier')
               for v in ('valid', 'malformed', 'unknown-code')]
    if args.crossed_only:
        cases = crossed
    elif args.extra_only:
        cases = list(EXTRA)
    elif args.phase is None and args.variant is None:
        cases += crossed + list(EXTRA)
    if not set(cases) <= required_native:
        raise ValueError('selection outside approved native mapping')
    inputs = [*sorted((ROOT / 'runtime/envoy/session').glob('*.go')), Path(__file__).resolve(), Path(__file__).with_name('case-map.json')]
    before = {str(p): sha(p) for p in inputs}
    full = set(cases) == required_native and not args.release and not args.omission
    controls = prove_controls(output / 'controls', args.qualification.resolve()) if args.proof_only or (full and not args.native_only) else []
    if args.proof_only:
        if before != {str(p): sha(p) for p in inputs}:
            raise RuntimeError('candidate changed during omission proofs')
        save(output / 'results.json', {'status': 'passed', 'controls': controls, 'sources': before,
                                      'native_acceptance_complete': False})
        return
    rows = []
    with observe_boundaries(output, args.omission), build_peer(output, './session') as (binary, awsh):
        for row, entry, actual in verified_rows(args.qualification.resolve(), awsh):
            if args.release and row['release'] != args.release:
                continue
            iid, folder = stage(output, row['release'], entry, actual, binary)
            argv = ['docker', 'run', '--rm', '--network=none', '-e',
                    'OMEGAFLOW_NATIVE_PRIVATE_FAILURE=1', '-e',
                    'OMEGAFLOW_PRIVATE_FAILURE_CASES=' + json.dumps(cases),
                    '-e', 'OMEGAFLOW_PRIVATE_FAILURE_EPOCHS=' + json.dumps(epochs),
                    '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                    '-test.run=^TestNativePrivateFailureDevelopment$', '-test.v',
                    '-test.failfast', '-test.timeout=1800s']
            started = time.time()
            log = folder / 'native.log'
            with log.open('w') as stream:
                result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=1830)
            observed = re.findall(r'NATIVE PRIVATE FAILURE ([^ ]+) PROVED:', log.read_text())
            passed = result.returncode == 0 and Counter(observed) == Counter(cases)
            rows.append({'release': row['release'], 'passed': passed,
                         'returncode': result.returncode, 'cases': cases, 'argv': argv,
                         'started_unix': started, 'completed_unix': time.time(),
                         'qualified_awsh_sha256': awsh, 'qualified_base_image': actual,
                         'test_image': iid, 'test_binary_sha256': sha(binary),
                         'log': str(log), 'log_sha256': sha(log)})
            save(output / 'progress.json', rows)
            if not passed:
                raise RuntimeError('native private failure: ' + str(log))
    if before != {str(p): sha(p) for p in inputs}:
        raise RuntimeError('candidate changed during native proof')
    save(output / 'results.json', {'status': 'passed', 'rows': rows,
                                  'scope': 'All250 assigned native scenarios; cumulative package checks still required.' if set(cases) == required_native else 'Explicit development subset only; no complete B8.3.2 or cumulative B8.3 claim.',
                                  'complete_native_selection': set(cases) == required_native,
                                  'omission': args.omission, 'controls': controls,
                                  'native_acceptance_complete': full and len(rows) == 3 and len(controls) == len(omission_specs()),
                                  'case_map_sha256': sha(Path(__file__).with_name('case-map.json')),
                                  'sources': before})
    print('Native private-failure development passed: %d genuine amd64 targets' % len(rows))


if __name__ == '__main__':
    main()
