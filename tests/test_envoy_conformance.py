"""Reject false completeness in the final case/evidence assembly."""
import importlib.util
from copy import deepcopy
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('envoy_conformance', ROOT / 'tests/envoy-conformance/run.py')
conformance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(conformance)


def test_inventory_is_not_an_execution_pass():
    audit = conformance.catalog()
    expected = conformance.corpus()
    assert {row['id'] for row in audit['cases']} == set(expected)
    assert audit['status'] == 'unverified'
    assert not any(row['execution_verified'] for row in audit['cases'])
    assert audit['without_locator'] == []
    assert all(case['implementation_leaf'].startswith('B') for case in expected.values())


def test_qualified_component_keeps_its_boundary():
    audit = conformance.catalog()
    row = next(row for row in audit['cases'] if row['id'] == 'B1-C020-CHLD-direct')
    assert {proof['kind'] for proof in row['locators']} == {'qualified-component'}
    assert row['execution_verified'] is False


def test_full_selection_is_complete_and_fixed(tmp_path):
    commands = conformance.full_commands(tmp_path / 'qualification', tmp_path / 'output', 'python3')
    names = [name for name, _ in commands]
    assert len(names) == len(set(names)) == 29
    assert {'go-race', 'python', 'private-failure-controls', 'captured-closure', 'assembly'} <= set(names)
    private = {name: argv for name, argv in commands if '--release' in argv}
    assert set(private) == {'private-failure-' + release for release in conformance.RELEASES}
    for release in conformance.RELEASES:
        argv = private['private-failure-' + release]
        assert argv[argv.index('--release') + 1] == release
        assert '--native-only' in argv
    for name, argv in commands:
        assert not {'--case', '--faults-only', '--phase', '--variant', '--crossed-only', '--extra-only'} & set(argv)
        if name not in private:
            assert '--release' not in argv and '--native-only' not in argv
        assert ('--proof-only' in argv) == (name == 'private-failure-controls')
        assert ('--regressions-only' in argv) == (name in {
            'private-startup-regressions', 'private-startup-cancellation-control'})
        if name not in {'go-race', 'python', 'inspection-proofs', 'inspection-worker-proofs'}:
            assert '--qualification' in argv
            assert (ROOT / argv[1]).is_file()


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / 'evidence.json'
    path.write_text('{"status":"failed","status":"passed"}')
    with pytest.raises(ValueError, match='duplicate JSON key'):
        conformance.read_json(path)


def test_duplicate_case_ids_are_rejected(tmp_path):
    folder = tmp_path / conformance.CORPUS.parent
    folder.mkdir(parents=True)
    row = json.dumps({'id': 'same', 'implementation_leaf': 'B8.4'})
    (tmp_path / conformance.CORPUS).write_text(row + '\n' + row + '\n')
    with pytest.raises(ValueError, match='duplicate corpus case'):
        conformance.corpus(tmp_path)


def test_symlink_evidence_is_rejected(tmp_path):
    actual = tmp_path / 'actual.json'
    actual.write_text('{}')
    link = tmp_path / 'link.json'
    link.symlink_to(actual)
    with pytest.raises(ValueError, match='regular evidence file'):
        conformance.read_json(link)


@pytest.fixture
def complete_assembly(tmp_path, monkeypatch):
    """Synthetic checker input only. No actor/qualification acceptance claim."""
    expected = conformance.assembly_cases()
    targets = {release: {'test_image': 'sha256:' + release, 'receipt_sha256': 'receipt-' + release}
               for release in conformance.RELEASES}
    monkeypatch.setattr(conformance, 'source_binding', lambda: {'one.go': 'current'})
    monkeypatch.setattr(conformance, 'qualification_bindings', lambda *args: targets)
    rows = []
    for release, target in targets.items():
        path = tmp_path / (release + '.log')
        path.write_text('\n'.join('NATIVE CONFORMANCE ' + name + ' PROVED' for name in expected) + '\nPASS\n')
        rows.append({'release': release, 'returncode': 0, 'cases': expected[:],
                     'qualified_base_image': target['test_image'],
                     'qualification_receipt_sha256': target['receipt_sha256'],
                     'qualified_awsh_sha256': 'a' * 64, 'test_binary_sha256': 'b' * 64,
                     'log': str(path), 'log_sha256': conformance.sha(path)})
    return {'version': 1, 'kind': 'actual-actor-assembly', 'status': 'passed',
            'sources': {'one.go': 'current'}, 'qualification': str(tmp_path),
            'awsh_sha256': 'a' * 64, 'rows': rows}


def test_checker_requires_exact_native_selection(complete_assembly):
    conformance.check_assembly(complete_assembly)
    partial = deepcopy(complete_assembly)
    partial['rows'][0]['cases'].pop()
    with pytest.raises(ValueError, match='partial or failed assembly'):
        conformance.check_assembly(partial)


@pytest.mark.parametrize('change, diagnostic', [
    ('status', 'incomplete assembly'),
    ('sources', 'source or mode drift'),
    ('target', 'qualification target drift'),
    ('release', 'every genuine target exactly once'),
    ('awsh', 'mixed qualified Awsh'),
    ('returncode', 'partial or failed assembly'),
])
def test_checker_rejects_stale_or_wrong_proof(complete_assembly, change, diagnostic):
    proof = deepcopy(complete_assembly)
    if change == 'status':
        proof['status'] = 'incomplete'
    elif change == 'sources':
        proof['sources'] = {'one.go': 'stale'}
    elif change == 'target':
        proof['rows'][0]['qualified_base_image'] = 'different-image'
    elif change == 'release':
        proof['rows'][0]['release'] = proof['rows'][1]['release']
    elif change == 'awsh':
        proof['rows'][0]['qualified_awsh_sha256'] = 'different-awsh'
    else:
        proof['rows'][0]['returncode'] = 1
    with pytest.raises(ValueError, match=diagnostic):
        conformance.check_assembly(proof)


@pytest.mark.parametrize('corruption', ['omit', 'duplicate', 'skip', 'failure'])
def test_native_markers_cannot_be_omitted_or_relabelled(complete_assembly, corruption):
    proof = deepcopy(complete_assembly)
    row = proof['rows'][0]
    path = Path(row['log'])
    text = path.read_text()
    first = text.splitlines()[0] + '\n'
    if corruption == 'omit':
        text = text.replace(first, '')
    elif corruption == 'duplicate':
        text += first
    elif corruption == 'skip':
        text += '--- SKIP: missing (0s)\n'
    else:
        text += '--- FAIL: broken (0s)\n'
    path.write_text(text)
    row['log_sha256'] = conformance.sha(path)
    with pytest.raises(ValueError, match='missing actual assembly proof'):
        conformance.check_assembly(proof)


@pytest.fixture
def command_receipt(tmp_path):
    """Minimal command-checker fixture, without actor acceptance evidence."""
    log = tmp_path / 'one.log'
    log.write_text('actual command transcript\n')
    expected = [('one', ['tool', 'fixed-selection'])]
    row = {'name': 'one', 'argv': expected[0][1][:], 'cwd': str(ROOT), 'returncode': 0,
           'failure': None, 'log': str(log), 'log_sha256': conformance.sha(log)}
    return {'commands': [row]}, expected


def test_full_command_evidence_requires_exact_plan(command_receipt):
    receipt, expected = command_receipt
    assert set(conformance.command_index(receipt, expected)) == {'one'}
    receipt['commands'][0]['argv'].append('--partial')
    with pytest.raises(ValueError, match='changed or failed full command'):
        conformance.command_index(receipt, expected)


@pytest.mark.parametrize('corruption', ['omit', 'duplicate', 'failed', 'typed-false', 'transcript'])
def test_full_command_evidence_rejects_incomplete_or_stale(command_receipt, corruption):
    receipt, expected = command_receipt
    if corruption == 'omit':
        receipt['commands'] = []
    elif corruption == 'duplicate':
        receipt['commands'] *= 2
    elif corruption == 'failed':
        receipt['commands'][0]['returncode'] = 1
    elif corruption == 'typed-false':
        receipt['commands'][0]['returncode'] = False
    else:
        Path(receipt['commands'][0]['log']).write_text('changed\n')
    with pytest.raises(ValueError):
        conformance.command_index(receipt, expected)


@pytest.fixture
def bound_execution_artifacts(tmp_path):
    """Synthetic execution side-artifacts for continuity checks only."""
    folder = tmp_path / 'terminal-closure'
    folder.mkdir()
    (folder / 'native.log').write_text(
        'NATIVE TERMINAL matrix-closed-missing-reap PROVED\nPASS\n')
    (folder / 'results.json').write_text('{"status":"passed"}\n')
    for index in range(6):
        (folder / ('control-' + str(index) + '.json')).write_text(
            '{"control":' + str(index) + '}\n')
    nested = tmp_path / 'nested'
    nested.mkdir()
    (nested / 'execution.json').write_text('{"nested":true}\n')
    (tmp_path / 'execution.json').write_text('{"status":"passed"}\n')
    return tmp_path, conformance.artifact_inventory(tmp_path)


def test_bound_execution_artifacts_accept_unchanged_evidence(bound_execution_artifacts):
    root, expected = bound_execution_artifacts
    assert conformance.check_artifact_inventory(root, expected) == expected


@pytest.mark.parametrize('mutation', ['changed-marker', 'changed-receipt',
                                      'removed-control', 'extra-artifact'])
def test_bound_execution_artifacts_reject_post_execution_mutation(bound_execution_artifacts, mutation):
    root, expected = bound_execution_artifacts
    if mutation == 'changed-marker':
        path = root / 'terminal-closure/native.log'
        path.write_text(path.read_text().replace(
            'NATIVE TERMINAL matrix-closed-missing-reap PROVED',
            'NATIVE TERMINAL matrix-closed-missing-reap OMITTED'))
    elif mutation == 'changed-receipt':
        (root / 'terminal-closure/results.json').write_text('{"status":"incomplete"}\n')
    elif mutation == 'removed-control':
        (root / 'terminal-closure/control-5.json').unlink()
    else:
        (root / 'terminal-closure/unexpected.json').write_text('{"extra":true}\n')
    with pytest.raises(ValueError, match='produced evidence artifact inventory changed'):
        conformance.check_artifact_inventory(root, expected)


def test_runtime_closure_cannot_end_at_a_peer_only_suite():
    leaves = {case['implementation_leaf'] for case in conformance.corpus().values()
              if not case['implementation_leaf'].startswith('B1.') and case['implementation_leaf'] != 'B6.6'}
    assert leaves == set(conformance.INTEGRATION)
    actual = {'public-startup', 'return-cleanup', 'process-lifecycle', 'evidence-ranges',
              'inspection-lifecycle', 'failure-isolation', 'terminal-closure',
              'captured-closure', 'private-failure', 'assembly'}
    for suites in conformance.INTEGRATION.values():
        assert suites and set(suites) <= actual


@pytest.fixture
def inspection_evidence(tmp_path):
    """Synthetic checker evidence; never actual inspection acceptance."""
    folder = tmp_path / 'inspection-proofs'
    folder.mkdir()
    rows = []
    for i, (mode, negative, count) in enumerate(conformance.INSPECTION_VARIANTS):
        case = folder / str(i)
        case.mkdir()
        overlay = case / 'overlay.json'
        overlay.write_text('{}')
        log = case / 'test.log'
        if negative:
            diagnostic = ('digest ' if mode == 'framing' else
                          'want inspection-read' if mode in {'readlink-error', 'open-error', 'open-entry-error'}
                          else 'want inspection-unstable')
            text = diagnostic + '\n--- FAIL: TestSynthetic (0s)\n'
        else:
            marker = ('OBSERVED READLINK FAILURE ' if mode == 'readlink-error' else
                      'OBSERVED OPEN FAILURE ' if mode in {'open-error', 'open-entry-error'} else
                      'OBSERVED INSPECTION MUTATION ')
            text = marker * count + '\nPASS\n'
        log.write_text(text)
        rows.append({'mode': mode, 'missing_check_control': negative, 'expected_mutations': count,
                     'proved': True, 'returncode': int(negative), 'log': str(log),
                     'log_sha256': conformance.sha(log), 'overlay_sha256': conformance.sha(overlay)})
    (folder / 'results.json').write_text(json.dumps(rows))
    worker_folder = tmp_path / 'inspection-worker-proofs'
    worker_folder.mkdir()
    names = ['build-baseline', 'normal'] + ['fixture-' + n for n in conformance.WORKER_FIXTURES]
    names += [prefix + n for n in conformance.WORKER_CONTROLS for prefix in ('build-control-', 'control-')]
    checks = []
    for name in names:
        negative = name.startswith('control-')
        diagnostic = (dict(zip(conformance.WORKER_CONTROLS, conformance.WORKER_DIAGNOSTICS))[
            name.removeprefix('control-')] if negative else None)
        log = worker_folder / (name + '.log')
        log.write_text(diagnostic if negative else 'PASS\n')
        checks.append({'name': name, 'passed': True, 'timed_out': False,
                       'returncode': int(negative), 'expected_failure': diagnostic,
                       'argv': [str(worker_folder / ('b7-worker-' + name.removeprefix('fixture-'))),
                                '-test.run=^TestWorkerFixture$', '-test.timeout=20s'],
                       'log': str(log), 'log_sha256': conformance.sha(log)})
    paths = [ROOT / 'runtime/envoy/inspection' / n for n in
             ('worker_linux.go', 'worker_codec_linux.go',
              'worker_process_linux_test.go', 'worker_codec_linux_test.go')]
    worker = {'status': 'passed', 'source_unchanged': True,
              'actual_fault_fixtures': list(conformance.WORKER_FIXTURES),
              'negative_controls': list(conformance.WORKER_CONTROLS),
              'sources': {str(p): conformance.sha(p) for p in paths}, 'checks': checks}
    (worker_folder / 'results.json').write_text(json.dumps(worker))
    return tmp_path


def test_explicit_inspection_fault_proofs_are_required(inspection_evidence):
    assert conformance.inspection_proofs(inspection_evidence) == {
        'inspection_fault_rows': 13, 'inspection_worker_checks': 23}


@pytest.mark.parametrize('corruption', ['omit', 'diagnostic', 'worker-omit', 'worker-source', 'worker-timeout'])
def test_inspection_fault_proofs_reject_false_pass(inspection_evidence, corruption):
    root = inspection_evidence
    if corruption in {'omit', 'diagnostic'}:
        path = root / 'inspection-proofs/results.json'
        value = json.loads(path.read_text())
        if corruption == 'omit':
            value.pop()
        else:
            row = next(r for r in value if r['missing_check_control'])
            log = Path(row['log'])
            log.write_text('unrelated failure\n')
            row['log_sha256'] = conformance.sha(log)
    else:
        path = root / 'inspection-worker-proofs/results.json'
        value = json.loads(path.read_text())
        if corruption == 'worker-omit':
            value['checks'].pop()
        elif corruption == 'worker-source':
            value['sources'][next(iter(value['sources']))] = '0' * 64
        else:
            value['checks'][0]['timed_out'] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        conformance.inspection_proofs(root)


def test_missing_named_oracle_requires_explicit_actual_replacement():
    case = {'locators': [{'kind': 'component', 'value': 'TestOriginal/not-executed'}]}
    with pytest.raises(ValueError, match='owning oracle did not execute'):
        conformance.owning_oracles(case, {'TestUnrelated'})
    case['locators'].append({'kind': 'native', 'tests': ['TestActual/replacement']})
    result = conformance.owning_oracles(case, {'TestActual'})
    assert result['historical_unexecuted_test_roots'] == ['TestOriginal']
    assert result['actual_replacement_proofs'] == [case['locators'][1]]
    assert conformance.owning_oracles(case, {'TestOriginal', 'TestActual'})['historical_unexecuted_test_roots'] == []


EXECUTABLE_CASES = {
    'B1-C003-blocked-worker-cancel-timeout': 'TestInspectionBlockedReapIsFatalWithinOriginalFiveSeconds',
    'B1-C003-worker-cancel-first': 'TestInspectionCancelAcceptedFirstDiscardsResultAndJoinsReap',
    'B1-C003-worker-result-first': 'TestInspectionJoinedResultCommitsWhileTelemetryBlocked',
    'B1-C062-exclusive-checked': 'TestNativeEvidenceRanges',
    'B1-C062-exclusive-suppress': 'TestNativeEvidenceRanges',
    'B1-C062-exclusive-replace': 'TestNativeEvidenceRanges',
    'B1-C062-planned-end-status': 'TestNativeEvidenceRanges',
    'B1-C062-finalization-failure-invalid-range': 'TestNativeEvidenceRanges',
    'B1-C062-user-cancel-invalid-range': 'TestNativeEvidenceRanges',
    'B1-C062-finalize-after-return': 'TestNativeEvidenceRanges',
}


def _proof_test_roots(proof):
    names = proof.get('tests')
    if names is None:
        import re
        names = re.findall(r'Test[A-Za-z0-9_]+', json.dumps(proof.get('value')))
    return {name.split('/')[0] for name in names}


def test_affected_runtime_cases_have_concrete_executable_owners():
    catalog = {row['id']: row for row in conformance.catalog()['cases']}
    for identity, expected in EXECUTABLE_CASES.items():
        row = catalog[identity]
        assert any(expected in _proof_test_roots(proof) for proof in row['locators'])
        result = conformance.owning_oracles(row, {expected})
        assert expected in result['passed_test_roots']


@pytest.mark.parametrize('identity, expected', sorted(EXECUTABLE_CASES.items()))
def test_omitting_affected_executable_owner_prevents_closure(identity, expected):
    row = next(row for row in conformance.catalog()['cases'] if row['id'] == identity)
    assert any(expected in _proof_test_roots(proof) for proof in row['locators'])
    with pytest.raises(ValueError, match='owning oracle did not execute'):
        conformance.owning_oracles(row, set())


@pytest.mark.parametrize('value', [None, 'descriptive component evidence only'])
def test_null_or_prose_only_nonlisted_component_locator_is_not_executable(value):
    case = {'id': 'B1-C999-unlisted', 'implementation_leaf': 'B9.9',
            'locators': [{'kind': 'component', 'value': value}]}
    with pytest.raises(ValueError, match='concrete executable owning oracle'):
        conformance.owning_oracles(case, {'TestUnrelated'})


def test_imported_owner_omission_rejects_catalog(monkeypatch):
    target = ROOT / 'tests/envoy-split-streams/case-map.json'
    original = conformance.read_json

    def without_imported_test(path):
        value = original(path)
        if path == target:
            value = deepcopy(value)
            row = next(row for row in value['cases'] if row['inventory_id'] == 'B1-C005-pty-start')
            row.pop('test')
        return value

    monkeypatch.setattr(conformance, 'read_json', without_imported_test)
    with pytest.raises(ValueError, match='concrete executable owning oracle'):
        conformance.catalog()


def test_static_qualified_and_native_scenario_boundaries_remain_exempt():
    catalog = {row['id']: row for row in conformance.catalog()['cases']}
    for identity in ('B1-C001-contract', 'B1-C020-CHLD-direct', 'B1-C036-contract'):
        assert conformance.owning_oracles(catalog[identity], set()) == {
            'passed_test_roots': [], 'historical_unexecuted_test_roots': [],
            'actual_replacement_proofs': []}
