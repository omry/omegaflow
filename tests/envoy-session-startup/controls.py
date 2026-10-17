#!/usr/bin/env python3
"""Prove integrated lifecycle/gate dispatch on genuinely qualified Bash targets."""
import argparse
from pathlib import Path
import subprocess

from staging import build_peer, save, sha, stage, verified_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release', choices=('5.2.15', '5.2.37', '5.3.9'))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    with build_peer(output, './session') as (binary, current_awsh):
        results = []
        for row, entry, actual in verified_rows(args.qualification, current_awsh):
            if args.release and args.release != row['release']:
                continue
            iid, context = stage(output, row['release'], entry, actual, binary)
            with (context / 'native.log').open('w') as log:
                result = subprocess.run(
                    ['docker', 'run', '--rm', '--network=none',
                     '-e', 'OMEGAFLOW_NATIVE_CONTROL=1',
                     '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                     '-test.run=^TestNativeControl', '-test.v'],
                    stdout=log, stderr=subprocess.STDOUT, timeout=45)
            results.append({'release': row['release'], 'qualified_awsh_sha256': current_awsh,
                            'receipt_sha256': row['receipt_sha256'], 'native_image': iid,
                            'test_binary_sha256': sha(binary), 'returncode': result.returncode,
                            'log_path': str(context / 'native.log'),
                            'log_sha256': sha(context / 'native.log')})
            save(output / 'results.json', results)
            if result.returncode:
                raise ValueError('actual integrated control proof failed: ' + str(context / 'native.log'))
        if not results:
            raise ValueError('no selected qualified target was exercised')
        print('Native integrated control evidence: ' + str(output))


if __name__ == '__main__':
    main()
