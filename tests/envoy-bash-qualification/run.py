#!/usr/bin/env python3
"""Measure B2 on retained native images; emit receipts, never edit admission data."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = {'helper': 'awsh/helper', 'protocol': 'protocol', 'bashbuild': 'bashbuild',
            'submission': 'awsh/submission', 'launch': 'awsh/launch'}


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def native(candidate_path):
    """Run the existing strict qualifier twice on the genuine container target."""
    tool = module(ROOT / 'tools/bash_builds.py', 'builds')
    retained = tool.read(candidate_path)
    release = platform.freedesktop_os_release()
    bash_path, bash_digest = tool.file_hash('/bin/bash')
    tool.require(bash_digest == retained['observed']['executable_sha256'], 'retained Bash mismatch')
    build = {'release': retained['release'], 'target': {
        'os': platform.system().lower(),
        'arch': {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine()),
        'os_release_id': release.get('ID'), 'version_id': release.get('VERSION_ID')},
        **{name: retained['provenance'][name] for name in
           ('artifact_sha256', 'definition_sha256', 'lock_sha256')},
        'executable_sha256': bash_digest, 'resolved_path': bash_path,
        'system_rc': '/etc/bash.bashrc'}
    inputs, assets = {}, {}
    for tree in ('/omegaflow-runtime', '/qualification'):
        for path in sorted(Path(tree).rglob('*')):
            if path.is_file():
                resolved, digest = tool.file_hash(path)
                inputs[resolved] = digest
                if tree == '/omegaflow-runtime':
                    assets[str(path)] = resolved
    for path in (bash_path, sys.executable):
        resolved, digest = tool.file_hash(path)
        inputs[resolved] = digest
    # The immutable reserved-state corpus is consumed at this fixed test path.
    resolved, digest = tool.file_hash('/proof/cases.json')
    inputs[resolved] = digest
    executable = str(Path(sys.executable).resolve())
    plan = {'inputs': inputs, 'assets': assets,
            'probe': [executable, str(Path(__file__).with_name('probe.py'))],
            'timeout_seconds': 300}
    save(Path('/evidence/candidate.json'), build)
    save(Path('/evidence/plan.json'), plan)
    receipt = tool.qualify(build, plan)
    save(Path('/evidence/receipt.json'), receipt)
    # Qualification is complete before exercising admission. The fixed runtime
    # table is an output; it was deliberately absent from the measured inputs.
    entry = {'candidate': build, 'qualification': receipt}
    table = {'version': 1, 'builds': {bash_digest: entry}}
    tool.validate_table(table)
    payload = Path('/omegaflow-runtime/etc/bash-builds.json')
    payload.write_bytes(tool.canonical(table) + b'\n')
    payload.chmod(0o444)
    namespace = {'__file__': str(ROOT / 'src/omegaflow/bash_builds.py')}
    host_source = Path('/qualification/host-template.py').read_text()
    marker = "_TABLE = '__QUALIFICATION_TABLE__'"
    tool.require(host_source.count(marker) == 1, 'invalid bound host template')
    host_source = host_source.replace(marker, '_TABLE = ' + repr(tool.canonical(table).decode()))
    exec(compile(host_source, namespace['__file__'], 'exec'), namespace)
    # The host consumer embeds output data. Only the Go runtime consumer needs
    # the separate payload's independently supplied manifest hash.
    current = receipt['adapter_inputs']
    tool.require(namespace['lookup']('/bin/bash', bash_digest, build['target'], current) == entry,
                 'generated host consumer lost qualified entry')
    for invalid in ({k: v for k, v in current.items() if k != '/omegaflow-runtime/bin/awsh'},
                    {**current, '/omegaflow-runtime/bin/awsh': '0' * 64}):
        try:
            namespace['lookup']('/bin/bash', bash_digest, build['target'], invalid)
        except ValueError:
            pass
        else:
            raise ValueError('generated host consumer accepted stale manifest inputs')
    probe = module(Path(__file__).with_name('probe.py'), 'admission_probe')
    directory = Path('/evidence/qualified-startup')
    directory.mkdir()
    signals = sorted(set(receipt['result']['measurements']['catchable_signals'].values()))
    observed = probe.startup(directory, signals, Path('/evidence/receipt.json'))
    for name in ('startup_pty_base64', 'startup_export'):
        tool.require(observed[name] == receipt['result']['measurements'][name],
                     'qualified launch differs from native measurement')
    tool.verify_inputs(plan['inputs'])
    print(json.dumps({'candidate': build, 'qualification': receipt}))


def stage(context):
    runtime = context / 'runtime'
    (runtime / 'bin').mkdir(parents=True)
    (runtime / 'etc').mkdir()
    proof = context / 'qualification'
    (proof / 'bin').mkdir(parents=True)
    repo = proof / 'repo'
    host = (ROOT / 'src/omegaflow/bash_builds.py').read_text()
    host, replacements = re.subn(r'^_TABLE = .+$', "_TABLE = '__QUALIFICATION_TABLE__'", host, flags=re.MULTILINE)
    if replacements != 1:
        raise ValueError('missing generated host table field')
    (proof / 'host-template.py').write_text(host)
    for relative in ('tools/bash_builds.py', 'tests/envoy-bash-qualification',
                     'tests/envoy-bash-launch', 'tests/envoy-bash-reserved-state',
                     'tests/envoy-bash-submission', 'tests/envoy-bash-start',
                     'tests/fixtures/envoy-protocol-v1', 'runtime/envoy', 'docs/design'):
        source, target = ROOT / relative, repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copyfile(source, target)
        if relative == 'runtime/envoy':
            # Table payloads are outputs even when copied under a test path.
            # Binding their previous data would recreate the circular input.
            for output in ('bash-builds.json', 'etc/bash-builds.json'):
                (target / output).unlink()
    for name in ('awsh-bashrc', 'inputrc'):
        shutil.copyfile(ROOT / 'runtime/envoy/etc' / name, runtime / 'etc' / name)
    shutil.copyfile(ROOT / 'tests/envoy-bash-launch/observe.py', proof / 'observe.py')
    env = {**os.environ, 'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': 'amd64'}
    subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(runtime / 'bin/awsh'), './cmd/awsh'],
                   cwd=ROOT / 'runtime/envoy', env=env, check=True)
    shutil.copyfile(runtime / 'bin/awsh', proof / 'bin/awsh-real')
    (proof / 'bin/awsh-real').chmod(0o555)
    for name, package in PACKAGES.items():
        subprocess.run(['go', 'test', '-buildvcs=false', '-c', '-o',
                        str(proof / 'bin' / (name + '.test')), './' + package],
                       cwd=ROOT / 'runtime/envoy', env=env, check=True)
    (context / 'Dockerfile').write_text(
        'ARG CANDIDATE\nFROM ${CANDIDATE}\n'
        'COPY runtime /omegaflow-runtime\nCOPY qualification /qualification\n'
        'RUN rm /etc/bash.bashrc && mkdir -p /run/omegaflow/session /proof '
        '/omegaflow-runtime/share/terminfo /omegaflow-runtime/lib/locale '
        '&& cp /qualification/repo/tests/envoy-bash-reserved-state/cases.json /proof/cases.json '
        '&& cp -a /usr/share/terminfo/. /omegaflow-runtime/share/terminfo/ '
        '&& if test -d /lib/terminfo; then cp -a /lib/terminfo/. /omegaflow-runtime/share/terminfo/; fi '
        '&& cp -a /usr/lib/locale/. /omegaflow-runtime/lib/locale/ '
        '&& chown 1000:1000 /run/omegaflow/session && chmod 700 /run/omegaflow/session '
        '&& chmod -R a-w /omegaflow-runtime /qualification /proof\nWORKDIR /\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquisition', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--release')
    parser.add_argument('--native', type=Path)
    args = parser.parse_args()
    if args.native:
        return native(args.native)
    if args.acquisition is None or args.output is None:
        parser.error('--acquisition and --output are required')
    tool = module(ROOT / 'tools/bash_builds.py', 'builds')
    launch = module(ROOT / 'tests/envoy-bash-launch/run.py', 'launch')
    candidates = launch._validate_acquisition(tool.read(args.acquisition))
    if args.release:
        candidates = [c for c in candidates if c['release'] == args.release]
        tool.require(len(candidates) == 1, 'unknown retained release')
    images = [launch._verify_image_identity(c) for c in candidates]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    context = output / 'context'
    stage(context)
    save(output / 'inputs.json', {
        'acquisition_sha256': tool.sha(args.acquisition.read_bytes()),
        'context': {str(p.relative_to(context)): tool.sha(p.read_bytes())
                    for p in sorted(context.rglob('*')) if p.is_file()}})
    results = []
    try:
        for candidate, image in zip(candidates, images):
            directory = output / candidate['release']
            directory.mkdir()
            save(directory / 'retained-candidate.json', candidate)
            iidfile = directory / 'image-id.txt'
            with (directory / 'build.log').open('w') as log:
                subprocess.run(['docker', 'build', '--build-arg', f'CANDIDATE={image}',
                                '--iidfile', str(iidfile), str(context)],
                               env={**os.environ, 'DOCKER_BUILDKIT': '0'},
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            iid = iidfile.read_text().strip()
            tool.require(launch._IMAGE_DIGEST_RE.fullmatch(iid) is not None, 'invalid derived image')
            with (directory / 'qualification.log').open('w') as log:
                run = subprocess.run(['docker', 'run', '--rm', '--network=none',
                    '-v', f'{directory}:/evidence', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                    '-e', 'OMEGAFLOW_CANDIDATE_DIGEST=' + candidate['observed']['executable_sha256'],
                    '--entrypoint=/usr/bin/python3', iid,
                    '/qualification/repo/tests/envoy-bash-qualification/run.py',
                    '--native=/evidence/retained-candidate.json'],
                    stdout=log, stderr=subprocess.STDOUT, timeout=630)
            row = {'release': candidate['release'], 'source_image': image,
                   'test_image': iid, 'returncode': run.returncode, 'qualified': False}
            results.append(row)
            tool.require(run.returncode == 0, 'native qualification failed; see ' + str(directory))
            entry = json.loads((directory / 'qualification.log').read_text())
            tool.receipt(entry['qualification'], entry['candidate'], tool.case_ids())
            save(directory / 'entry.json', entry)
            row['qualified'] = True
            row['receipt_sha256'] = tool.sha((directory / 'receipt.json').read_bytes())
    finally:
        save(output / 'results.json', results)
    print('Native B2 qualification passed: ' + str(output))


if __name__ == '__main__':
    main()
