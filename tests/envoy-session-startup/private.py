#!/usr/bin/env python3
"""Run the actual private Awsh entry on fresh genuine qualification outputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = output / 'native.test'
    subprocess.run(['go', 'test', '-buildvcs=false', '-c', '-o', str(binary),
                    './awsh/supervise'], cwd=ROOT / 'runtime/envoy', check=True,
                   env={**os.environ, 'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': 'amd64'})
    go_version = subprocess.check_output(['go', 'env', 'GOVERSION'], text=True).strip()
    revision = subprocess.check_output(['sl', 'log', '-r', '.', '-T', '{node}'],
                                      cwd=ROOT, env={**os.environ, 'CHGDISABLE': '1'}, text=True)
    rows = json.loads((args.qualification / 'results.json').read_text())
    if len(rows) != 3 or not all(row['qualified'] and row['returncode'] == 0 for row in rows):
        raise ValueError('complete genuine qualification matrix required')
    results = []
    for row in rows:
        release = row['release']
        retained = args.qualification / release
        entry = json.loads((retained / 'entry.json').read_text())
        if sha(retained / 'receipt.json') != row['receipt_sha256']:
            raise ValueError('qualification receipt drift')
        actual = subprocess.check_output(['docker', 'image', 'inspect', '--format',
                                         '{{.Id}}', row['test_image']], text=True).strip()
        if actual != row['test_image']:
            raise ValueError('qualified test-image identity drift')
        context = output / release
        context.mkdir()
        shutil.copyfile(binary, context / 'native.test')
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
        with (context / 'native.log').open('w') as log:
            result = subprocess.run(['docker', 'run', '--rm', '--network=none',
                                     '-e', 'OMEGAFLOW_NATIVE_PRIVATE_STARTUP=1',
                                     '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                                     '-test.run=^TestNative(RuntimeInputs|PrivateStartup)$', '-test.v'],
                                    stdout=log, stderr=subprocess.STDOUT, timeout=45)
        results.append({'release': release, 'qualified_awsh_sha256':
                        entry['qualification']['adapter_inputs']['/omegaflow-runtime/bin/awsh'],
                        'native_image': iid, 'returncode': result.returncode,
                        'log_sha256': sha(context / 'native.log')})
        save(output / 'results.json', results)
        if result.returncode:
            raise ValueError('actual private startup failed: ' + str(context / 'native.log'))
    print('Actual private startup passed on all three genuinely qualified targets: ' + str(output))


if __name__ == '__main__':
    main()
