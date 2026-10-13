#!/usr/bin/env python3
"""Run complete public startup and idle shutdown on genuine qualified targets."""
import argparse
import os
from pathlib import Path
import subprocess

from private import pre_session_failures
from staging import build_peer, save, sha, stage, verified_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary, current_awsh = build_peer(output, './session')
    results = []
    for row, entry, actual in verified_rows(args.qualification, current_awsh):
        iid, context = stage(output, row['release'], entry, actual, binary)
        with (context / 'native.log').open('w') as log:
            result = subprocess.run(['docker', 'run', '--rm', '--network=none',
                                     '-e', 'OMEGAFLOW_NATIVE_PUBLIC_STARTUP=1',
                                     '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                                     '-test.run=^TestNativePublic(Startup(ExpiredEpoch|ForeignDirectory|SelectedLoss)?|ShutdownSelectedLoss|CleanupTimeout)$', '-test.v'],
                                    stdout=log, stderr=subprocess.STDOUT, timeout=45)
        failures = pre_session_failures(output, row['release'], iid, public=True)
        results.append({'release': row['release'], 'qualified_awsh_sha256': current_awsh,
                        'receipt_sha256': row['receipt_sha256'],
                        'native_image': iid, 'returncode': result.returncode,
                        'log_path': str(context / 'native.log'),
                        'log_sha256': sha(context / 'native.log'),
                        'pre_session_failures': failures})
        save(output / 'results.json', results)
        if result.returncode or any(row['returncode'] for row in failures):
            raise ValueError('actual public startup failed: ' + str(context / 'native.log'))
    print('Complete public startup passed on all three genuinely qualified targets: ' + str(output))


if __name__ == '__main__':
    main()
