#!/usr/bin/env python3
"""Prove native gate helpers and barriers on genuinely qualified Bash targets."""
import argparse
from pathlib import Path
import subprocess

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
            result = subprocess.run(
                ['docker', 'run', '--rm', '--network=none',
                 '-e', 'OMEGAFLOW_NATIVE_GATE=1',
                 '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                 '-test.run=^TestNativeGate', '-test.v'],
                stdout=log, stderr=subprocess.STDOUT, timeout=45)
        results.append({'release': row['release'], 'qualified_awsh_sha256': current_awsh,
                        'receipt_sha256': row['receipt_sha256'], 'native_image': iid,
                        'test_binary_sha256': sha(binary), 'returncode': result.returncode,
                        'log_path': str(context / 'native.log'),
                        'log_sha256': sha(context / 'native.log')})
        save(output / 'results.json', results)
        if result.returncode:
            raise ValueError('actual gate proof failed: ' + str(context / 'native.log'))
    if len(results) != 3:
        raise ValueError('complete genuine target matrix required')
    print('Native gate evidence: ' + str(output))


if __name__ == '__main__':
    main()
