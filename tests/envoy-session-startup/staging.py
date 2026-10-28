"""Shared isolated native payload staging; no production path override."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]


def artifact_tool():
    sys.path.insert(0, str(ROOT / 'tools'))
    import build_envoy_runtime
    return build_envoy_runtime


def stage_artifact(output, release, actual, artifact):
    """Copy the whole accepted artifact; never synthesize a table or manifest."""
    tool = artifact_tool()
    before = tool.read_artifact(artifact)
    context = output / release
    context.mkdir()
    shutil.copytree(artifact, context / 'runtime')
    if tool.read_artifact(context / 'runtime') != before:
        raise ValueError('runtime copy changed artifact bytes')
    (context / 'Dockerfile').write_text(
        f'FROM {actual}\n'
        'RUN find /omegaflow-runtime -type d -exec chmod u+w {} + '
        '&& rm -rf /omegaflow-runtime /run/omegaflow/session '
        '&& chown 1000:1000 /run/omegaflow && chmod 700 /run/omegaflow\n'
        'COPY runtime /omegaflow-runtime\n'
        'RUN chmod 555 /omegaflow-runtime\nWORKDIR /\nUSER 1000:1000\n')
    iidfile = context / 'image-id.txt'
    with (context / 'build.log').open('w') as log:
        subprocess.run(['docker', 'build', '--iidfile', str(iidfile), str(context)],
                       env={**os.environ, 'DOCKER_BUILDKIT': '0'}, stdout=log,
                       stderr=subprocess.STDOUT, check=True)
    if tool.read_artifact(artifact) != before:
        raise ValueError('caller artifact changed during image build')
    save(context / 'artifact.json', {'manifest': before[0],
         'manifest_sha256': sha(artifact / 'manifest.json')})
    return iidfile.read_text().strip(), context


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


@contextmanager
def temporary_binary(output):
    """Own rebuildable executables separately from retained proof evidence."""
    with TemporaryDirectory(prefix='.native-build-', dir=output) as scratch:
        yield Path(scratch) / 'native.test'


@contextmanager
def build_peer(output, package, *, production=False):
    with temporary_binary(output) as binary:
        env = {**os.environ, 'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': 'amd64'}
        build = (['go', 'build', '-trimpath', '-buildvcs=false', '-ldflags=-s -w -buildid=']
                 if production else ['go', 'test', '-buildvcs=false', '-c'])
        subprocess.run([*build, '-o', str(binary), package],
                       cwd=ROOT / 'runtime/envoy', check=True, env=env)
        current = binary.with_name('awsh-current')
        subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(current), './cmd/awsh'],
                       cwd=ROOT / 'runtime/envoy', check=True, env=env)
        yield binary, sha(current)


def verified_rows(qualification, current_awsh):
    rows = json.loads((qualification / 'results.json').read_text())
    if len(rows) != 3 or not all(row['qualified'] and row['returncode'] == 0 for row in rows):
        raise ValueError('complete genuine qualification matrix required')
    for row in rows:
        retained = qualification / row['release']
        receipt = json.loads((retained / 'receipt.json').read_text())
        entry = json.loads((retained / 'entry.json').read_text())
        if sha(retained / 'receipt.json') != row['receipt_sha256'] or entry['qualification'] != receipt:
            raise ValueError('qualification receipt or entry drift')
        if current_awsh != receipt['adapter_inputs']['/omegaflow-runtime/bin/awsh']:
            raise ValueError('changed Awsh requires genuine requalification')
        for name in ('awsh-bashrc', 'inputrc'):
            if sha(ROOT / 'runtime/envoy/etc' / name) != receipt['adapter_inputs']['/omegaflow-runtime/etc/' + name]:
                raise ValueError('changed fixed startup asset requires requalification')
        actual = subprocess.check_output(['docker', 'image', 'inspect', '--format',
                                         '{{.Id}}', row['test_image']], text=True).strip()
        if actual != row['test_image']:
            raise ValueError('qualified test-image identity drift')
        yield row, entry, actual


def stage(output, release, entry, actual, binary):
    go_version = subprocess.check_output(['go', 'env', 'GOVERSION'], text=True).strip()
    revision = subprocess.check_output(['sl', 'log', '-r', '.', '-T', '{node}'],
                                      cwd=ROOT, env={**os.environ, 'CHGDISABLE': '1'}, text=True)
    context = output / release
    context.mkdir()
    staged = context / 'native.test'
    try:
        save(context / 'test-binary.json', {'sha256': sha(binary),
                                          'bytes': binary.stat().st_size})
        shutil.copyfile(binary, staged)
        return build_image(context, entry, actual, go_version, revision)
    finally:
        # Docker has consumed this copy, even when the build fails.
        staged.unlink(missing_ok=True)


def build_image(context, entry, actual, go_version, revision):
    table = {'version': 1, 'builds': {entry['candidate']['executable_sha256']: entry}}
    (context / 'bash-builds.json').write_text(json.dumps(table, sort_keys=True, separators=(',', ':')) + '\n')
    (context / 'manifest.py').write_text('''import hashlib,json,pathlib,os
r=pathlib.Path('/omegaflow-runtime')
# The isolated payload must satisfy the same regular-file contract as C4.
# Preserve every qualified logical asset's exact bytes while materializing aliases.
for p in sorted(r.rglob('*')):
 if p.is_symlink():
  data=p.read_bytes(); mode=p.stat().st_mode&0o777
  p.unlink(); p.write_bytes(data); p.chmod(mode)
files=[]
for p in sorted(r.rglob('*')):
 if p.is_file():
  files.append(dict(path=str(p.relative_to(r)),size=p.stat().st_size,executable=bool(p.stat().st_mode&0o111),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
m=dict(schema='omegaflow-runtime-manifest-v1',version='isolated-native-proof',source_revision=os.environ['PROOF_REVISION'],go_toolchain=os.environ['PROOF_GO'],target=dict(os='linux',arch='amd64'),schemas=dict(telemetry='omegaflow-envoy-telemetry-v1',private='awsh-v1',helper='awsh-helper-v1'),files=files)
(r/'manifest.json').write_text(json.dumps(m,separators=(',',':'))+'\\n')
''')
    (context / 'Dockerfile').write_text(
        f'FROM {actual}\nCOPY native.test /omegaflow-runtime/bin/envoy\n'
        'COPY bash-builds.json /omegaflow-runtime/etc/bash-builds.json\n'
        'COPY manifest.py /tmp/manifest.py\n'
        f'ENV PROOF_REVISION={revision} PROOF_GO={go_version}\n'
        'RUN chmod 555 /omegaflow-runtime/bin/envoy '
        '&& /usr/bin/python3 /tmp/manifest.py '
        '&& chmod -R a-w /omegaflow-runtime '
        '&& rm -rf /run/omegaflow/session '
        '&& chown 1000:1000 /run/omegaflow && chmod 700 /run/omegaflow\n'
        'WORKDIR /\nUSER 1000:1000\n')
    iidfile = context / 'image-id.txt'
    with (context / 'build.log').open('w') as log:
        subprocess.run(['docker', 'build', '--iidfile', str(iidfile), str(context)],
                       env={**os.environ, 'DOCKER_BUILDKIT': '0'}, stdout=log,
                       stderr=subprocess.STDOUT, check=True)
    iid = iidfile.read_text().strip()
    return iid, context
