#!/usr/bin/env python3
"""Run the actual private Awsh entry on fresh genuine qualification outputs."""
import argparse
import os
from pathlib import Path
import subprocess

from staging import build_peer, save, sha, stage, verified_rows


def pre_session_failures(output, release, qualified_image, public=False):
    results = []
    for kind in ('manifest', 'qualification'):
        context = output / (release + '-pre-session-' + kind)
        context.mkdir()
        setup = 'RUN rm /omegaflow-runtime/manifest.json\n'
        if kind == 'qualification':
            save(context / 'bash-builds.json', {'version': 1, 'builds': {}})
            setup = ('COPY bash-builds.json /omegaflow-runtime/etc/bash-builds.json\n'
                     'RUN rm /omegaflow-runtime/manifest.json '
                     '&& /usr/bin/python3 /tmp/manifest.py '
                     '&& chmod -R a-w /omegaflow-runtime\n')
        (context / 'Dockerfile').write_text(
            f'FROM {qualified_image}\nUSER 0:0\n' + setup + 'USER 1000:1000\n')
        iidfile = context / 'image-id.txt'
        with (context / 'build.log').open('w') as log:
            subprocess.run(['docker', 'build', '--iidfile', str(iidfile), str(context)],
                           env={**os.environ, 'DOCKER_BUILDKIT': '0'}, stdout=log,
                           stderr=subprocess.STDOUT, check=True)
        iid = iidfile.read_text().strip()
        with (context / 'native.log').open('w') as log:
            result = subprocess.run(['docker', 'run', '--rm', '--network=none',
                                     '-e', ('OMEGAFLOW_NATIVE_PUBLIC_FAILURE=' if public else
                                            'OMEGAFLOW_NATIVE_PRESESSION_FAILURE=') + kind,
                                     '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                                     ('-test.run=^TestNativePublicStartupRefusal$' if public else
                                      '-test.run=^TestNativePreSessionFailure$'), '-test.v'],
                                    stdout=log, stderr=subprocess.STDOUT, timeout=20)
        results.append({'kind': kind, 'native_image': iid,
                        'returncode': result.returncode,
                        'log_path': str(context / 'native.log'),
                        'log_sha256': sha(context / 'native.log')})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary, current_awsh = build_peer(output, './awsh/supervise')
    results = []
    for row, entry, actual in verified_rows(args.qualification, current_awsh):
        release = row['release']
        iid, context = stage(output, release, entry, actual, binary)
        with (context / 'native.log').open('w') as log:
            result = subprocess.run(['docker', 'run', '--rm', '--network=none',
                                     '-e', 'OMEGAFLOW_NATIVE_PRIVATE_STARTUP=1',
                                     '-e', 'OMEGAFLOW_NATIVE_PRIVATE_OPERATION=1',
                                     '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                                     '-test.run=^TestNative(RuntimeInputs|PrivateStartup|PrivateOperation)$', '-test.v'],
                                    stdout=log, stderr=subprocess.STDOUT, timeout=45)
        failures = pre_session_failures(output, release, iid)
        results.append({'release': release, 'qualified_awsh_sha256':
                        entry['qualification']['adapter_inputs']['/omegaflow-runtime/bin/awsh'],
                        'native_image': iid, 'returncode': result.returncode,
                        'log_sha256': sha(context / 'native.log'),
                        'pre_session_failures': failures})
        save(output / 'results.json', results)
        if result.returncode or any(row['returncode'] for row in failures):
            logs = [row['log_path'] for row in failures if row['returncode']]
            if result.returncode:
                logs.insert(0, str(context / 'native.log'))
            raise ValueError('actual private startup failed: ' + ', '.join(logs))
    print('Actual private startup passed on all three genuinely qualified targets: ' + str(output))


if __name__ == '__main__':
    main()
