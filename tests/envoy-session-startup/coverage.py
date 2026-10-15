#!/usr/bin/env python3
"""Supplement actual-entry proofs with component coverage and a forced schedule."""
import argparse
import os
from pathlib import Path
import subprocess

from staging import ROOT, build_peer, save, sha, stage, verified_rows


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('scheduling fixture source anchor changed: ' + old)
    return text.replace(old, new)


def build(binary, overlay=None, coverage=False):
    args = ['go', 'test', '-buildvcs=false', '-c', '-o', str(binary)]
    if coverage:
        args.append('-coverpkg=./awsh/supervise,./awsh/launch,./awsh/submission')
    if overlay:
        args.append('-overlay=' + str(overlay))
    subprocess.run(args + ['./awsh/supervise'], cwd=ROOT / 'runtime/envoy',
                   env={**os.environ, 'CGO_ENABLED': '0', 'GOOS': 'linux',
                        'GOARCH': 'amd64'}, check=True)


def scheduling_overlay(output, drop_pending):
    """Instrument copies only. Go coverage is deliberately disabled here."""
    serve = ROOT / 'runtime/envoy/awsh/supervise/serve_linux.go'
    fixture = ROOT / 'runtime/envoy/awsh/supervise/operation_linux_test.go'
    code = replace_once(serve.read_text(),
                        'job <- execute(ctx, s, inputs, *m, controls, releases)', '''err := execute(ctx, s, inputs, *m, controls, releases)
			if err == nil && m.OperationID == "first" {
				_ = os.WriteFile("/evidence/worker-entered", []byte{1}, 0600)
				f, e := os.Open("/evidence/release-job")
				if e == nil { var b [1]byte; _, e = f.Read(b[:]); f.Close() }
				if e != nil { err = e }
			}
			job <- err''')
    pending = 'pending = m // preceding completed/rejected write is finishing'
    code = replace_once(code, pending,
                        ('pending = nil' if drop_pending else pending) +
                        '\n\t\t\t\t\t_ = os.WriteFile("/evidence/pending-seen", []byte{1}, 0600)')
    (output / serve.name).write_text(code)
    code = replace_once(fixture.read_text(),
                        'coverage := os.Getenv("OMEGAFLOW_NATIVE_PRIVATE_COVERAGE") == "1"', '''coverage := os.Getenv("OMEGAFLOW_NATIVE_PRIVATE_COVERAGE") == "1"
	for _, name := range []string{"/evidence/pending-seen", "/evidence/release-job"} {
		if err := syscall.Mkfifo(name, 0600); err != nil { t.Fatal(err) }
		t.Cleanup(func(){ os.Remove(name) })
	}''')
    code = replace_once(code,
                        ', "-test.coverprofile=/evidence/"+outcome+"-"+strconv.FormatInt(time.Now().UnixNano(), 10)+".cover"', '')
    code = replace_once(code,
                        'if err := helper.WriteAll(cw, next); err != nil {\n\t\t\t\tt.Fatal(err)\n\t\t\t}', '''if err := helper.WriteAll(cw, next); err != nil { t.Fatal(err) }
			f, err := os.Open("/evidence/pending-seen")
			if err != nil { t.Fatal(err) }
			var b [1]byte
			n, err := f.Read(b[:]); f.Close()
			if n != 1 || err != nil || b[0] != 1 { t.Fatal("deferred request was not observed", n, err) }
			if err := os.WriteFile("/evidence/deferred-observed", b[:], 0600); err != nil { t.Fatal(err) }
			if err := os.WriteFile("/evidence/release-job", []byte{1}, 0600); err != nil { t.Fatal(err) }
			rr.SetReadDeadline(time.Now().Add(2*time.Second))''')
    (output / fixture.name).write_text(code)
    overlay = output / 'overlay.json'
    save(overlay, {'Replace': {str(f): str(output / f.name) for f in (serve, fixture)}})
    return overlay


def run_image(output, row, entry, actual, binary, fault=False):
    image, context = stage(output, row['release'], entry, actual, binary)
    evidence = context / 'evidence'
    evidence.mkdir(mode=0o777)
    evidence.chmod(0o777)  # the isolated peer runs as uid 1000
    selector = '^TestNativePrivateOperation$/ordinary$' if fault else '^TestNativePrivateOperation$'
    with (context / 'native.log').open('w') as log:
        result = subprocess.run(
            ['docker', 'run', '--rm', '--network=none', '-v', str(evidence) + ':/evidence',
             '-e', 'OMEGAFLOW_NATIVE_PRIVATE_OPERATION=1',
             '-e', 'OMEGAFLOW_NATIVE_PRIVATE_COVERAGE=1',
             '--entrypoint=/omegaflow-runtime/bin/envoy', image,
             '-test.run=' + selector, '-test.timeout=30s', '-test.v'],
            stdout=log, stderr=subprocess.STDOUT, timeout=40)
    return result.returncode, context, evidence, image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = [ROOT / 'runtime/envoy/awsh/supervise' / name
               for name in ('serve_linux.go', 'operation_linux_test.go')]
    before = {str(f): sha(f) for f in sources}
    binary, current_awsh = build_peer(output, './awsh/supervise')
    rows = list(verified_rows(args.qualification, current_awsh))
    results = {'qualified_awsh_sha256': current_awsh, 'coverage': [], 'schedule': []}
    try:
        build(binary, coverage=True)
        profiles = []
        for row, entry, actual in rows:
            code, context, evidence, image = run_image(output, row, entry, actual, binary)
            found = sorted(evidence.glob('*.cover'))
            results['coverage'].append({'release': row['release'], 'returncode': code,
                                        'image': image, 'log_sha256': sha(context / 'native.log'),
                                        'profiles': [str(f) for f in found]})
            if code or not found:
                raise ValueError('component coverage failed: ' + str(context / 'native.log'))
            profiles.extend(found)
        # Each child has the same instrumented packages. Union block counts;
        # profiles from instrumented fault copies never enter this collection.
        blocks = {}
        for profile in profiles:
            lines = profile.read_text().splitlines()
            if lines[0] != 'mode: set':
                raise ValueError('unexpected coverage mode')
            for line in lines[1:]:
                block, count = line.rsplit(' ', 1)
                blocks[block] = max(blocks.get(block, 0), int(count))
        (output / 'components.cover').write_text(
            'mode: set\n' + ''.join(f'{k} {v}\n' for k, v in sorted(blocks.items())))
        # Hold the first completed job until the next execute is actually
        # pending. Then prove retention, and prove dropping it fails the test.
        for drop in (False, True):
            fault = output / ('schedule-drop' if drop else 'schedule-retain')
            fault.mkdir()
            overlay = scheduling_overlay(fault, drop)
            fault_binary = fault / 'native.test'
            build(fault_binary, overlay=overlay)
            if b'/evidence/worker-entered' not in fault_binary.read_bytes():
                raise ValueError('Go did not compile the scheduling overlay')
            for row, entry, actual in rows:
                code, context, evidence, image = run_image(fault, row, entry, actual, fault_binary, fault=True)
                observed = (evidence / 'deferred-observed').read_bytes() == b'\x01'
                log = (context / 'native.log').read_text()
                expected = bool(code) and 'private result' in log and 'i/o timeout' in log if drop else code == 0
                results['schedule'].append({'release': row['release'], 'drop_pending': drop,
                                            'returncode': code, 'expected_result': expected,
                                            'deferred_observed': observed, 'image': image,
                                            'log_sha256': sha(context / 'native.log')})
                if not observed or not expected:
                    raise ValueError('scheduling counterfactual failed: ' + str(context / 'native.log'))
    finally:
        after = {str(f): sha(f) for f in sources}
        results['source_before'] = before
        results['source_after'] = after
        save(output / 'results.json', results)
        if after != before:
            raise ValueError('candidate source changed during supplemental proof')
    print('Component coverage and deferred-reply counterfactual passed: ' + str(output))


if __name__ == '__main__':
    main()
