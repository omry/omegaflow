#!/usr/bin/env python3
"""B2.1.1 isolated admission assembly; never produces support evidence.

The receipt envelope is synthetic. Startup/Readline measurements and actual
Start execution are real. All-case qualification and support remain B2.8.
"""
import argparse
import base64
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import pty
import re
import select
import shutil
import signal
import subprocess
import termios
import time

RUNTIME = Path('/omegaflow-runtime')
TABLE = str(RUNTIME / 'etc/bash-builds.json')
ENV = {'PATH': '/hostile/path', 'HOME': '/hostile/home', 'PWD': '/',
       'HISTFILE': '', 'INPUTRC': str(RUNTIME / 'etc/inputrc'),
       'TERM': 'xterm-256color', 'TERMINFO': str(RUNTIME / 'share/terminfo'),
       'TERMINFO_DIRS': str(RUNTIME / 'share/terminfo'), 'LC_ALL': 'C.UTF-8',
       'LANG': 'C.UTF-8', 'LOCPATH': str(RUNTIME / 'lib/locale')}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def assets():
    # Bind every regular file, resolving the copied terminal tree's aliases.
    result = {}
    for path in sorted(RUNTIME.rglob('*')):
        if path.is_file() and str(path) != TABLE:
            resolved = path.resolve(strict=True)
            require(resolved.is_relative_to(RUNTIME), 'asset escapes trusted tree')
            result[str(resolved)] = sha(resolved)
    return result


def measure():
    release = platform.freedesktop_os_release()
    bash = Path('/bin/bash').resolve(strict=True)
    output = subprocess.check_output(['/bin/bash', '--noprofile', '--norc', '-c',
                                      'builtin trap -l'], text=True)
    signals = {name.removeprefix('SIG'): int(number)
               for number, name in re.findall(r'(\d+)\)\s+(\S+)', output)
               if int(number) not in (9, 19)}
    require(not Path('/etc/bash.bashrc').exists(), 'system rc exists')
    return {'target': {'os': platform.system().lower(),
                       'arch': {'x86_64': 'amd64', 'aarch64': 'arm64'}[platform.machine()],
                       'os_release_id': release['ID'], 'version_id': release['VERSION_ID']},
            'resolved_path': str(bash), 'executable_sha256': sha(bash),
            'adapter_inputs': assets(), 'catchable_signals': signals}


def start(expected_rejection=False):
    master, slave = pty.openpty()
    control_r, control_w = os.pipe()
    result_r, result_w = os.pipe()
    evidence_r, evidence_w = os.pipe()
    sources = [control_r, result_w, slave, evidence_w]
    stable = [fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10) for fd in sources]
    pid = os.fork()
    if pid == 0:
        for target, source in enumerate(stable, 3):
            os.dup2(source, target)
        for fd, mode in ((0, os.O_RDONLY), (1, os.O_WRONLY), (2, os.O_WRONLY)):
            null = os.open('/dev/null', mode)
            os.dup2(null, fd)
            if null > 6:
                os.close(null)
        os.closerange(7, 65536)
        os.execve(str(RUNTIME / 'bin/awsh'), ['awsh', 'proof-qualified-start'], ENV)
    for fd in stable + sources:
        os.close(fd)
    os.set_blocking(master, False)
    raw = bytearray()
    evidence = bytearray()
    frame = bytearray()
    exited = None
    deadline = time.monotonic() + 12
    try:
        while time.monotonic() < deadline:
            for fd in select.select([master, result_r, evidence_r], [], [], .02)[0]:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    data = b''
                if fd == master:
                    raw.extend(data)
                elif fd == result_r:
                    frame.extend(data)
                else:
                    evidence.extend(data)
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                exited = os.waitstatus_to_exitcode(status)
                break
            if b'\n' in evidence and frame.count(0) >= 5:
                break
        if expected_rejection:
            require(exited == 2 and not raw and not frame and not evidence,
                    'rejected admission created startup output or accepted readiness')
            require(not Path('/run/omegaflow/session/bash').exists(), 'rejection created private paths')
            return {'rejected_before_start': True}
        require(exited is None and b'\n' in evidence, 'qualified Start failed or timed out')
        detail = json.loads(evidence)
        require(frame.endswith(b'\0') and frame.count(0) == 5, 'partial/extra private readiness')
        fields = frame[:-1].decode().split('\0')
        require(fields == ['awsh-v1', 'ready', str(pid), str(detail['shell_pid']), '/'],
                'wrong actual ready identity')
        require(detail['state']['Status'] == 0 and detail['state']['HistExpand'] == 'off'
                and detail['state']['EditingMode'] == 'emacs', 'wrong startup state')
        attrs = termios.tcgetattr(master)
        require(not attrs[3] & (termios.ICANON | termios.ECHO), 'Readline not echo-off ready')
        time.sleep(.03)
        while select.select([master], [], [], 0)[0]:
            raw.extend(os.read(master, 65536))
        require(len(raw) <= 4096 and not re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', raw),
                f'unexpected startup prompt bytes: {bytes(raw)!r}')
        exported = detail['state']['ExportedEnv']
        expected = {**ENV, 'SHLVL': '1', '_': str(RUNTIME / 'bin/awsh')}
        require(exported == expected, 'startup export differs from measured transform')
        os.write(control_w, b'x')
        waited, status = os.waitpid(pid, 0)
        exited = os.waitstatus_to_exitcode(status)
        require(waited == pid and exited == 0, 'assembled Awsh cleanup failed')
        require(not Path('/run/omegaflow/session/bash').exists(), 'private cleanup incomplete')
        return {'startup_pty_base64': base64.b64encode(raw).decode(),
                'startup_export': {'unset': [], 'set': {'SHLVL': '1', '_': str(RUNTIME / 'bin/awsh')}},
                'state': detail['state'], 'readline_ready': True, 'exact_cleanup': True}
    finally:
        if exited is None:
            # This test peer owns teardown; production cleanup is separately proved.
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        for fd in (master, control_w, result_r, evidence_r):
            os.close(fd)


def host(args):
    root = Path(__file__).resolve().parents[2]
    tool = module(root / 'tools/bash_builds.py', 'builds')
    launch = module(root / 'tests/envoy-bash-launch/run.py', 'launch')
    candidates = launch._validate_acquisition(json.loads(args.acquisition.read_text()))
    startup = json.loads((args.startup / 'results.json').read_text())
    readline = json.loads((args.readline / 'results.json').read_text())
    require(len(startup) == len(candidates) == len(readline), 'incomplete native candidate runs')
    require(all(row['returncode'] == 0 and row['raw_restoration']['returncode'] == 0
                and all(fault['returncode'] == 0 for fault in row['faults'].values())
                for row in startup), 'failed native startup proof')
    require(all(row['checker'] == row['readline'] == 'passed'
                and all(fault['passed'] for fault in row['loader_faults'].values())
                for row in readline), 'failed native Readline proof')
    binary = args.startup / 'context/bin/awsh'
    require(sha(binary) == sha(args.readline / 'context/bin/awsh'), 'measured helper binary changed')
    args.output.mkdir(parents=True, exist_ok=False)
    context = args.output / 'context'
    shutil.copytree(args.startup / 'context', context)
    (context / 'proof').mkdir()
    shutil.copyfile(__file__, context / 'proof/qualified.py')
    results = []
    for item in candidates:
        release = item['release']
        source = launch._verify_image_identity(item)
        for rows in (startup, readline):
            matches = [row for row in rows if row['release'] == release]
            require(len(matches) == 1 and matches[0]['source_image'] == source
                    and matches[0]['executable_sha256'] == item['observed']['executable_sha256'],
                    'native proof candidate mismatch')
        iidfile = args.output / f'{release}.image'
        dockerfile = (context / 'Dockerfile').read_text() + 'COPY proof /proof\n'
        (context / 'Dockerfile.qualified').write_text(dockerfile)
        log = args.output / f'{release}-build.log'
        with log.open('w') as stream:
            subprocess.run(['docker', 'build', '-f', str(context / 'Dockerfile.qualified'),
                            '--build-arg', f'CANDIDATE={source}', '--iidfile', str(iidfile),
                            str(context)], check=True, stdout=stream, stderr=subprocess.STDOUT,
                           env={**os.environ, 'DOCKER_BUILDKIT': '0'})
        image = iidfile.read_text().strip()
        prefix = ['docker', 'run', '--rm', '--network=none']
        command = [image, '/usr/bin/python3', '/proof/qualified.py']
        observed = json.loads(subprocess.check_output(prefix + command + ['--measure']))
        require(observed['executable_sha256'] == item['observed']['executable_sha256'], 'Bash identity changed')
        require(observed['adapter_inputs'][str(RUNTIME / 'bin/awsh')] == sha(binary), 'assembly binary mismatch')
        build = {key: observed[key] for key in ('target', 'resolved_path', 'executable_sha256')}
        build.update(release=release, system_rc='/etc/bash.bashrc',
                     **{key: item['provenance'][key] for key in ('artifact_sha256', 'definition_sha256', 'lock_sha256')})
        obsroot = args.readline / f'{release}-readline'
        proofs = {name: json.loads((obsroot / name / 'observations.json').read_text()) for name in
                  ('B2.5-maximum', 'B2.5-unicode-no-redisplay')}
        require(all(row['error'] is None for row in proofs.values()), 'failed real Readline proof')
        require(proofs['B2.5-maximum']['source_bytes'] == 786432, 'maximum not measured')
        inputs = observed['adapter_inputs']
        native_log = (args.startup / f'{release}-launch.log').read_text()
        startup_bytes = [bytes.fromhex(value) for value in re.findall(r'startup bytes ([0-9a-f]*)', native_log)]
        exports = [json.loads(line.split('evidence=', 1)[1])['state']['ExportedEnv']
                   for line in native_log.splitlines() if 'ready=' in line and 'evidence=' in line]
        require(len(startup_bytes) == len(exports) == 3 and all(value == startup_bytes[0] for value in startup_bytes)
                and all(value == exports[0] for value in exports), 'nonrepeatable native startup measurements')
        export_transform = {'unset': sorted(set(ENV) - set(exports[0])),
                            'set': {key: value for key, value in exports[0].items() if ENV.get(key) != value}}
        # Only a synthetic admission envelope, never a genuine B2 case receipt.
        result = {'measurements': {'startup_pty_base64': base64.b64encode(startup_bytes[0]).decode(),
                  'startup_export': export_transform,
                  'catchable_signals': observed['catchable_signals'],
                  'readline': {'keymap': 'emacs', 'loader_hex': '1801', 'submit_hex': '1802',
                               'readiness': True, 'no_redisplay': True, 'utf8_cursor': True,
                               'maximum_line_bytes': proofs['B2.5-maximum']['source_bytes']}},
                  'cases': dict.fromkeys(tool.case_ids(), True)}
        receipt = {'version': 1, 'candidate_sha256': tool.sha(tool.canonical(build)),
                   'inputs': {**inputs, build['resolved_path']: build['executable_sha256'],
                              '/proof/qualified.py': sha(__file__)},
                   'assets': {key: key for key in inputs}, 'adapter_inputs': inputs,
                   'target': build['target'], 'probe': ['/proof/qualified.py', '--synthetic-envelope'],
                   'repetitions': 2, 'result': result, 'result_sha256': tool.sha(tool.canonical(result))}
        table = {'version': 1, 'builds': {build['executable_sha256']: {'candidate': build, 'qualification': receipt}}}
        observations, go_outputs = [], None
        for repetition in range(2):
            current = copy.deepcopy(table)
            current['builds'][build['executable_sha256']]['qualification']['probe'].append(f'--generation={repetition}')
            generated = tool.generated(current)
            code = {path: data for path, data in generated.items() if path.endswith('.go')}
            if go_outputs is not None:
                require(go_outputs == code, 'qualification data changed runtime Go')
            go_outputs = code
            payload = args.output / f'{release}-{repetition}.json'
            payload.write_text(generated['runtime/envoy/etc/bash-builds.json'])
            generated_root = args.output / f'{release}-generated-{repetition}'
            tool.generate(current, generated_root)
            overlay = args.output / f'{release}-{repetition}-overlay.json'
            replacements = {str(root / path): str(generated_root / path) for path in code}
            replacements[str(root / 'runtime/envoy/cmd/awsh/main.go')] = str(Path(__file__).with_name('qualified-main.go.txt'))
            overlay.write_text(json.dumps({'Replace': replacements}))
            rebuilt = args.output / f'{release}-{repetition}-awsh'
            subprocess.run(['go', 'build', '-overlay', str(overlay), '-buildvcs=false',
                            '-o', str(rebuilt), './cmd/awsh'], cwd=root / 'runtime/envoy',
                           env={**os.environ, 'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': 'amd64'}, check=True)
            require(sha(rebuilt) == sha(binary), 'regenerated data changed actual assembled Awsh')
            manifest = args.output / f'{release}-{repetition}-manifest.json'
            manifest.write_text(json.dumps({'Digest': build['executable_sha256'], 'Target': build['target'],
                                           'Inputs': {**inputs, TABLE: sha(payload)}}))
            argv = prefix + ['-v', f'{payload}:{TABLE}:ro', '-v', f'{manifest}:/proof/manifest.json:ro'] + command
            actual = json.loads(subprocess.check_output(argv + ['--start'], timeout=20))
            require(actual['startup_pty_base64'] == result['measurements']['startup_pty_base64']
                    and actual['startup_export'] == result['measurements']['startup_export'], 'nonrepeatable startup')
            observations.append(actual)
        rejections = {}
        for label in ('manifest-digest', 'bash-digest', 'target', 'adapter-input', 'unqualified', 'malformed'):
            bad_manifest = json.loads(manifest.read_text())
            bad_payload = payload.read_bytes()
            if label == 'manifest-digest':
                bad_manifest['Inputs'][TABLE] = '0' * 64
            elif label == 'bash-digest':
                bad_manifest['Digest'] = '0' * 64
            elif label == 'target':
                bad_manifest['Target']['version_id'] = 'mismatched-target'
            elif label == 'adapter-input':
                bad_manifest['Inputs'][str(RUNTIME / 'etc/inputrc')] = '0' * 64
            elif label == 'unqualified':
                invalid = copy.deepcopy(table)
                invalid['builds'][build['executable_sha256']]['qualification'] = None
                bad_payload = tool.canonical(invalid)
                bad_manifest['Inputs'][TABLE] = tool.sha(bad_payload)
            else:
                bad_payload += b'{}'
                bad_manifest['Inputs'][TABLE] = tool.sha(bad_payload)
            bad_file = args.output / f'{release}-{label}.json'
            bad_file.write_bytes(bad_payload)
            bad_manifest_file = args.output / f'{release}-{label}-manifest.json'
            bad_manifest_file.write_text(json.dumps(bad_manifest))
            argv = prefix + ['-v', f'{bad_file}:{TABLE}:ro', '-v', f'{bad_manifest_file}:/proof/manifest.json:ro'] + command
            rejections[label] = json.loads(subprocess.check_output(argv + ['--reject'], timeout=20))
        require(sha(binary) == inputs[str(RUNTIME / 'bin/awsh')], 'assembled binary changed')
        require(sha(args.output / f'{release}-0.json') != sha(args.output / f'{release}-1.json'), 'table data did not change')
        results.append({'release': release, 'source_image': source, 'test_image': image,
                        'candidate': build, 'actual_adapter_inputs': inputs, 'awsh_sha256': sha(binary),
                        'startup': observations, 'rejections': rejections,
                        'synthetic_admission_envelope': True,
                        'all_case_qualification': False, 'qualified': False})
        (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(f'{len(results)} measured candidates passed repeated actual Start with stable assembled Awsh; no support admission')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--measure', action='store_true')
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--reject', action='store_true')
    parser.add_argument('--acquisition', type=Path)
    parser.add_argument('--startup', type=Path)
    parser.add_argument('--readline', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.measure:
        print(json.dumps(measure()))
    elif args.start or args.reject:
        before = assets()
        result = start(args.reject)
        require(before == assets(), 'adapter bytes changed during actual Start')
        print(json.dumps(result))
    else:
        host(args)
