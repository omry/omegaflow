#!/usr/bin/env python3
"""Assemble explicit case obligations for the final local Envoy gate."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
CORPUS = Path('tests/fixtures/envoy-protocol-v1/cases.jsonl')
MAPPING = Path('tests/envoy-conformance/case-map.json')
RELEASES = {'5.2.15', '5.2.37', '5.3.9'}
START_PHASES = ('before-first-byte', 'partial-execute', 'complete-execute', 'submit',
                'start-prepared', 'start-release-written', 'started',
                'public-operation-started', 'started-ack', 'start-released')
BASE_ASSEMBLY_CASES = ['adapter-state', 'status-and-source', 'source-rejection', 'inspection-paths'] + [
    f'start-cancel/{mode}/{phase}' for mode in ('pty', 'split') for phase in START_PHASES]

def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_json(path):
    require(path.is_file() and not path.is_symlink(), 'regular evidence file required: ' + str(path))
    require(path.stat().st_size <= 8 * 1024 * 1024, 'evidence exceeds bound: ' + str(path))
    return json.loads(path.read_text(), object_pairs_hook=unique_object)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_inventory(root):
    """Return the exact regular-file evidence produced below an execution root."""
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(),
            'regular execution evidence directory required: ' + str(root))
    inventory = {}
    execution = root / 'execution.json'
    for path in sorted(root.rglob('*')):
        if path == execution:
            continue
        require(not path.is_symlink(), 'symlink produced evidence: ' + str(path))
        if path.is_dir():
            continue
        require(path.is_file(), 'regular produced evidence required: ' + str(path))
        inventory[str(path.relative_to(root))] = sha(path)
    return inventory


def check_artifact_inventory(root, expected):
    """Reject missing, extra, or changed evidence bound at execution completion."""
    actual = artifact_inventory(root)
    require(actual == expected, 'produced evidence artifact inventory changed')
    return actual


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reserved_cases():
    rows = read_json(ROOT / 'tests/envoy-bash-reserved-state/cases.json')['cases']
    result = [row for row in rows if row['mode'] == 'fatal']
    require(len({row['id'] for row in result}) == len(result) == 120, 'reserved mutation inventory drift')
    return result


def assembly_cases():
    return BASE_ASSEMBLY_CASES + ['reserved/' + row['id'] for row in reserved_cases()]


def source_binding(root=ROOT):
    """Bind all runtime and local Envoy proof inputs, including file modes."""
    paths = set()
    for folder in ('runtime/envoy', 'tests/fixtures/envoy-protocol-v1', 'docs/design', 'src', 'changes'):
        paths.update(path for path in (root / folder).rglob('*') if path.is_file())
    paths.update((root / 'tests').glob('test_*.py'))
    for name in ('pyproject.toml', 'tools/bash_builds.py', 'noxfile.py', 'CHANGELOG.md',
                 'website/scripts/update_recording_schema_docs.py',
                 'website/docs/reference/configuration/recordings.md',
                 'website/docs/reference/recording-files/schema.md',
                 'website/docs/reference/output/index.md'):
        require((root / name).is_file(), 'missing required source input: ' + name)
        paths.add(root / name)
    if (root / 'uv.lock').is_file():
        paths.add(root / 'uv.lock')
    for folder in (root / 'tests').glob('envoy-*'):
        paths.update(path for path in folder.rglob('*')
                     if path.is_file() and '__pycache__' not in path.parts)
    return {str(path.relative_to(root)): {'sha256': sha(path), 'mode': path.stat().st_mode & 0o777,
                                         'symlink': os.readlink(path) if path.is_symlink() else None}
            for path in sorted(paths) if '__pycache__' not in path.parts}


@contextmanager
def assembly_observations(output):
    captured = load_module('conformance_captured', ROOT / 'tests/envoy-captured-closure/run.py')
    with captured.observe_boundaries(output):
        # Reuse the owning observer and make only its repeated hold one-shot.
        # The actual write loop, process proof and cancellation remain intact.
        source = ROOT / 'runtime/envoy/session/captured_closure_native_linux_test.go'
        text, anchor = source.read_text(), 'close(h.entered)'
        require(text.count(anchor) == 1, 'nonunique conformance observation anchor')
        modified = output / 'conformance-observer.go'
        modified.write_text(text.replace(anchor, 'if nativeConformanceBoundary(ctx) { return }; ' + anchor))
        overlay = output / 'observations/overlay.json'
        binding = read_json(overlay)
        binding['Replace'][str(source)] = str(modified)
        overlay.write_text(json.dumps(binding, indent=2) + '\n')
        (output / 'conformance-observer-binding.json').write_text(json.dumps({
            'source': str(source), 'source_sha256': sha(source),
            'observed_source': str(modified), 'observed_sha256': sha(modified),
            'overlay_sha256': sha(overlay)}, indent=2) + '\n')
        yield


def check_assembly(receipt):
    require(receipt['version'] == 1 and receipt['kind'] == 'actual-actor-assembly' and
            receipt['status'] == 'passed', 'wrong or incomplete assembly receipt')
    require(receipt['sources'] == source_binding(), 'assembly source or mode drift')
    targets = qualification_bindings(Path(receipt['qualification']), receipt['awsh_sha256'])
    require(Counter(row['release'] for row in receipt['rows']) == Counter(RELEASES),
            'assembly requires every genuine target exactly once')
    identities = set()
    expected = assembly_cases()
    for row in receipt['rows']:
        target = targets[row['release']]
        require(row['qualified_base_image'] == target['test_image'] and
                row['qualification_receipt_sha256'] == target['receipt_sha256'],
                'assembly qualification target drift')
        require(row['returncode'] == 0 and row['cases'] == expected, 'partial or failed assembly')
        log = Path(row['log'])
        require(log.is_file() and not log.is_symlink(), 'regular native transcript required')
        require(sha(log) == row['log_sha256'], 'assembly transcript changed')
        text = log.read_text()
        observed = re.findall(r'NATIVE CONFORMANCE ([^ ]+) PROVED', text)
        require(Counter(observed) == Counter(expected) and '--- SKIP:' not in text and
                '--- FAIL:' not in text and '\nPASS\n' in text, 'missing actual assembly proof')
        require(row['qualified_awsh_sha256'] == receipt['awsh_sha256'], 'mixed qualified Awsh')
        identities.add(row['qualified_base_image'])
        require(len(row['test_binary_sha256']) == 64, 'missing native executable identity')
    require(len(identities) == len(RELEASES), 'target images were substituted')
    return receipt


def native_assembly(qualification, output):
    output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(ROOT / 'tests/envoy-session-startup'))
    from staging import build_peer, save, stage, verified_rows
    sources, started = source_binding(), time.time()
    result = {'version': 1, 'kind': 'actual-actor-assembly', 'status': 'incomplete',
              'sources': sources, 'rows': [], 'started_unix': started,
              'qualification': str(qualification),
              'scope': 'Source/start assembly and exact reserved mutation scenarios; this alone does not close the B1-B8 case inventory.'}
    with assembly_observations(output), build_peer(output, './session') as (binary, awsh):
        result['awsh_sha256'] = awsh
        for row, entry, actual in verified_rows(qualification, awsh):
            iid, context = stage(output, row['release'], entry, actual, binary)
            argv = ['docker', 'run', '--rm', '--network=none', '-e', 'OMEGAFLOW_NATIVE_CONFORMANCE=1',
                    '-e', 'OMEGAFLOW_CONFORMANCE_RESERVED_CASES=' + json.dumps(reserved_cases(), separators=(',', ':')),
                    '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                    '-test.run=^TestNativeConformance$', '-test.v', '-test.timeout=240s']
            log, begin = context / 'native.log', time.time()
            with log.open('x') as stream:
                run = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=270)
            result['rows'].append({'release': row['release'], 'returncode': run.returncode,
                                   'cases': assembly_cases(), 'argv': argv, 'started_unix': begin,
                                   'completed_unix': time.time(), 'qualified_awsh_sha256': awsh,
                                   'qualification_receipt_sha256': row['receipt_sha256'],
                                   'qualified_base_image': actual, 'test_image': iid,
                                   'test_binary_sha256': sha(binary), 'log': str(log), 'log_sha256': sha(log)})
            save(output / 'results.json', result)
            require(run.returncode == 0, 'actual assembly failed: ' + str(log))
    require(source_binding() == sources, 'source changed during actual assembly')
    result.update(status='passed', completed_unix=time.time())
    check_assembly(result)
    save(output / 'results.json', result)
    return result


def qualification_bindings(qualification, awsh):
    rows = read_json(qualification / 'results.json')
    require(Counter(row['release'] for row in rows) == Counter(RELEASES), 'incomplete qualification matrix')
    result = {}
    for row in rows:
        folder = qualification / row['release']
        receipt = read_json(folder / 'receipt.json')
        entry = read_json(folder / 'entry.json')
        require(row['qualified'] and row['returncode'] == 0 and
                row['receipt_sha256'] == sha(folder / 'receipt.json') and entry['qualification'] == receipt,
                'unverified qualification receipt')
        require(receipt['adapter_inputs']['/omegaflow-runtime/bin/awsh'] == awsh, 'qualification Awsh drift')
        for name in ('awsh-bashrc', 'inputrc'):
            require(sha(ROOT / 'runtime/envoy/etc' / name) ==
                    receipt['adapter_inputs']['/omegaflow-runtime/etc/' + name], 'qualified startup asset drift')
        result[row['release']] = row
    return result


def full_commands(qualification, output, python):
    """Fixed full selection, including all three complete private selections."""
    commands = [
        ('go-race', ['go', '-C', 'runtime/envoy', 'test', '-json', '-race', '-buildvcs=false',
                     '-count=1', '-timeout=300s', './...']),
        ('python', [python, '-m', 'nox', '-s', 'ci', '--', '-q',
                    '--junitxml=' + str(output / 'python-junit.xml')]),
    ]
    for name, script in [('inspection-proofs', 'envoy-inspection'),
                         ('inspection-worker-proofs', 'envoy-inspection-worker')]:
        commands.append((name, [python, 'tests/' + script + '/run.py',
                                '--output', str(output / name)]))
    # Start the slow independent target matrices early. Each target still runs
    # every native case; the separate controls job remains mandatory.
    for release in sorted(RELEASES):
        name = 'private-failure-' + release
        commands.append((name, [python, 'tests/envoy-private-failure/run.py',
                                '--qualification', str(qualification), '--output', str(output / name),
                                '--release', release, '--native-only']))
    commands.append(('private-failure-controls', [python, 'tests/envoy-private-failure/run.py',
                     '--qualification', str(qualification), '--output', str(output / 'private-failure-controls'),
                     '--proof-only']))
    for name, script in [
        ('public-startup', 'envoy-session-startup/public.py'),
        ('private-startup', 'envoy-session-startup/private.py'),
        ('ordinary', 'envoy-session-startup/ordinary.py'),
        ('resize', 'envoy-session-startup/resize.py'),
        ('controls', 'envoy-session-startup/controls.py'),
        ('gate', 'envoy-session-startup/gate.py'),
        ('lifecycle-signal', 'envoy-session-startup/lifecycle.py'),
        ('split-setup', 'envoy-split-setup/run.py'),
        ('split-streams', 'envoy-split-streams/run.py'),
        ('split-completion', 'envoy-split-completion/run.py'),
        ('process-cleanup', 'envoy-process-cleanup/run.py'),
        ('return-cleanup', 'envoy-return-cleanup/run.py'),
        ('process-lifecycle', 'envoy-process-lifecycle/run.py'),
        ('evidence-ranges', 'envoy-evidence-ranges/run.py'),
        ('inspection-lifecycle', 'envoy-inspection-lifecycle/run.py'),
        ('failure-isolation', 'envoy-failure-isolation/run.py'),
        ('terminal-closure', 'envoy-terminal-closure/run.py'),
        ('captured-closure', 'envoy-captured-closure/run.py'),
        ('assembly', 'envoy-conformance/run.py'),
    ]:
        argv = [python, 'tests/' + script, '--qualification', str(qualification),
                '--output', str(output / name)]
        if name == 'assembly':
            argv.append('--native-assembly')
        commands.append((name, argv))
    for name, flags in [
        ('private-startup-regressions', ['--regressions-only']),
        ('private-startup-cancellation-control', ['--regressions-only', '--omission',
                                                  'startup-cancellation-classifier']),
    ]:
        commands.append((name, [python, 'tests/envoy-private-failure/run.py', '--qualification',
                                str(qualification), '--output', str(output / name), *flags]))
    return commands


def execute_full(qualification, output):
    output.mkdir(parents=True, exist_ok=False)
    sources, begin = source_binding(), time.time()
    commands = full_commands(qualification, output, sys.executable)
    result = {'version': 1, 'kind': 'full-B-execution', 'status': 'incomplete',
              'python': sys.executable,
              'tools': {'python': sys.version, 'python_sha256': sha(Path(sys.executable)),
                        'go': subprocess.check_output(['go', 'version'], text=True).strip(),
                        'docker': subprocess.check_output(['docker', '--version'], text=True).strip()},
              'sources': sources, 'qualification': str(qualification), 'started_unix': begin,
              'commands': [], 'case_catalog': catalog(),
              'timing': 'Command wall intervals may overlap. Exclusive edit/CPU/service wait is unavailable.'}

    def run(item):
        name, argv = item
        log, started = output / (name + '.log'), time.time()
        # Each native runner owns its processes, private observation overlays
        # and isolated containers. No thread mutates shared runtime inputs.
        with log.open('x') as stream:
            try:
                process = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                         env=os.environ | {'PYTHONPATH': str(ROOT / 'src'),
                                                           'PYTEST_ADDOPTS': ''}, timeout=3600)
                code, failure = process.returncode, None
            except subprocess.TimeoutExpired:
                code, failure = None, 'harness command timeout; child activity needs reconciliation'
        return {'name': name, 'argv': argv, 'cwd': str(ROOT), 'returncode': code,
                'failure': failure, 'started_unix': started, 'completed_unix': time.time(),
                'log': str(log), 'log_sha256': sha(log)}

    # Independent native images keep the same exact source snapshot. Three
    # command workers cap load while preserving command-specific transcripts.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run, (name, argv)) for name, argv in commands]
        for future in as_completed(futures):
            row = future.result()
            result['commands'].append(row)
            (output / 'execution.json').write_text(json.dumps(result, indent=2) + '\n')
            print(row['name'] + ': ' + str(row['returncode']), flush=True)
    result.update(completed_unix=time.time(), source_unchanged=source_binding() == sources)
    passed = result['source_unchanged'] and all(row['returncode'] == 0 for row in result['commands'])
    result['status'] = 'passed' if passed else 'failed'
    # Execution and case closure are separate claims. In particular a green
    # package or component oracle cannot turn an absent native locator green.
    result['case_closure'] = 'unverified'
    if passed:
        # Bind every produced side-artifact after all successful commands have
        # completed. The root execution receipt is excluded because it stores
        # this inventory and would otherwise hash itself recursively.
        result['artifacts'] = artifact_inventory(output)
    (output / 'execution.json').write_text(json.dumps(result, indent=2) + '\n')
    require(passed, 'full execution failed; retain each command transcript')
    return result


def command_index(receipt, expected):
    """Reject missing, duplicated, changed or failed command evidence."""
    rows = receipt['commands']
    require(Counter(row['name'] for row in rows) == Counter(name for name, _ in expected),
            'incomplete or duplicate full execution selection')
    index = {row['name']: row for row in rows}
    for name, argv in expected:
        row = index[name]
        require(row['argv'] == argv and row['cwd'] == str(ROOT) and
                type(row['returncode']) is int and row['returncode'] == 0 and row['failure'] is None,
                'changed or failed full command: ' + name)
        path = Path(row['log'])
        require(path.is_file() and not path.is_symlink() and sha(path) == row['log_sha256'],
                'changed full command transcript: ' + name)
    return index


def native_matrix(folder, qualification, awsh):
    """Verify each genuine image, executable transcript and successful root."""
    receipt = read_json(folder / 'results.json')
    rows = receipt if isinstance(receipt, list) else receipt['rows']
    require(Counter(row['release'] for row in rows) == Counter(RELEASES),
            'missing native target: ' + str(folder))
    for row in rows:
        release = row['release']
        require(row['returncode'] == 0 and row['qualified_awsh_sha256'] == awsh,
                'failed or stale native target: ' + str(folder))
        if 'qualified_base_image' in row:
            require(row['qualified_base_image'] == qualification[release]['test_image'], 'native base image drift')
        if 'base_image' in row:
            require(row['base_image'] == qualification[release]['test_image'], 'native base image drift')
        if 'receipt_sha256' in row:
            require(row['receipt_sha256'] == qualification[release]['receipt_sha256'], 'native qualification drift')
        if 'qualification_receipt_sha256' in row:
            require(row['qualification_receipt_sha256'] == qualification[release]['receipt_sha256'],
                    'native qualification drift')
        dockerfile = folder / release / 'Dockerfile'
        require(dockerfile.read_text().startswith('FROM ' + qualification[release]['test_image'] + '\n'),
                'native staging parent differs from genuine target')
        image = row.get('native_image', row.get('test_image'))
        require((folder / release / 'image-id.txt').read_text().strip() == image, 'native image identity drift')
        path = folder / release / 'native.log'
        require(path.is_file() and not path.is_symlink(), 'missing native transcript: ' + str(path))
        text = path.read_text()
        require('--- PASS: TestNative' in text and '\nPASS\n' in text and
                '--- SKIP:' not in text and '--- FAIL:' not in text,
                'native fixture skipped or failed: ' + str(path))
        require(sha(path) == row['log_sha256'], 'native transcript drift: ' + str(path))
    return receipt


def startup_regressions(root, qualified, awsh):
    coverage = load_module('conformance_private_coverage', ROOT / 'tests/envoy-private-failure/coverage.py')
    diagnostics = coverage.runner.regression_omission_specs()['startup-cancellation-classifier'][3]
    expected = Counter((release, name) for release in RELEASES for name in diagnostics)
    for folder, omission in [('private-startup-regressions', None),
                             ('private-startup-cancellation-control', 'startup-cancellation-classifier')]:
        result = read_json(root / folder / 'results.json')
        require(result['status'] == 'passed' and result['omission'] == omission and
                Counter((row['release'], row['regression']) for row in result['rows']) == expected,
                'incomplete native startup regressions or control')
        coverage.current_sources(result, 'envoy-private-failure')
        for row in result['rows']:
            log = Path(row['log'])
            require(log.is_file() and not log.is_symlink() and sha(log) == row['log_sha256'] and
                    row['qualified_awsh_sha256'] == awsh and
                    row['qualified_base_image'] == qualified[row['release']]['test_image'],
                    'stale startup regression evidence')
            text = log.read_text()
            if omission is None:
                require(row['returncode'] == 0 and '--- PASS: ' + row['regression'] in text and
                        '--- SKIP:' not in text, 'native startup regression skipped or failed')
            else:
                require(row['returncode'] != 0 and diagnostics[row['regression']] in text and
                        '--- FAIL: ' + row['regression'] in text, 'startup omission was not detected')


INSPECTION_VARIANTS = (
    ('snapshot', False, 8), ('component', False, 1), ('read', False, 1),
    ('snapshot', True, 0), ('component', True, 0), ('read', True, 0),
    ('framing', True, 0), ('readlink-error', False, 2), ('readlink-error', True, 0),
    ('open-error', False, 4), ('open-error', True, 0),
    ('open-entry-error', False, 2), ('open-entry-error', True, 0),
)
WORKER_FIXTURES = ('malformed', 'trailing', 'large', 'mismatch', 'nonzero', 'late-exit', 'block')
WORKER_CONTROLS = ('test-parent-setup', 'environment', 'inheritance', 'correlation',
                   'validation', 'reap', 'stream-bound')
WORKER_DIAGNOSTICS = (
    'inherited test-parent descriptor prevented worker launch', 'actual inspection:',
    'broken inheritance protection did not reject before fork',
    'invalid child result accepted by b7-worker-mismatch',
    'accepted malformed result "duplicate-field"', 'caller bound falsely proved reap',
    'context deadline exceeded',
)


def inspection_proofs(root):
    """Require the explicit inspection fault suites skipped by ordinary Go tests."""
    folder = root / 'inspection-proofs'
    rows = read_json(folder / 'results.json')
    require([(r['mode'], r['missing_check_control'], r['expected_mutations']) for r in rows] ==
            list(INSPECTION_VARIANTS), 'incomplete inspection fault/control selection')
    for row in rows:
        require(row['proved'] is True and type(row['returncode']) is int,
                'unproved inspection fault/control')
        log = Path(row['log'])
        require(log.is_file() and not log.is_symlink() and log.parent.parent == folder and
                sha(log) == row['log_sha256'], 'changed inspection fault transcript')
        overlay = log.parent / 'overlay.json'
        require(sha(overlay) == row['overlay_sha256'], 'changed inspection fault overlay')
        text, mode = log.read_text(), row['mode']
        if row['missing_check_control']:
            diagnostic = ('digest ' if mode == 'framing' else
                          'want inspection-read' if mode in {'readlink-error', 'open-error', 'open-entry-error'}
                          else 'want inspection-unstable')
            require(row['returncode'] != 0 and diagnostic in text and '--- FAIL: Test' in text,
                    'inspection omission was not detected')
        else:
            marker = ('OBSERVED READLINK FAILURE ' if mode == 'readlink-error' else
                      'OBSERVED OPEN FAILURE ' if mode in {'open-error', 'open-entry-error'} else
                      'OBSERVED INSPECTION MUTATION ')
            require(row['returncode'] == 0 and text.count(marker) == row['expected_mutations'] and
                    '--- SKIP:' not in text and '--- FAIL:' not in text and '\nPASS\n' in text,
                    'inspection fault skipped or failed')

    worker = read_json(root / 'inspection-worker-proofs/results.json')
    names = ['build-baseline', 'normal'] + ['fixture-' + name for name in WORKER_FIXTURES]
    names += [prefix + name for name in WORKER_CONTROLS for prefix in ('build-control-', 'control-')]
    require(worker['status'] == 'passed' and worker['source_unchanged'] is True and
            worker['actual_fault_fixtures'] == list(WORKER_FIXTURES) and
            worker['negative_controls'] == list(WORKER_CONTROLS) and
            Counter(row['name'] for row in worker['checks']) == Counter(names),
            'incomplete inspection worker selection')
    expected = {str(ROOT / 'runtime/envoy/inspection' / name) for name in
                ('worker_linux.go', 'worker_codec_linux.go',
                 'worker_process_linux_test.go', 'worker_codec_linux_test.go')}
    require(set(worker['sources']) == expected and
            all(sha(Path(path)) == digest for path, digest in worker['sources'].items()),
            'inspection worker source drift')
    for row in worker['checks']:
        log = Path(row['log'])
        require(log.is_file() and not log.is_symlink() and
                log.parent == root / 'inspection-worker-proofs' and
                sha(log) == row['log_sha256'], 'changed inspection worker transcript')
        require(row['passed'] is True and row['timed_out'] is False and
                type(row['returncode']) is int, 'unproved inspection worker check')
        if row['name'].startswith('control-'):
            expected = dict(zip(WORKER_CONTROLS, WORKER_DIAGNOSTICS))[row['name'].removeprefix('control-')]
            require(row['expected_failure'] == expected and
                    row['returncode'] != 0 and expected in log.read_text(),
                    'inspection worker omission was not detected')
        else:
            require(row['returncode'] == 0 and row['expected_failure'] is None,
                    'inspection worker baseline failed')
            if row['name'].startswith('fixture-'):
                fixture = row['name'].removeprefix('fixture-')
                require(Path(row['argv'][0]).name == 'b7-worker-' + fixture and
                        row['argv'][1:] == ['-test.run=^TestWorkerFixture$', '-test.timeout=20s'] and
                        log.read_text() == 'PASS\n', 'worker fixture was not actually selected')
    return {'inspection_fault_rows': len(rows), 'inspection_worker_checks': len(worker['checks'])}


def locator_test_roots(proof):
    if proof['kind'] in {'static', 'qualified-component'}:
        return set()
    names = proof.get('tests')
    if names is None:
        names = re.findall(r'Test[A-Za-z0-9_]+', json.dumps(proof.get('value')))
    require(isinstance(names, (list, tuple, set)) and
            all(isinstance(name, str) and name for name in names),
            'invalid executable owning locator')
    return {name.split('/')[0] for name in names}


def requires_executable_owner(case):
    """Static corpus and qualified receipts have separate ownership checks."""
    return (not case.get('implementation_leaf', '').startswith('B1.') and
            not any(proof['kind'] == 'qualified-component' for proof in case['locators']))


def require_executable_owner(case, names=None, native_scenario=None):
    if names is None or native_scenario is None:
        names, native_scenario = set(), False
        for proof in case['locators']:
            names.update(locator_test_roots(proof))
            native_scenario |= (proof['kind'] == 'native' and
                                proof.get('value') not in (None, '', [], {}))
    if requires_executable_owner(case):
        require(names or native_scenario,
                'case lacks concrete executable owning oracle: ' + str(case.get('id', '<unknown>')))


def owning_oracles(case, observed):
    """Keep stale named locators explicit and require their actual replacement."""
    passed, missing = set(), set()
    executable, native_scenario = set(), False
    for proof in case['locators']:
        names = locator_test_roots(proof)
        executable.update(names)
        native_scenario |= (proof['kind'] == 'native' and
                            proof.get('value') not in (None, '', [], {}))
        passed.update(names & observed)
        missing.update(names - observed)
    require_executable_owner(case, executable, native_scenario)
    replacement = [proof for proof in case['locators'] if proof['kind'] == 'native' and
                   proof.get('tests') and all(name.split('/')[0] in observed for name in proof['tests'])]
    require(not missing or replacement, 'owning oracle did not execute: ' + ', '.join(sorted(missing)))
    return {'passed_test_roots': sorted(passed), 'historical_unexecuted_test_roots': sorted(missing),
            'actual_replacement_proofs': replacement if missing else []}


# Each runtime obligation retains its case-specific oracle and also requires
# the listed peer-free crossing suites. Qualified helper/component receipts
# alone cannot supply final runtime closure. C/D owners remain outside this map.
INTEGRATION = {
    'B2.1': ('public-startup', 'assembly'), 'B2.2': ('private-failure', 'assembly'),
    'B2.3': ('public-startup', 'assembly'), 'B2.4': ('assembly',),
    'B2.5': ('assembly', 'private-failure'), 'B2.6': ('assembly', 'private-failure'),
    'B2.7': ('return-cleanup', 'inspection-lifecycle'), 'B2.8': ('public-startup', 'assembly'),
    'B3.1': ('public-startup', 'failure-isolation'), 'B3.2': ('public-startup', 'private-failure'),
    'B3.3': ('assembly', 'captured-closure'), 'B3.4': ('return-cleanup', 'inspection-lifecycle'),
    'B4.1': ('evidence-ranges', 'process-lifecycle'), 'B4.2': ('assembly', 'process-lifecycle'),
    'B4.3': ('captured-closure', 'private-failure'), 'B4.4': ('process-lifecycle', 'inspection-lifecycle'),
    'B5.1': ('assembly', 'return-cleanup'), 'B5.2': ('return-cleanup', 'evidence-ranges'),
    'B5.3': ('return-cleanup', 'process-lifecycle'),
    'B6.1': ('return-cleanup', 'process-lifecycle'), 'B6.2': ('return-cleanup', 'process-lifecycle'),
    'B6.3': ('return-cleanup', 'process-lifecycle'), 'B6.4': ('evidence-ranges', 'inspection-lifecycle'),
    'B6.5': ('process-lifecycle',), 'B7.1': ('inspection-lifecycle',),
    'B7.2': ('inspection-lifecycle', 'failure-isolation'), 'B7.3': ('inspection-lifecycle', 'private-failure'),
    'B8.1': ('failure-isolation',), 'B8.2': ('terminal-closure',),
    'B8.3': ('captured-closure', 'private-failure'), 'B8.4': ('assembly', 'private-failure'),
}


def verify_final(path, output):
    receipt = read_json(path)
    root = path.resolve().parent
    require(not output.exists() and not output.resolve().is_relative_to(root),
            'retain existing evidence; place the final report outside the execution directory')
    require(receipt['version'] == 1 and receipt['kind'] == 'full-B-execution' and
            receipt['status'] == 'passed' and receipt['source_unchanged'], 'full execution is unfinished or failed')
    check_artifact_inventory(root, receipt.get('artifacts'))
    sources = source_binding()
    require(receipt['sources'] == sources and receipt['case_catalog'] == catalog(), 'stale execution inputs')
    require(receipt['tools']['python_sha256'] == sha(Path(sys.executable)) and
            receipt['tools']['python'] == sys.version, 'verification interpreter differs from execution')
    qualification = Path(receipt['qualification'])
    commands = command_index(receipt, full_commands(qualification, root, receipt['python']))
    assembly = check_assembly(read_json(root / 'assembly/results.json'))
    qualified = qualification_bindings(qualification, assembly['awsh_sha256'])
    for name in ('public-startup', 'return-cleanup', 'process-lifecycle', 'evidence-ranges',
                 'inspection-lifecycle', 'failure-isolation', 'terminal-closure', 'captured-closure'):
        native_matrix(root / name, qualified, assembly['awsh_sha256'])
    startup_regressions(root, qualified, assembly['awsh_sha256'])
    inspection_checks = inspection_proofs(root)

    # Reuse the owning exact 330-case checker, including its native marker,
    # original-source, target and omission-control validation.
    verification = output.with_suffix('.checks')
    verification.mkdir(parents=True, exist_ok=False)
    cumulative = verification / 'cumulative-b8-3-coverage.json'
    argv = [sys.executable, str(ROOT / 'tests/envoy-private-failure/coverage.py')]
    for release in sorted(RELEASES):
        argv += ['--private-results', str(root / ('private-failure-' + release) / 'results.json')]
    argv += ['--controls-results', str(root / 'private-failure-controls/results.json'),
             '--captured-results', str(root / 'captured-closure/results.json'),
             '--go-log', str(root / 'go-race.log'), '--output', str(cumulative)]
    subprocess.run(argv, cwd=ROOT, check=True)
    checked = read_json(cumulative)
    require(checked['status'] == 'passed' and checked['cumulative_cases'] == 330 and
            set(checked['qualified_targets']) == RELEASES, 'incomplete B8.3 closure')
    for name, digest in checked['evidence'].items():
        require(sha(Path(name)) == digest, 'changed cumulative B8.3 evidence')

    events = [json.loads(line) for line in (root / 'go-race.log').read_text().splitlines()]
    require(not any(row.get('Action') == 'fail' for row in events), 'Go race/conformance failure')
    passed = {row['Test'] for row in events if row.get('Action') == 'pass' and 'Test' in row}
    # The standalone renamed fixtures are validated above, including their
    # exact executable names/selection and all seven causal controls.
    passed.add('TestWorkerFixture')
    for row in read_json(root / 'inspection-proofs/results.json'):
        if not row['missing_check_control']:
            passed.update(re.findall(r'--- PASS: (Test[A-Za-z0-9_]+)[ /]', Path(row['log']).read_text()))
    inventory = corpus()
    for case in inventory.values():
        require(case['static_test'] in passed, 'missing static case check: ' + case['id'])
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name.endswith('controls') or folder.name.endswith('control'):
            continue
        for log in folder.glob('*/native.log'):
            passed.update(re.findall(r'--- PASS: (Test[A-Za-z0-9_]+)[ /]', log.read_text()))
    xml = ET.parse(root / 'python-junit.xml')
    compiler, python_passed = set(), set()
    for case in xml.iter('testcase'):
        successful = all(case.find(name) is None for name in ('skipped', 'failure', 'error'))
        if successful:
            python_passed.add(case.get('name', '').split('[')[0])
        if case.get('classname', '').endswith('test_recording_plan_interactive_output'):
            require(successful, 'compiler eligibility check skipped or failed')
            compiler.add(case.get('name', '').split('[')[0])
    for group in read_json(ROOT / MAPPING)['groups']:
        observed = (compiler if group['kind'] == 'compiler' else
                    python_passed if group['path'].endswith('.py') else passed)
        for test in group['tests']:
            require(test.split('/')[0] in observed, 'owning oracle did not execute: ' + test)
    for release in RELEASES:
        cases = read_json(qualification / release / 'receipt.json')['result']['cases']
        for identity in read_json(ROOT / 'tests/envoy-bash-qualification/case-map.json'):
            require(cases.get(identity) is True, 'missing qualified adapter case: ' + identity)

    # Retain the distinction between direct native scenarios and composed
    # boundary proofs. No row claims that its synthetic wire fixture was
    # literally produced by a genuine Bash entry with different measured data.
    rows = []
    for case in catalog()['cases']:
        leaf = case['implementation_leaf']
        roles = {proof['kind'] for proof in case['locators']}
        require(case['locators'], 'case lacks its owning proof oracle: ' + case['id'])
        if leaf.startswith('B1.'):
            method, crossings = 'static codec/corpus', ()
        elif leaf == 'B6.6':
            method, crossings = 'actual compiler before actor launch', ()
        else:
            require(leaf in INTEGRATION, 'missing actual actor closure: ' + leaf)
            crossings = INTEGRATION[leaf]
            method = ('native mapped scenarios' if roles == {'native'} else
                      'owning oracle and actual actor boundary')
            for name in crossings:
                require(name == 'private-failure' or name in commands, 'missing actual crossing: ' + name)
        rows.append({'id': case['id'], 'implementation_leaf': leaf, 'status': 'passed',
                     'proof_method': method, 'owning_proofs': case['locators'],
                     'actual_actor_suites': list(crossings),
                     'owning_test_execution': owning_oracles(case, passed | python_passed)})
    require({row['id'] for row in rows} == set(inventory), 'incomplete final B case partition')
    artifacts = {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*'))
                 if p.is_file() and not p.is_symlink()}
    result = {'version': 1, 'status': 'passed', 'owning_leaf': 'B8.4', 'cases': rows,
              'sources': sources, 'qualified_targets': checked['qualified_targets'],
              'tools': receipt['tools'],
              'cumulative_B8_3_evidence': {'path': str(cumulative), 'sha256': sha(cumulative)},
              'execution': str(path), 'execution_sha256': sha(path), 'artifacts': artifacts,
              'assembled_native_scenarios_per_target': len(assembly_cases()),
              'cumulative_B8_3_cases': 330, 'inspection_checks': inspection_checks,
              'actor_scope': 'Actual Envoy session/relay/coordinator modules in isolated test assembly, manifested Awsh and genuinely qualified Bash. Production entry/host packaging retain later owners.',
              'proof_scope': 'Case-specific component/qualified oracles remain explicit. Every runtime row also requires its actual actor crossing suites; no peer-only evidence closes the final gate.',
              'timing': {'started_unix': receipt['started_unix'], 'completed_unix': receipt['completed_unix'],
                         'note': 'Concurrent command intervals overlap. Exclusive CPU/edit/service waits are unavailable.'}}
    require(source_binding() == sources, 'candidate changed during final verification')
    output.write_text(json.dumps(result, indent=2) + '\n')
    return result


def corpus(root=ROOT):
    result, seen = {}, set()
    for line in (root / CORPUS).read_text().splitlines():
        case = json.loads(line, object_pairs_hook=unique_object)
        identity = case['id']
        require(identity not in seen, 'duplicate corpus case: ' + identity)
        seen.add(identity)
        if case['implementation_leaf'].startswith('B'):
            result[identity] = case
    require(result, 'empty B conformance inventory')
    return result


def catalog(root=ROOT):
    """Locate proof obligations. A locator never declares an executed pass."""
    inventory = corpus(root)
    config = read_json(root / MAPPING)
    require(set(config) == {'version', 'imports', 'groups'}, 'invalid final case map')
    require(config['version'] == 1, 'unknown final case-map version')
    locators = {identity: [] for identity in inventory}
    for identity, case in inventory.items():
        if case['implementation_leaf'].startswith('B1.'):
            locators[identity].append({'kind': 'static', 'package': 'protocol',
                                       'test': case['static_test'], 'fixture': case['fixture']})
    for source in config['imports']:
        require(set(source) == {'path', 'kind', 'format'}, 'invalid imported case map')
        relative = Path(source['path'])
        require(not relative.is_absolute() and '..' not in relative.parts, 'unsafe case-map path')
        require(source['kind'] in {'native', 'component', 'qualified-component'}, 'unknown proof boundary')
        mapping = read_json(root / relative)
        if source['format'] == 'flat':
            cases = mapping
        elif source['format'] == 'cases':
            cases = mapping['cases']
        elif source['format'] == 'obligations':
            cases = mapping['corpus_obligations']
            if isinstance(cases, list):
                require(len(set(cases)) == len(cases), 'duplicate imported obligation')
                cases = {identity: mapping.get('runtime_tests') or mapping.get('native')
                         for identity in cases}
        elif source['format'] == 'foundation':
            rows = mapping['cases']
            require(len({row['id'] for row in rows}) == len(rows), 'duplicate foundation obligation')
            cases = {row['id']: row['tests'] for row in rows}
        elif source['format'] == 'indexed':
            rows = mapping['cases']
            require(len({row['inventory_id'] for row in rows}) == len(rows), 'duplicate indexed obligation')
            cases = {row['inventory_id']: row for row in rows}
        elif source['format'] == 'boundaries':
            cases = {key: value for key, value in mapping['boundary_evidence'].items()
                     if key.startswith('B1-')}
        else:
            raise ValueError('unknown case-map format')
        require(isinstance(cases, dict), 'case mapping must be an object')
        for identity, value in cases.items():
            require(identity in inventory, 'unknown or non-B imported case: ' + identity)
            locators[identity].append({'kind': source['kind'], 'path': str(relative),
                                       'map_sha256': sha(root / relative), 'value': value})
    require(isinstance(config['groups'], list), 'invalid explicit proof groups')
    assigned = set()
    for group in config['groups']:
        require(set(group) == {'kind', 'path', 'tests', 'reason', 'ids'} and
                group['kind'] in {'component', 'native', 'compiler'} and
                isinstance(group['tests'], list) and group['tests'] and
                all(isinstance(test, str) and test for test in group['tests']) and
                isinstance(group['reason'], str) and group['reason'].strip(), 'invalid explicit proof boundary')
        path = Path(group['path'])
        require(not path.is_absolute() and '..' not in path.parts and (root / path).is_file(),
                'unknown explicit proof source')
        text = (root / path).read_text()
        for test in group['tests']:
            name = test.split('/')[0]
            require(('func ' + name + '(' in text or 'def ' + name + '(' in text), 'unknown proof function: ' + test)
        require(isinstance(group['ids'], list) and group['ids'], 'empty explicit proof group')
        for identity in group['ids']:
            key = (identity, group['kind'], str(path))
            require(identity in inventory and key not in assigned, 'unknown or duplicate explicit proof: ' + identity)
            assigned.add(key)
            locators[identity].append({key: value for key, value in group.items() if key != 'ids'})
    rows = []
    for identity, case in sorted(inventory.items()):
        rows.append({'id': identity, 'implementation_leaf': case['implementation_leaf'],
                     'closure_leaf': case['closure_leaf'], 'static_test': case['static_test'],
                     'locators': locators[identity], 'execution_verified': False})
    for identity, case in inventory.items():
        require_executable_owner({'id': identity, 'implementation_leaf': case['implementation_leaf'],
                                  'locators': locators[identity]})
    return {'status': 'unverified', 'corpus_sha256': sha(root / CORPUS),
            'case_map_sha256': sha(root / MAPPING), 'cases': rows,
            'without_locator': [row['id'] for row in rows if not row['locators']],
            'note': 'Proof locations only. No current command, actor or qualification pass is asserted.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--audit', action='store_true',
                        help='emit obligations only; does not close final acceptance')
    mode.add_argument('--native-assembly', action='store_true',
                      help='run all assembly cases on all genuine qualified targets')
    mode.add_argument('--run', action='store_true', help='execute the fixed full B suite; case closure remains separate')
    mode.add_argument('--verify', type=Path, metavar='EXECUTION_JSON', help='verify full current evidence and assemble final case closure')
    parser.add_argument('--qualification', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.verify:
        result = verify_final(args.verify, args.output)
        print(f"Final B conformance verified: {len(result['cases'])} cases, three genuine amd64 targets.")
        return
    if args.run:
        require(args.qualification is not None, 'genuine qualification evidence required')
        execute_full(args.qualification.resolve(), args.output.resolve())
        print('Full B execution passed. Per-case closure still requires verification.')
        return
    if args.native_assembly:
        require(args.qualification is not None, 'genuine qualification evidence required')
        native_assembly(args.qualification.resolve(), args.output.resolve())
        print(f'Actual assembly matrix passed: {len(assembly_cases())} cases on each of three genuine amd64 targets.')
        return
    result = catalog()
    require(not args.output.exists(), 'preserve an existing audit unchanged')
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(f"Unverified B case inventory: {len(result['cases'])}; "
          f"without a locator: {len(result['without_locator'])}")


if __name__ == '__main__':
    main()
