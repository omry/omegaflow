"""Native proof scratch must not become retained review evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

PROOFS = Path(__file__).parent / 'envoy-session-startup'
RUNNERS = ('private', 'public', 'ordinary', 'resize', 'lifecycle', 'gate')


def load(name):
    spec = importlib.util.spec_from_file_location('proof_' + name, PROOFS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def proof_tools(tmp_path, monkeypatch):
    staging = load('staging')
    monkeypatch.setitem(sys.modules, 'staging', staging)
    monkeypatch.setitem(sys.modules, 'private', load('private'))
    state = SimpleNamespace(failure=None, builds=[], images={}, staged={})

    def rows(qualification, awsh):
        if state.failure == 'qualification':
            raise ValueError('qualification rejected')
        for release in ('5.2.15', '5.2.37', '5.3.9'):
            entry = {'candidate': {'executable_sha256': 'qualified-bash'},
                     'qualification': {'adapter_inputs': {'/omegaflow-runtime/bin/awsh': awsh}}}
            yield {'release': release, 'receipt_sha256': 'receipt'}, entry, 'qualified-image'

    monkeypatch.setattr(staging, 'verified_rows', rows)
    # Reload private so its imported qualifier is also the test qualifier.
    monkeypatch.setitem(sys.modules, 'private', load('private'))
    monkeypatch.setattr(staging.subprocess, 'check_output', lambda *a, **k: 'test-identity\n')

    def run(argv, **kwargs):
        if argv[0] == 'go':
            binary = Path(argv[argv.index('-o') + 1])
            payload = b'isolated compiled peer /evidence/worker-entered'
            if any(arg.startswith('-coverpkg=') for arg in argv):
                payload += b' coverage'
                phase = 'coverage-build'
            elif any(arg.startswith('-overlay=') for arg in argv):
                phase = binary.parent.parent.name + '-build'
                payload += phase.encode()
            else:
                phase = 'go-' + argv[1]
            binary.write_bytes(payload)
            state.builds.append(binary)
            if state.failure == phase:
                raise subprocess.CalledProcessError(1, argv)
        elif argv[:2] == ['docker', 'build']:
            context = Path(argv[-1])
            recipe = (context / 'Dockerfile').read_text()
            if 'COPY native.test ' in recipe:
                payload = (context / 'native.test').read_bytes()
                state.staged[context] = payload
                assert json.loads((context / 'test-binary.json').read_text()) == {
                    'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)}
            kwargs['stdout'].write('retained build log\n')
            if state.failure == 'docker-build':
                raise subprocess.CalledProcessError(1, argv)
            iid = 'image-' + str(len(state.images))
            state.images[iid] = context
            Path(argv[argv.index('--iidfile') + 1]).write_text(iid)
        elif argv[:2] == ['docker', 'run']:
            # Build-context copies must already be gone before any test runs.
            assert all(not (p / 'native.test').exists() for p in state.staged)
            kwargs['stdout'].write('retained native log\n')
            if state.failure == 'test-timeout':
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
            if state.failure == 'test-failure':
                return subprocess.CompletedProcess(argv, 1)
            if '-v' in argv:
                evidence = Path(argv[argv.index('-v') + 1].removesuffix(':/evidence'))
                (evidence / 'scenario.cover').write_text('mode: set\nfixture.go:1.1,2.1 1 1\n')
                (evidence / 'deferred-observed').write_bytes(b'\x01')
                if 'schedule-drop' in evidence.parts:
                    kwargs['stdout'].write('private result: i/o timeout\n')
                    return subprocess.CompletedProcess(argv, 1)
        else:
            pytest.fail('unexpected external command: ' + repr(argv))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(staging.subprocess, 'run', run)
    return staging, state


def assert_clean(output, state):
    assert not list(output.rglob('*.test'))
    assert not list(output.rglob('awsh-current'))
    assert not list(output.rglob('.native-build-*'))
    assert all(not path.exists() for path in state.builds)
    for context, payload in state.staged.items():
        assert (context / 'Dockerfile').is_file()
        assert (context / 'build.log').read_text() == 'retained build log\n'
        assert json.loads((context / 'test-binary.json').read_text())['sha256'] == hashlib.sha256(payload).hexdigest()


def invoke(runner, output, monkeypatch):
    monkeypatch.setattr(sys, 'argv', [runner.__name__, '--qualification', 'qualification',
                                     '--output', str(output)])
    runner.main()


@pytest.mark.parametrize('name', RUNNERS)
@pytest.mark.parametrize('failure', [None, 'go-test', 'go-build', 'qualification',
                                     'docker-build', 'test-failure', 'test-timeout'])
def test_proof_runner_cleans_executables_and_keeps_evidence(
        tmp_path, monkeypatch, proof_tools, name, failure):
    staging, state = proof_tools
    state.failure = failure
    output = tmp_path / 'evidence'
    runner = load(name)
    if failure is None:
        invoke(runner, output, monkeypatch)
        results = json.loads((output / 'results.json').read_text())
        assert len(results) == 3
        assert all(row['returncode'] == 0 for row in results)
        assert all(row['log_sha256'] == staging.sha(output / row['release'] / 'native.log')
                   for row in results)
        # One compilation serves all three Bash versions.
        assert len(state.builds) == 2  # peer and Awsh comparison binary
        assert len(set(state.staged.values())) == 1
    else:
        with pytest.raises((ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired)):
            invoke(runner, output, monkeypatch)
        if failure == 'test-failure':
            assert json.loads((output / 'results.json').read_text())[0]['returncode'] == 1
        if failure == 'test-timeout':
            assert (output / '5.2.15/native.log').read_text() == 'retained native log\n'
    assert_clean(output, state)


@pytest.mark.parametrize('failure', [None, 'go-test', 'go-build', 'qualification',
                                     'coverage-build', 'schedule-retain-build',
                                     'schedule-drop-build', 'docker-build',
                                     'test-failure', 'test-timeout'])
def test_coverage_and_overlay_executables_are_scoped(
        tmp_path, monkeypatch, proof_tools, failure):
    staging, state = proof_tools
    state.failure = failure
    runner = load('coverage')
    root = tmp_path / 'source'
    source = root / 'runtime/envoy/awsh/supervise'
    source.mkdir(parents=True)
    for name in ('serve_linux.go', 'operation_linux_test.go'):
        (source / name).write_text('unchanged source\n')
    monkeypatch.setattr(runner, 'ROOT', root)

    def overlay(output, drop):
        path = output / 'overlay.json'
        staging.save(path, {'fixture': 'drop' if drop else 'retain'})
        return path

    monkeypatch.setattr(runner, 'scheduling_overlay', overlay)
    output = tmp_path / 'evidence'
    if failure is None:
        invoke(runner, output, monkeypatch)
        results = json.loads((output / 'results.json').read_text())
        assert len(results['coverage']) == 3
        assert len(results['schedule']) == 6
        assert all(row['expected_result'] for row in results['schedule'])
        assert (output / 'components.cover').read_text() == 'mode: set\nfixture.go:1.1,2.1 1 1\n'
        assert len({p for p in state.builds if p.name == 'native.test'}) == 3
        assert len(set(state.staged.values())) == 3  # coverage and two distinct overlays
        for name in ('schedule-retain', 'schedule-drop'):
            assert (output / name / 'overlay.json').is_file()
            assert (output / name / '5.3.9/evidence/deferred-observed').read_bytes() == b'\x01'
    else:
        with pytest.raises((ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired)):
            invoke(runner, output, monkeypatch)
        if failure not in ('go-test', 'go-build', 'qualification'):
            results = json.loads((output / 'results.json').read_text())
            assert results['source_before'] == results['source_after']
    assert_clean(output, state)


def test_partial_staging_copy_is_removed_without_touching_input(
        tmp_path, monkeypatch, proof_tools):
    staging, state = proof_tools
    binary = tmp_path / 'input.test'
    binary.write_bytes(b'original binary')
    output = tmp_path / 'evidence'
    output.mkdir()

    def partial_copy(source, destination):
        destination.write_bytes(b'partial')
        raise OSError('copy failed')

    monkeypatch.setattr(staging.shutil, 'copyfile', partial_copy)
    with pytest.raises(OSError, match='copy failed'):
        staging.stage(output, '5.2.15', {}, 'image', binary)
    assert binary.read_bytes() == b'original binary'
    assert not list(output.rglob('*.test'))
    assert (output / '5.2.15/test-binary.json').is_file()
