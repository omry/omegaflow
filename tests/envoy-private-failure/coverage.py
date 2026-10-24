#!/usr/bin/env python3
"""Close the B8.3 map from current native evidence and enclosing Go checks."""
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('private_failure_runner', Path(__file__).with_name('run.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
sha, save = runner.sha, runner.save
RELEASES = {'5.2.15', '5.2.37', '5.3.9'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def current_sources(receipt, owner):
    sources = receipt['sources']
    required = set(map(str, (ROOT / 'runtime/envoy/session').glob('*.go')))
    required.update(str(ROOT / 'tests' / owner / name) for name in ('run.py', 'case-map.json'))
    require(set(sources) == required, 'missing or extra native source bindings')
    for name, digest in sources.items():
        require(sha(Path(name)) == digest, 'stale native source: ' + name)


def controls(rows, names):
    require(Counter(row['name'] for row in rows) == Counter(list(names)), 'incomplete omission controls')
    for row in rows:
        log = Path(row['log'])
        require(row['passed'] and row['returncode'] != 0 and sha(log) == row['log_sha256'] and
                row['expected_diagnostic'] in log.read_text(), 'unproved omission: ' + row['name'])


def native_rows(receipts, expected, marker):
    rows = [row for receipt in receipts for row in receipt['rows']]
    require(Counter(row['release'] for row in rows) == Counter(RELEASES), 'need each genuine target exactly once')
    for row in rows:
        require(row['passed'] and row['returncode'] == 0 and Counter(row['cases']) == Counter(expected),
                'incomplete native case selection: ' + row['release'])
        log = Path(row['log'])
        require(sha(log) == row['log_sha256'], 'native transcript changed')
        observed = re.findall(marker + r' ([^ ]+) PROVED', log.read_text())
        require(Counter(observed) == Counter(expected), 'native proof identities differ from selected cases')
    return {row['release']: (row['qualified_awsh_sha256'], row['qualified_base_image']) for row in rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-results', required=True, type=Path, action='append')
    parser.add_argument('--controls-results', required=True, type=Path)
    parser.add_argument('--captured-results', required=True, type=Path)
    parser.add_argument('--go-log', required=True, type=Path,
                        help='go test -race -json ./... log from the enclosing validation profile')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    mapping, _, expected = runner.read_map()
    private = [read(path) for path in args.private_results]
    for receipt in private:
        require(receipt['status'] == 'passed' and receipt['complete_native_selection'] and not receipt['omission'],
                'development subset or negative run cannot close acceptance')
        current_sources(receipt, 'envoy-private-failure')
        require(receipt['case_map_sha256'] == sha(Path(__file__).with_name('case-map.json')), 'stale private case map')
    qualified = native_rows(private, expected, 'NATIVE PRIVATE FAILURE')
    proof = read(args.controls_results)
    current_sources(proof, 'envoy-private-failure')
    require(proof['status'] == 'passed', 'omission proof failed')
    controls(proof['controls'], runner.omission_specs())
    captured = read(args.captured_results)
    require(captured['status'] == 'passed' and captured['native_acceptance_complete'] and captured['source_unchanged'],
            'captured closure requires its complete current native matrix and controls')
    current_sources(captured, 'envoy-captured-closure')
    prior = read(ROOT / 'tests/envoy-captured-closure/case-map.json')['cases']
    prior = set(prior) | {'extra-continue-reap-bound'}
    require(native_rows([captured], prior, 'NATIVE CAPTURED') == qualified, 'native targets or Awsh identities differ')
    controls(captured['controls'], ('captured-deadline', 'captured-priority', 'retired-captured-context',
                                   'continue-deadline', 'cancelled-gate-entry', 'continue-closure-bound'))
    events = [json.loads(line) for line in args.go_log.read_text().splitlines()]
    require(not any(row.get('Action') == 'fail' for row in events), 'enclosing Go validation failed')
    passed = {row['Package'] for row in events if row.get('Action') == 'pass' and 'Test' not in row}
    prefix = 'github.com/omry/omegaflow/runtime/envoy/'
    require({prefix + 'session', prefix + 'protocol'} <= passed, 'session race or conformance inventory check missing')
    inputs = [*args.private_results, args.controls_results, args.captured_results, args.go_log]
    require(not args.output.exists(), 'retain an existing cumulative receipt unchanged')
    save(args.output, {'status': 'passed', 'owning_leaf': 'B8.3.2', 'assigned_cases': len(mapping['cases']),
                       'predecessor_cases': 67, 'cumulative_cases': 330, 'native_scenarios_per_target': len(expected),
                       'qualified_targets': qualified, 'checks': mapping['cases']['B1-PACKAGE-B8.3']['checks'],
                       'evidence': {str(path): sha(path) for path in inputs},
                       'go_input_binding': 'Owned by the enclosing exact-candidate validation receipt; no separate approval asserted.'})
    print('B8.3 cumulative acceptance evidence: 330 cases, three genuine targets, both native matrices and omission controls.')


if __name__ == '__main__':
    main()
