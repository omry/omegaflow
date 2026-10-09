#!/usr/bin/env python3
"""B2.5 isolated checker/helper/Readline proof on pinned Reploy candidates."""

import argparse
import base64
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import socket
import subprocess
import termios
import time


RCFILE = Path('/omegaflow-runtime/etc/awsh-bashrc')
SOCKET = Path('/run/omegaflow/session/bash/helper.sock')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def receive(connection):
    data = bytearray()
    while chunk := connection.recv(65536):
        data.extend(chunk)
        if len(data) > 1048580:
            raise AssertionError('oversized helper request')
    if len(data) < 4 or int.from_bytes(data[:4], 'big') != len(data) - 4:
        raise AssertionError('invalid request length')
    if not data.endswith(b'\0'):
        raise AssertionError('missing final field delimiter')
    return bytes(data), bytes(data[4:-1]).decode().split('\0')


def reply(connection, fields):
    payload = ('\0'.join(fields) + '\0').encode()
    connection.sendall(len(payload).to_bytes(4, 'big') + payload)


def drain(master, raw):
    while select.select([master], [], [], 0)[0]:
        try:
            data = os.read(master, 65536)
        except OSError:
            break
        if not data:
            break
        raw.extend(data)


def descendants(pid):
    children = []
    for entry in Path('/proc').iterdir():
        if entry.name.isdecimal():
            try:
                stat = (entry / 'stat').read_text()
                fields = stat[stat.rindex(')') + 2:].split()
                if int(fields[1]) == pid:
                    children.append((int(entry.name), fields[0]))
            except (FileNotFoundError, ProcessLookupError):
                pass
    return children


def readline(case, directory):
    """Actual interactive Bash + actual helper; only its Awsh peer is synthetic.

    B2.6 owns START_RELEASED. Its test-only no-op replacement admits no signal,
    private/public start or completion-cleanup claim from this proof.
    """
    directory.mkdir()
    source = case.get('source', '#')
    if case.get('maximum'):
        source = '#' * 786432
    status = case.get('status', 0)
    history, editing = case.get('history', 'off'), case.get('editing', 'emacs')
    script = RCFILE.read_text()
    script += '\n__OMEGAFLOW_AWSH_START_RELEASED() { builtin return 0; }\n'
    script += 'builtin readonly -f __OMEGAFLOW_AWSH_START_RELEASED\n'
    script += f'builtin set {"-H" if history == "on" else "+H"} -o {editing}\n'
    script += case.get('setup', '') + '\n'
    script += f'__OMEGAFLOW_AWSH_RETURN {status}\n'
    fixture = directory / 'rcfile'
    fixture.write_text(script)
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    master, slave = pty.openpty()
    raw, requests, terminal_states, error, proc = bytearray(), [], [], None, None
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(SOCKET))
    listener.listen(4)
    listener.settimeout(5)
    try:
        proc = subprocess.Popen(
            ['/bin/bash', '--noprofile', '--rcfile', str(fixture), '-i'],
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        os.close(slave)
        slave = None

        def prompt(expected):
            workload = None
            for phase in ['prompt_state', 'prompt_ready']:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(5)
                    data, fields = receive(connection)
                    (directory / f'{len(requests)}-{phase}.request').write_bytes(data)
                    requests.append(fields)
                    if fields[:2] != ['awsh-helper-v1', phase]:
                        raise AssertionError('unexpected helper phase')
                    if phase == 'prompt_state':
                        if len(fields) != 8 or fields[2:5] != expected:
                            raise AssertionError(f'prompt state {fields[2:5]} != {expected}')
                        if not Path(fields[5]).is_absolute() or not isinstance(json.loads(fields[7]), dict):
                            raise AssertionError('invalid prompt snapshot')
                        workload = termios.tcgetattr(master)
                    elif len(fields) != 2:
                        raise AssertionError('invalid readiness arity')
                    else:
                        prepared = copy.deepcopy(workload)
                        prepared[3] = (prepared[3] | termios.ICANON) & ~termios.ECHO
                        termios.tcsetattr(master, termios.TCSANOW, prepared)
                        observed = termios.tcgetattr(master)
                        if observed != prepared:
                            raise AssertionError('echo-off preparation readback mismatch')
                        terminal_states.append({'workload': workload, 'prepared': observed})
                    reply(connection, ['awsh-helper-v1', 'accepted'])

        prompt([str(status), history, editing])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            attrs = termios.tcgetattr(master)
            if not attrs[3] & (termios.ICANON | termios.ECHO):
                break
            time.sleep(.005)
        else:
            raise AssertionError('no Readline termios boundary')
        drain(master, raw)
        before = len(raw)
        os.write(master, b'\x18\x02')
        if not case.get('helper_fault'):
            connection, _ = listener.accept()
        else:
            connection = None
        if connection is not None:
            with connection:
                connection.settimeout(5)
                data, fields = receive(connection)
                (directory / 'source.request').write_bytes(data)
                requests.append(fields)
                if fields != ['awsh-helper-v1', 'source']:
                    raise AssertionError('source request is not argument-free')
                fields = ['awsh-helper-v1', 'source', 'op1', str(status), history, editing,
                          'pty', '', '', source]
                fault = case.get('fault')
                if fault == 'trailing-reply':
                    reply(connection, fields)
                    connection.sendall(b'!')
                elif fault == 'partial-reply':
                    connection.sendall(b'\0\0\0\x10x')
                elif fault == 'empty-reply':
                    pass
                else:
                    reply(connection, fields)
        if case.get('fault'):
            deadline = time.monotonic() + 5
            stopped = None
            while time.monotonic() < deadline and proc.poll() is None:
                drain(master, raw)
                stopped = next((pid for pid, state in descendants(proc.pid) if state == 'T'), None)
                if stopped:
                    break
                time.sleep(.01)
            if stopped is None:
                raise AssertionError('loader failure returned instead of fail-stop')
            os.kill(stopped, signal.SIGCONT)
            time.sleep(.03)
            if (stopped, 'T') not in descendants(proc.pid):
                raise AssertionError('fail-stop helper returned after SIGCONT')
        else:
            prompt([str(case.get('result', 0)), case.get('final_history', history),
                    case.get('final_editing', editing)])
            time.sleep(.03)
            drain(master, raw)
            output = bytes(raw[before:])
            for expected in case.get('output', []):
                if expected.encode() not in output:
                    raise AssertionError(f'missing authored output {expected!r}')
            for forbidden in case.get('absent', []):
                if forbidden.encode() in output:
                    raise AssertionError(f'unexpected authored output {forbidden!r}')
            if b'__OMEGAFLOW_AWSH_' in output or b'SOURCE_MUST_NOT_REDISPLAY' in output:
                raise AssertionError('source/frame redisplayed')
            if b'bash:' in output or b'command not found' in output:
                raise AssertionError('unexpected shell diagnostic')
    except BaseException as caught:
        error = str(caught)
        raise
    finally:
        if proc is not None:
            for pid, _ in descendants(proc.pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.kill()
            proc.wait(timeout=5)
        if slave is not None:
            os.close(slave)
        drain(master, raw)
        os.close(master)
        listener.close()
        SOCKET.unlink(missing_ok=True)
        (directory / 'terminal.raw').write_bytes(raw)
        save(directory / 'observations.json', {
            'case_id': case['id'], 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'source_bytes': len(source.encode()), 'rcfile_sha256': sha(fixture),
            'requests': requests, 'raw_base64': base64.b64encode(raw).decode(),
            'terminal_states': [
                {name: attrs[:6] + [[value[0] if isinstance(value, bytes) else value
                                    for value in attrs[6]]]
                 for name, attrs in state.items()} for state in terminal_states],
            'error': error, 'qualified': False, 'start_release': 'test-only no-op',
            'completion': 'startup-form peer; no descendant cleanup proof'})


MUTATIONS = {
    'status-restoration': ('__OMEGAFLOW_AWSH_RETURN() { builtin return "$1"; }',
                           '__OMEGAFLOW_AWSH_RETURN() { builtin return 0; }', 'B2.5-preceding-status'),
    'loader-binding': ('"\\C-x\\C-a":__OMEGAFLOW_AWSH_LOAD', '"\\C-x\\C-c":__OMEGAFLOW_AWSH_LOAD', 'B2.5-unicode-no-redisplay'),
    'history-restoration': ('__OMEGAFLOW_AWSH_SET -H', '__OMEGAFLOW_AWSH_SET +H', 'B2.5-history-vi'),
    'readonly-detection': ('(builtin trap - EXIT; READLINE_LINE= READLINE_POINT=0) >/dev/null 2>&1 || __OMEGAFLOW_AWSH_FAIL_STOP',
                           ':', 'B2.5-readonly-line'),
}
MUTATION_FAILURES = {
    'status-restoration': "prompt state ['0', 'off', 'emacs'] != ['17', 'off', 'emacs']",
    'loader-binding': 'TimeoutError',
    'history-restoration': "prompt state ['1', 'off', 'vi'] != ['0', 'on', 'vi']",
    'readonly-detection': 'loader failure returned instead of fail-stop',
}


def worker(output, case_id=None):
    digest = os.environ['OMEGAFLOW_CANDIDATE_DIGEST']
    if sha(Path('/bin/bash')) != digest:
        raise ValueError('candidate executable identity mismatch')
    cases = json.loads(Path(__file__).with_name('cases.json').read_text())
    if case_id:
        cases = [case for case in cases if case['id'] == case_id]
        if len(cases) != 1:
            raise ValueError('unknown mutation case')
    else:
        cases = [case for case in cases if not case.get('helper_fault')]
    for case in cases:
        readline(case, output / case['id'])
        for variant in case.get('variants', []):
            expanded = {**case, **variant, 'id': case['id'] + '-' + variant['id']}
            readline(expanded, output / expanded['id'])
    print(f'{len(cases)} real Readline cases passed with '
          f'{sum(len(case.get("variants", [])) for case in cases)} attribute variants')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquisition', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--case')
    parser.add_argument('--mutations', action='store_true')
    args = parser.parse_args()
    if args.worker:
        return worker(args.output, args.case)
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('launch_proof', root / 'tests/envoy-bash-launch/run.py')
    tools = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tools)
    candidates = tools._validate_acquisition(json.loads(args.acquisition.read_text()))
    images = [tools._verify_image_identity(candidate) for candidate in candidates]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    context = output / 'context'
    (context / 'bin').mkdir(parents=True)
    (context / 'etc').mkdir()
    for name in ['awsh-bashrc', 'inputrc']:
        shutil.copyfile(root / 'runtime/envoy/etc' / name, context / 'etc' / name)
    for name in ['run.py', 'cases.json']:
        shutil.copyfile(Path(__file__).with_name(name), context / 'etc' / name)
    env = {**os.environ, 'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': 'amd64'}
    subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(context / 'bin/awsh'), './cmd/awsh'],
                   cwd=root / 'runtime/envoy', env=env, check=True)
    shutil.copyfile(context / 'bin/awsh', context / 'bin/awsh-real')
    (context / 'bin/awsh-real').chmod(0o555)
    subprocess.run(['go', 'test', '-c', '-cover', '-covermode=atomic', '-o',
                    str(context / 'bin/submission.test'), './awsh/submission'],
                   cwd=root / 'runtime/envoy', env=env, check=True)
    (context / 'Dockerfile').write_text(
        'ARG CANDIDATE\nFROM ${CANDIDATE}\n'
        'COPY bin /omegaflow-runtime/bin\nCOPY etc /omegaflow-runtime/etc\n'
        'RUN rm /etc/bash.bashrc && mkdir -p /run/omegaflow/session '
        '/omegaflow-runtime/share/terminfo /omegaflow-runtime/lib/locale '
        '&& cp -a /usr/share/terminfo/. /omegaflow-runtime/share/terminfo/ '
        '&& if test -d /lib/terminfo; then cp -a /lib/terminfo/. /omegaflow-runtime/share/terminfo/; fi '
        '&& cp -a /usr/lib/locale/. /omegaflow-runtime/lib/locale/ '
        '&& chmod -R a-w /omegaflow-runtime\nWORKDIR /\n')
    save(output / 'inputs.json', {
        'acquisition_sha256': sha(args.acquisition),
        'assets': {str(path.relative_to(context)): sha(path) for path in sorted(context.rglob('*')) if path.is_file()},
        'launch_input_rules_sha256': sha(root / 'tests/envoy-bash-launch/run.py')})
    results = []
    try:
        for candidate, image in zip(candidates, images):
            release = candidate['release']
            artifacts = tools._candidate_artifacts(output, release)
            with artifacts['build_log'].open('w') as log:
                subprocess.run(['docker', 'build', '--build-arg', f'CANDIDATE={image}',
                                '--iidfile', str(artifacts['iidfile']), str(context)],
                               env={**os.environ, 'DOCKER_BUILDKIT': '0'},
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            iid = artifacts['iidfile'].read_text().strip()
            if not tools._IMAGE_DIGEST_RE.fullmatch(iid):
                raise ValueError('invalid derived image identity')
            artifacts['coverage'].mkdir()
            command = ['docker', 'run', '--rm', '--network=none', '-e',
                       f"OMEGAFLOW_CANDIDATE_DIGEST={candidate['observed']['executable_sha256']}"]
            with artifacts['launch_log'].open('w') as log:
                subprocess.run(command + ['-v', f"{artifacts['coverage']}:/proof-coverage",
                    '--entrypoint=/omegaflow-runtime/bin/submission.test', iid, '-test.v',
                    '-test.timeout=120s', '-test.gocoverdir=/proof-coverage',
                    '-test.run=^Test(CheckFailsClosedWithoutQualification|SelectedBashChecker|CheckerKeepsDeadlineAndIntegrityErrors|CheckerIgnoresInheritedParserEnvironment|CheckerRejectsDescriptorInheritance)$'],
                    stdout=log, stderr=subprocess.STDOUT, timeout=135, check=True)
            proof = output / f'{release}-readline'
            proof.mkdir()
            environment = ['-e', 'INPUTRC=/omegaflow-runtime/etc/inputrc', '-e', 'TERM=xterm-256color',
                           '-e', 'TERMINFO=/omegaflow-runtime/share/terminfo',
                           '-e', 'TERMINFO_DIRS=/omegaflow-runtime/share/terminfo',
                           '-e', 'LC_ALL=C.UTF-8', '-e', 'LANG=C.UTF-8',
                           '-e', 'LOCPATH=/omegaflow-runtime/lib/locale', '-e', 'HISTFILE=']
            with (output / f'{release}-readline.log').open('w') as log:
                subprocess.run(command + environment + ['-v', f'{proof}:/proof',
                    '--entrypoint=/usr/bin/python3', iid, '/omegaflow-runtime/etc/run.py',
                    '--worker', '--output=/proof'], stdout=log, stderr=subprocess.STDOUT,
                    timeout=120, check=True)
            row = {'release': release, 'source_image': image, 'test_image': iid,
                   'executable_sha256': candidate['observed']['executable_sha256'],
                   'checker': 'passed', 'readline': 'passed', 'qualified': False, 'mutations': {}}
            results.append(row)
            for case in json.loads(Path(__file__).with_name('cases.json').read_text()):
                if not case.get('helper_fault'):
                    continue
                case_id = case['id']
                fault = case['helper_fault']
                fixture = output / f'{release}-{case_id}.helper'
                fixture.write_text('#!/bin/bash\nif [[ $3 == source ]]; then\n'
                    f"builtin printf '%s' {shlex.quote(fault['stdout'])}\n"
                    f"builtin exit {fault['status']}\nfi\n"
                    'exec /omegaflow-runtime/bin/awsh-real "$@"\n')
                fixture.chmod(0o555)
                directory = output / f'{release}-{case_id}'
                directory.mkdir()
                log_path = output / f'{release}-{case_id}.log'
                with log_path.open('w') as log:
                    subprocess.run(command + environment + ['-v', f'{directory}:/proof',
                        '-v', f'{fixture}:/omegaflow-runtime/bin/awsh:ro',
                        '--entrypoint=/usr/bin/python3', iid, '/omegaflow-runtime/etc/run.py',
                        '--worker', '--output=/proof', f'--case={case_id}'],
                        stdout=log, stderr=subprocess.STDOUT, timeout=25, check=True)
                row.setdefault('loader_faults', {})[case_id] = {
                    'helper_sha256': sha(fixture), 'log': str(log_path), 'passed': True}
            if args.mutations:
                for name, (before, after, case_id) in MUTATIONS.items():
                    fixture = output / f'mutation-{name}.bash'
                    original = (context / 'etc/awsh-bashrc').read_text()
                    if original.count(before) != 1:
                        raise ValueError(f'nonunique mutation {name}')
                    fixture.write_text(original.replace(before, after))
                    directory = output / f'{release}-mutation-{name}'
                    directory.mkdir()
                    log_path = output / f'{release}-mutation-{name}.log'
                    with log_path.open('w') as log:
                        run = subprocess.run(command + environment + ['-v', f'{directory}:/proof',
                            '-v', f'{fixture}:{RCFILE}:ro', '--entrypoint=/usr/bin/python3', iid,
                            '/omegaflow-runtime/etc/run.py', '--worker', '--output=/proof',
                            f'--case={case_id}'], stdout=log, stderr=subprocess.STDOUT, timeout=25)
                    row['mutations'][name] = {'returncode': run.returncode, 'rcfile_sha256': sha(fixture),
                                              'case_id': case_id, 'log': str(log_path)}
                    if run.returncode == 0:
                        raise AssertionError(f'uncovered mutation {name}')
                    if MUTATION_FAILURES[name] not in log_path.read_text():
                        raise AssertionError(f'unrelated mutation failure {name}')
    finally:
        save(output / 'results.json', results)
    print(f'{len(results)} pinned candidates passed B2.5 isolated proof: {output}')


if __name__ == '__main__':
    main()
