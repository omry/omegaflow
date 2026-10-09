#!/usr/bin/env python3
"""B2.4 candidate-only fixed-rcfile proof; never support admission."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pty
import re
import shlex
import shutil
import signal
import socket
import subprocess
import termios
import threading
import time

RCFILE = Path('/omegaflow-runtime/etc/awsh-bashrc')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def launch_tools(root):
    spec = importlib.util.spec_from_file_location('launch_proof', root / 'tests/envoy-bash-launch/run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Each fault removes an independently owned behavior from an isolated asset
# copy. A failing candidate test demonstrates coverage; no fault enters the
# checkout, production configuration, or supported build table.
MUTATIONS = {
    'trap-preflight': ('if __OMEGAFLOW_AWSH_TRAP_PREFLIGHT "$@" 3>&-; then',
                       'if ((1)); then', 'B1-C012-contract', 'stopped helper: Bash returned'),
    'enable-preflight': ('if __OMEGAFLOW_AWSH_ENABLE_PREFLIGHT "$@" 3>&-; then',
                         'if ((1)); then', 'B1-C020-contract', 'stopped helper: Bash returned'),
    'numeric-canonicalization': ('if [[ $__OMEGAFLOW_AWSH_SIGNAL == *[!0-9]* ]]; then',
                                'if ((0)); then', 'B1-C020-selected-numeric-INT-direct',
                                'stopped helper: Bash returned'),
    'trap-grammar': ('if builtin trap -p "$__OMEGAFLOW_AWSH_SPEC" >/dev/null 2>&1; then',
                     'if ((1)); then', 'B2.4-trap-native-163', 'ordinary request did not return'),
    'posix-reservation': ('builtin readonly POSIXLY_CORRECT\n', '',
                          'B1-C013-contract', 'POSIX reservation did not survive'),
    'canonical-options': ('builtin set +H +e +E +T +o posix +n +v +x\n', '',
                          'B2.4-canonical-entry', 'canonical histexpand is not off'),
    'canonical-grammar': ('builtin shopt -u expand_aliases extdebug extglob\n', '',
                          'B2.4-canonical-entry', 'missing canonical state'),
    'readonly-namespace': ('builtin readonly -f __OMEGAFLOW_AWSH_FAIL_STOP',
                           '# builtin readonly -f __OMEGAFLOW_AWSH_FAIL_STOP',
                           'B2.4-readonly-namespace', 'is no longer readonly'),
    'fail-stop-fallback': ('while ((1)); do ((1)); done', 'builtin return 0',
                           'B2.4-fail-stop-fallback', 'returning helper released Bash'),
    'absolute-gate-helper': ('/omegaflow-runtime/bin/awsh bash-helper --socket=/run/omegaflow/session/bash/helper.sock gate "$2"',
                              'awsh-external bash-helper --socket=/run/omegaflow/session/bash/helper.sock gate "$2"',
                              'B2.4-awsh-hostile-path', 'expected positive output'),
    'prompt-local-dependency': ('    __OMEGAFLOW_AWSH_STATUS=$? __OMEGAFLOW_AWSH_HISTORY=off __OMEGAFLOW_AWSH_EDITING=emacs',
                                '    builtin local __OMEGAFLOW_AWSH_STATUS=$? __OMEGAFLOW_AWSH_HISTORY=off __OMEGAFLOW_AWSH_EDITING=emacs',
                                'B2.4-prompt-disabled-local', 'prompt peer did not complete'),
    'trap-xtrace-leak': ('} 3>&2 2>/dev/null\n\n__OMEGAFLOW_AWSH_ENABLE_PREFLIGHT',
                         '} 3>&2\n\n__OMEGAFLOW_AWSH_ENABLE_PREFLIGHT',
                         'B2.4-xtrace-trap', 'native behavior differs'),
    'enable-xtrace-leak': ('} 3>&2 2>/dev/null\n\n# The gate transport',
                           '} 3>&2\n\n# The gate transport',
                           'B2.4-xtrace-enable', 'native behavior differs'),
    'nocasematch-builtin-name': ('        __OMEGAFLOW_AWSH_NAME=${__OMEGAFLOW_AWSH_ARGS[__OMEGAFLOW_AWSH_INDEX]}',
                                 '        case ${__OMEGAFLOW_AWSH_ARGS[__OMEGAFLOW_AWSH_INDEX]} in KILL) builtin return 1 ;; esac\n'
                                 '        __OMEGAFLOW_AWSH_NAME=${__OMEGAFLOW_AWSH_ARGS[__OMEGAFLOW_AWSH_INDEX]}',
                                 'B2.4-nocasematch-uppercase-KILL', 'ordinary request did not return'),
    'nocasematch-enable-option': ('            if [[ -z ${__OMEGAFLOW_AWSH_LETTER#p} ]]; then',
                                  '            case $__OMEGAFLOW_AWSH_LETTER in N) builtin return 1 ;; esac\n'
                                  '            if [[ -z ${__OMEGAFLOW_AWSH_LETTER#p} ]]; then',
                                  'B2.4-nocasematch-option-N', 'ordinary request did not return'),
    'nocasematch-prompt-history': ('    [[ ${-#*H} != "$-" ]] && __OMEGAFLOW_AWSH_HISTORY=on',
                                   '    [[ $- == *H* ]] && __OMEGAFLOW_AWSH_HISTORY=on',
                                   'B2.4-nocasematch-prompt-history-off', 'saved prompt state was lost'),
    'nocasematch-gate-token': ('    if [[ $# == 2 && -z ${1#gate} && -n $2 ]]; then',
                               '    if [[ $# == 2 && $1 == gate && -n $2 ]]; then',
                               'B2.4-nocasematch-gate-token', 'expected positive output'),
}


def shell(command, rcfile=RCFILE, startup=False):
    # A private controlling terminal permits genuine interactive Bash behavior,
    # notably readonly-assignment errors that end a noninteractive shell.
    master, slave = pty.openpty()
    try:
        invocation = ['/bin/bash', '--noprofile', '--rcfile', str(rcfile), '-i']
        if not startup:
            invocation += ['-c',
             'CHLD_NUMBER=$(builtin kill -l CHLD); INT_NUMBER=$(builtin kill -l INT); '
             'USR2_NUMBER=$(builtin kill -l USR2); DEBUG_NUMBER=$(builtin kill -l DEBUG); '
             'ERR_NUMBER=$(builtin kill -l ERR); RETURN_NUMBER=$(builtin kill -l RETURN)\n' + command]
        proc = subprocess.Popen(invocation,
            stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)
    proc.proof_master = master
    return proc


def completed(command, rcfile=RCFILE, startup=False):
    proc = shell(command, rcfile, startup)
    try:
        out, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise AssertionError('ordinary request did not return')
    finally:
        os.close(proc.proof_master)
    return {'status': proc.returncode, 'stdout': out.decode(), 'stderr': err.decode()}


def process_state(pid):
    try:
        data = Path(f'/proc/{pid}/stat').read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return data[data.rindex(')') + 2:].split()[0]


def prompt(case, directory):
    """Real helper exchanges with a deterministic startup peer, not cleanup."""
    directory.mkdir()
    path = Path('/run/omegaflow/session/bash/helper.sock')
    path.parent.mkdir(parents=True, exist_ok=True)
    requests, errors = [], []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen(2)
        listener.settimeout(5)

        def peer():
            try:
                for phase in ['prompt_state', 'prompt_ready']:
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(5)
                        data = bytearray()
                        while chunk := connection.recv(4096):
                            data.extend(chunk)
                            if len(data) > 1048580:
                                raise AssertionError('oversized prompt request')
                        if len(data) < 4 or int.from_bytes(data[:4], 'big') != len(data) - 4:
                            raise AssertionError('invalid prompt request length')
                        payload = bytes(data[4:])
                        (directory / (phase + '.request')).write_bytes(data)
                        if not payload.endswith(b'\0'):
                            raise AssertionError('unterminated prompt request')
                        fields = payload[:-1].decode().split('\0')
                        if fields[:2] != ['awsh-helper-v1', phase]:
                            raise AssertionError('wrong prompt helper phase')
                        if phase == 'prompt_state':
                            if len(fields) != 8 or fields[2:5] != case.get('prompt_fields', ['1', 'on', 'emacs']):
                                raise AssertionError('saved prompt state was lost')
                            if not Path(fields[5]).is_absolute() or not isinstance(json.loads(fields[7]), dict):
                                raise AssertionError('invalid captured prompt state')
                        elif len(fields) != 2:
                            raise AssertionError('wrong startup readiness arity')
                        requests.append(fields)
                        reply = b'awsh-helper-v1\0accepted\0'
                        connection.sendall(len(reply).to_bytes(4, 'big') + reply)
            except BaseException as error:
                errors.append(str(error))

        thread = threading.Thread(target=peer)
        thread.start()
        observed, completion_error = None, None
        try:
            # bind -x followed by set -o emacs can crash selected Bash under
            # -i -c, which skips Readline initialization. Exercise this prompt
            # hook in the real startup form, without typing source into a PTY.
            fixture = directory / 'prompt.bashrc'
            fixture.write_text(RCFILE.read_text() + '\n' + case['command'] + '\nbuiltin exit "$?"\n')
            observed = completed('', fixture, startup=True)
        except AssertionError as error:
            completion_error = str(error)
        finally:
            thread.join(6)
            path.unlink()
        write_json(directory / 'observations.json',
                   {'requests': requests, 'observed': observed, 'peer_errors': errors,
                    'completion_error': completion_error})
        if thread.is_alive() or errors or len(requests) != 2:
            raise AssertionError(f'prompt peer did not complete: {errors}')
        if completion_error:
            raise AssertionError(completion_error)
    if observed != {'status': 0, 'stdout': case.get('expected_stdout', 'PROMPT_OK\n'), 'stderr': ''}:
        raise AssertionError(f'disabled local broke prompt hook: {observed!r}')
    proof = {'requests': requests, 'observed': observed, 'qualified': False}
    write_json(directory / 'observations.json', proof)
    return proof


def children(pid):
    try:
        return [int(x) for x in Path(f'/proc/{pid}/task/{pid}/children').read_text().split()]
    except (FileNotFoundError, ProcessLookupError):
        return []


def wait_for(predicate, proc, label):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        if proc.poll() is not None:
            raise AssertionError(f'{label}: Bash returned status {proc.returncode}')
        time.sleep(.001)
    raise AssertionError(f'{label}: observation deadline expired')


def fatal(case, directory):
    directory.mkdir()
    initial_traps, final_traps = directory / 'initial.traps', directory / 'final.traps'
    initial_builtins, final_builtins = directory / 'initial.builtins', directory / 'final.builtins'
    done = directory / 'snapshot.done'
    snapshot = (f'builtin trap -p > {shlex.quote(str(final_traps))}; '
                f'builtin enable -a > {shlex.quote(str(final_builtins))}; '
                f'builtin enable printf; builtin printf DONE > {shlex.quote(str(done))}')
    command = (case.get('setup', '') + '\n' + f'builtin trap {shlex.quote(snapshot)} WINCH\n'
               f'builtin trap -p > {shlex.quote(str(initial_traps))}\n'
               f'builtin enable -a > {shlex.quote(str(initial_builtins))}\n'
               + case['command'] + '\nbuiltin enable printf; builtin printf RETURNED\n')
    proc = shell(command)
    observations = []
    helper = None
    try:
        wait_for(initial_builtins.exists, proc, 'pre-request snapshot')
        if case['mode'] == 'fallback':
            # Fault-only bind mount makes the fixed command return. The fallback
            # must stay alive and produce no authored continuation/output.
            time.sleep(.05)
            if proc.poll() is not None:
                raise AssertionError('returning helper released Bash')
            observations.append({'phase': 'returning-helper', 'time_ns': time.monotonic_ns(),
                                 'bash_state': process_state(proc.pid)})
        else:
            helper = wait_for(lambda: next((pid for pid in children(proc.pid)
                               if process_state(pid) == 'T'), None), proc, 'stopped helper')
            if os.readlink(f'/proc/{helper}/exe') != '/omegaflow-runtime/bin/awsh':
                raise AssertionError('stopped child is not the fixed manifested helper')
            for repeat in range(3):
                os.kill(helper, signal.SIGCONT)
                time.sleep(.01)
                wait_for(lambda: process_state(helper) == 'T', proc, 'helper restop')
                observations.append({'phase': 'restop', 'repeat': repeat,
                                     'time_ns': time.monotonic_ns(), 'helper_pid': helper,
                                     'helper_state': process_state(helper)})
            os.kill(helper, signal.SIGKILL)
            # A returned command must enter the same non-returning fallback.
            wait_for(lambda: process_state(helper) is None, proc, 'helper reap')
        os.kill(proc.pid, signal.SIGWINCH)
        wait_for(done.exists, proc, 'post-refusal snapshot')
        if initial_traps.read_bytes() != final_traps.read_bytes():
            raise AssertionError('trap request partially mutated state before refusal')
        if initial_builtins.read_bytes() != final_builtins.read_bytes():
            raise AssertionError('enable request partially mutated state before refusal')
        if proc.poll() is not None:
            raise AssertionError('fail-stop returned')
    finally:
        for pid in children(proc.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.kill()
        out, err = proc.communicate(timeout=5)
        os.close(proc.proof_master)
        (directory / 'stdout').write_bytes(out)
        (directory / 'stderr').write_bytes(err)
        write_json(directory / 'schedule.json', observations)
    if 'expected_stderr' in case and err.decode() != case['expected_stderr']:
        raise AssertionError(f'fail-stop trace differs: {err!r}')
    if out or (err and not case.get('allow_stderr') and 'expected_stderr' not in case):
        raise AssertionError(f'fail-stop output {out!r} / {err!r}')
    return {'stopped_helper': helper, 'observations': observations, 'returned': False,
            'partial_mutation': False, 'stdout': out.decode(), 'stderr': err.decode()}


def worker(args):
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    digest = os.environ['OMEGAFLOW_CANDIDATE_DIGEST']
    if sha(Path('/bin/bash')) != digest:
        raise ValueError('resolved candidate executable digest mismatch')
    corpus = json.loads(Path('/proof/cases.json').read_text())
    ids = [c['id'] for c in corpus['cases']]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', x) for x in ids):
        raise ValueError('invalid or duplicate case identity')
    # Ordinary native baseline removes only mediation from a test-only copy;
    # all canonical inputs, POSIX reservation and awsh identity stay identical.
    native = output / 'native.bashrc'
    text = RCFILE.read_text()
    native.write_text(text.replace(' trap enable awsh\n', ' awsh\n') + '\nbuiltin unset -f trap enable\n')
    cases = [c for c in corpus['cases'] if not args.case or c['id'] == args.case]
    cases = [c for c in cases if (c['mode'] == 'fallback') == args.returning_helper
             and (c['mode'] == 'helper-probe') == args.helper_probe]
    if not cases:
        raise ValueError('no selected proof case')
    results = []
    try:
        for case in cases:
            directory = output / case['id']
            result = {'id': case['id'], 'mode': case['mode'], 'command': case['command']}
            results.append(result)
            if case['mode'] == 'prompt':
                result['proof'] = prompt(case, directory)
            elif case['mode'] in ['fatal', 'fallback']:
                result['proof'] = fatal(case, directory)
            else:
                command = case.get('setup', '') + '\n' + case['command']
                if case.get('after') == 'posix':
                    command += '\nbuiltin printf "ATTEMPT_STATUS=%s\\n" "$?"; '
                    command += '[[ ! -o posix && ! -v POSIXLY_CORRECT ]]; builtin printf "POSIX_DISABLED=%s\\n" "$?"'
                observed = completed(command)
                baseline_file = RCFILE if 'readonly' in case['id'] or case['id'] in [
                    'B1-C021-contract', 'B1-C021-redefine-awsh', 'B1-C021-unset-awsh'] else native
                expected = completed(command, baseline_file)
                result['proof'] = {'observed': observed, 'native': expected}
                directory.mkdir()
                write_json(directory / 'observations.json', result['proof'])
                if case['mode'] == 'canonical':
                    if observed['status']:
                        raise AssertionError(f'canonical setup failed: {observed!r}')
                    lines = [' '.join(line.split()) for line in observed['stdout'].splitlines()]
                    values = dict(line.split() for line in observed['stdout'].splitlines()
                                  if len(line.split()) == 2)
                    for name in ['histexpand','errexit','errtrace','functrace','posix','noexec','verbose','xtrace']:
                        if values.get(name) != 'off':
                            raise AssertionError(f'canonical {name} is not off')
                    if values.get('monitor') != 'on' or values.get('emacs') != 'on':
                        raise AssertionError('canonical job control/editing state')
                    if any(line.startswith('trap --') for line in lines):
                        raise AssertionError('canonical reserved traps are not unset')
                    for line in ['shopt -u expand_aliases','shopt -u extdebug','shopt -u extglob',
                                 'shopt -s interactive_comments','declare -r POSIXLY_CORRECT',
                                 'declare -fr trap','declare -fr enable','declare -fr awsh']:
                        if line not in lines:
                            raise AssertionError(f'missing canonical state {line}')
                    if observed['stderr']:
                        raise AssertionError(observed['stderr'])
                elif observed != expected:
                    raise AssertionError(f'native behavior differs: {observed!r} vs {expected!r}')
                if case.get('expected_stdout') is not None and observed['stdout'] != case['expected_stdout']:
                    raise AssertionError(f"expected positive output {case['expected_stdout']!r}")
                if 'readonly' in case['id']:
                    for name in ['trap', 'enable', 'awsh', '__OMEGAFLOW_AWSH_FAIL_STOP']:
                        if f'declare -fr {name}' not in observed['stdout'].splitlines():
                            raise AssertionError(f'{name} is no longer readonly')
                if case.get('after') == 'posix' and 'POSIX_DISABLED=0' not in observed['stdout']:
                    raise AssertionError('POSIX reservation did not survive native request')
                if case.get('static_discrepancy'):
                    result['static_discrepancy'] = case['static_discrepancy']
                    result['static_expectation_status'] = 'pending-owning-correction'
            result['passed'] = True
            write_json(directory / 'result.json', result)
            print('PASS', case['id'], flush=True)
    finally:
        write_json(output / 'results.json', {'qualified': False, 'executable_sha256': digest,
                   'rcfile_sha256': sha(RCFILE), 'cases': results, 'pending': corpus['pending']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquisition', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--case')
    parser.add_argument('--returning-helper', action='store_true')
    parser.add_argument('--helper-probe', action='store_true')
    parser.add_argument('--mutations', action='store_true')
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if not args.acquisition:
        parser.error('--acquisition is required')
    root = Path(__file__).resolve().parents[2]
    tools = launch_tools(root)
    candidates = tools._validate_acquisition(json.loads(args.acquisition.read_text()))
    images = [tools._verify_image_identity(c) for c in candidates]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    context = output / 'context'
    (context / 'bin').mkdir(parents=True)
    (context / 'etc').mkdir()
    (context / 'proof').mkdir()
    shutil.copyfile(root / 'runtime/envoy/etc/awsh-bashrc', context / 'etc/awsh-bashrc')
    shutil.copyfile(Path(__file__), context / 'proof/run.py')
    shutil.copyfile(Path(__file__).with_name('cases.json'), context / 'proof/cases.json')
    env = {**os.environ, 'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': 'amd64'}
    subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(context / 'bin/awsh'), './cmd/awsh'],
                   cwd=root / 'runtime/envoy', env=env, check=True)
    (context / 'Dockerfile').write_text('ARG CANDIDATE\nFROM ${CANDIDATE}\n'
        'COPY bin /omegaflow-runtime/bin\nCOPY etc /omegaflow-runtime/etc\nCOPY proof /proof\n'
        'RUN rm /etc/bash.bashrc && mkdir /proof-output && chmod -R a-w /omegaflow-runtime /proof\n')
    returning = output / 'returning-helper'
    returning.write_text('#!/bin/bash\nexit 0\n')
    returning.chmod(0o555)
    write_json(output / 'inputs.json', {'acquisition_sha256': sha(args.acquisition),
               'assets': {str(p.relative_to(context)): sha(p) for p in sorted(context.rglob('*')) if p.is_file()},
               'launch_rules_sha256': sha(root / 'tests/envoy-bash-launch/run.py')})
    results = []
    try:
        for candidate, image in zip(candidates, images):
            release = candidate['release']
            iidfile = output / f'{release}-image-id'
            with (output / f'{release}-build.log').open('w') as log:
                subprocess.run(['docker', 'build', '--build-arg', f'CANDIDATE={image}',
                                '--iidfile', str(iidfile), str(context)],
                               env={**os.environ, 'DOCKER_BUILDKIT': '0'},
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            iid = iidfile.read_text().strip()
            if not tools._IMAGE_DIGEST_RE.fullmatch(iid):
                raise ValueError('invalid derived image identity')
            modes = [(False, False), (True, False), (False, True)]
            if args.case:
                mode = next(c['mode'] for c in json.loads((context / 'proof/cases.json').read_text())['cases']
                            if c['id'] == args.case)
                modes = [(mode == 'fallback', mode == 'helper-probe')]
            for fallback, helper_probe in modes:
                name = f'{release}-' + ('fallback' if fallback else 'helper-probe' if helper_probe else 'reserved-state')
                command = ['docker', 'run', '--rm', '--network=none', '-v',
                           f'{output}:/proof-output', '-e',
                           f"OMEGAFLOW_CANDIDATE_DIGEST={candidate['observed']['executable_sha256']}",
                           '--entrypoint=/usr/bin/python3']
                if fallback or helper_probe:
                    command += ['-v', f'{returning}:/omegaflow-runtime/bin/awsh:ro']
                command += [iid, '/proof/run.py', '--worker', '--output', f'/proof-output/{name}']
                if fallback:
                    command += ['--returning-helper']
                if helper_probe:
                    command += ['--helper-probe']
                if args.case:
                    command += ['--case', args.case]
                with (output / f'{name}.log').open('w') as log:
                    run = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600)
                results.append({'release': release, 'source_image': image, 'test_image': iid,
                                'returning_helper_fault': fallback, 'helper_token_probe': helper_probe, 'returncode': run.returncode,
                                'qualified': False, 'evidence': name})
                if run.returncode:
                    raise RuntimeError(f'{name} failed; see {output / (name + ".log")}')
            if args.mutations:
                for name, (before, after, case_id, expected_failure) in MUTATIONS.items():
                    original = (context / 'etc/awsh-bashrc').read_text()
                    if original.count(before) != 1:
                        raise ValueError(f'ambiguous mutation {name}')
                    mutant = output / f'mutant-{name}.bashrc'
                    mutant.write_text(original.replace(before, after))
                    evidence = f'{release}-mutant-{name}'
                    command = ['docker', 'run', '--rm', '--network=none', '-v',
                               f'{output}:/proof-output', '-v',
                               f'{mutant}:/omegaflow-runtime/etc/awsh-bashrc:ro', '-e',
                               f"OMEGAFLOW_CANDIDATE_DIGEST={candidate['observed']['executable_sha256']}",
                               '--entrypoint=/usr/bin/python3']
                    returning_fault = name == 'fail-stop-fallback'
                    helper_probe = name == 'nocasematch-gate-token'
                    if returning_fault or helper_probe:
                        command += ['-v', f'{returning}:/omegaflow-runtime/bin/awsh:ro']
                    command += [iid, '/proof/run.py', '--worker', '--case', case_id,
                                '--output', f'/proof-output/{evidence}']
                    if returning_fault:
                        command += ['--returning-helper']
                    if helper_probe:
                        command += ['--helper-probe']
                    log_path = output / f'{evidence}.log'
                    with log_path.open('w') as log:
                        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=30)
                    detected = result.returncode != 0 and expected_failure in log_path.read_text()
                    results.append({'release': release, 'mutation': name, 'case_id': case_id,
                                    'original_sha256': sha(context / 'etc/awsh-bashrc'),
                                    'mutant_sha256': sha(mutant), 'returncode': result.returncode,
                                    'detected': detected, 'expected_failure': expected_failure,
                                    'evidence': evidence, 'qualified': False})
                    if not detected:
                        raise AssertionError(f'mutation {name} was not detected: {log_path}')
    finally:
        write_json(output / 'results.json', results)
    print(f'All {len(candidates)} pinned candidates passed fixed-state proofs: {output}')


if __name__ == '__main__':
    main()
