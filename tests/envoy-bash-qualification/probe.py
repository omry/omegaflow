#!/usr/bin/env python3
"""Trusted native B2 qualification probe; run only in its isolated test image."""
import base64
import contextlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import signal
import subprocess
import tempfile
import termios
import time
import traceback
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path('/omegaflow-runtime')
PROOF = Path('/qualification')
AWSh = RUNTIME / 'bin/awsh'
RC = RUNTIME / 'etc/awsh-bashrc'
PACKAGES = {'helper': 'awsh/helper', 'protocol': 'protocol', 'bashbuild': 'bashbuild',
            'submission': 'awsh/submission', 'launch': 'awsh/launch'}
ENV = {'PATH': '/hostile/path', 'HOME': '/hostile/home', 'PWD': '/',
       'HISTFILE': '', 'INPUTRC': str(RUNTIME / 'etc/inputrc'),
       'TERM': 'xterm-256color', 'TERMINFO': str(RUNTIME / 'share/terminfo'),
       'TERMINFO_DIRS': str(RUNTIME / 'share/terminfo'), 'LC_ALL': 'C.UTF-8',
       'LANG': 'C.UTF-8', 'LOCPATH': str(RUNTIME / 'lib/locale')}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@contextlib.contextmanager
def fault(path, data):
    """Existing negative-case injection, confined to the disposable image.

    Normal cases run the pristine bound bytes. Every fault restores those
    bytes and their mode before the qualification tool rechecks inputs.
    """
    path = path.resolve(strict=True)
    original, mode = path.read_bytes(), path.stat().st_mode & 0o777

    def replace(content, permissions):
        # A previous isolated helper may still be exiting. Replace its inode
        # instead of truncating an executing file; retain placement symlinks.
        descriptor, name = tempfile.mkstemp(prefix='.qualification-fault-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(content)
            os.chmod(name, permissions)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    try:
        replace(data, 0o555)
        yield
    finally:
        replace(original, mode)
        require(path.read_bytes() == original, 'fault restoration mismatch')


def successful_go(binary, pattern, directory, proofs, extra=None, label=None):
    label = label or binary
    name = label + '-' + str(len(list(directory.glob('*.log'))))
    log = directory / (name + '.log')
    with log.open('w') as stream:
        subprocess.run([str(PROOF / 'bin' / (binary + '.test')), '-test.v',
                        '-test.timeout=90s', '-test.run=' + pattern],
                       cwd='/' if binary == 'launch' else ROOT / 'runtime/envoy' / PACKAGES[binary],
                       env={**os.environ, **ENV, **(extra or {})},
                       stdout=stream, stderr=subprocess.STDOUT, timeout=100, check=True)
    text = log.read_text()
    require('--- SKIP:' not in text, 'skipped native qualification test: ' + name)
    passed = re.findall(r'--- PASS: (\S+) \(', text)
    require(bool(passed), 'native command executed no assertions: ' + name)
    for test in passed:
        proofs[label + ':' + test] = {'log': str(log), 'test': test}


def startup(directory, signals, qualified_receipt=None):
    """Exercise the existing real launch supervisor and capture its actual state."""
    master, slave = pty.openpty()
    cr, cw = os.pipe()
    rr, rw = os.pipe()
    er, ew = os.pipe()
    sources = [cr, rw, slave, ew]
    stable = [fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10) for fd in sources]
    pid = os.fork()
    if pid == 0:
        for target, source in enumerate(stable, 3):
            os.dup2(source, target)
        for fd, mode in [(0, os.O_RDONLY), (1, os.O_WRONLY), (2, os.O_WRONLY)]:
            null = os.open('/dev/null', mode)
            os.dup2(null, fd)
            if null > 6:
                os.close(null)
        os.closerange(7, 65536)
        env = {**ENV, 'OMEGAFLOW_TEST_CHILD': '1',
               'OMEGAFLOW_TEST_DIGEST': os.environ['OMEGAFLOW_CANDIDATE_DIGEST'],
               'OMEGAFLOW_TEST_SIGNALS': json.dumps(signals)}
        if qualified_receipt is not None:
            env['OMEGAFLOW_QUALIFIED_RECEIPT'] = str(qualified_receipt)
        os.execve(str(PROOF / 'bin/launch.test'), ['launch.test', '-test.run=^TestSupervisorProcess$'], env)
    for fd in stable + sources:
        os.close(fd)
    raw, frame, evidence = bytearray(), bytearray(), bytearray()
    exited = False
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and b'\n' not in evidence:
            for fd in select.select([master, rr, er], [], [], .02)[0]:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    data = b''
                {master: raw, rr: frame, er: evidence}[fd].extend(data)
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited:
                exited = True
                raise ValueError('startup supervisor exited before measurement: ' + evidence.decode(errors='replace'))
        require(b'\n' in evidence, 'missing actual startup evidence')
        detail = json.loads(evidence)
        require(frame.endswith(b'\0') and frame[:-1].decode().split('\0') ==
                ['awsh-v1', 'ready', str(pid), str(detail['shell_pid']), '/'], 'wrong startup identity')
        require(detail['active_termios']['Lflag'] & (termios.ICANON | termios.ECHO) == 0,
                'Readline echo/canonical mode')
        require(detail['restored_termios'] == detail['workload_termios'], 'inexact restoration')
        time.sleep(.03)
        while select.select([master], [], [], 0)[0]:
            raw.extend(os.read(master, 65536))
        require(len(raw) <= 4096 and not re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', raw),
                'unexpected startup prompt/source bytes')
        exported = detail['state']['ExportedEnv']
        transform = {'unset': sorted(set(ENV) - set(exported)),
                     'set': {k: v for k, v in exported.items() if ENV.get(k) != v}}
        os.write(cw, b'x')
        _, status = os.waitpid(pid, 0)
        exited = True
        require(os.waitstatus_to_exitcode(status) == 0, 'startup cleanup failed')
        require(not Path('/run/omegaflow/session/bash').exists(), 'startup path retained')
        (directory / 'startup.json').write_text(json.dumps(detail, indent=2) + '\n')
        (directory / 'startup.raw').write_bytes(raw)
        return {'startup_pty_base64': base64.b64encode(raw).decode(), 'startup_export': transform}
    finally:
        if not exited:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            os.waitpid(pid, 0)
        for fd in (master, cw, rr, er):
            os.close(fd)


def reserved(directory, proofs):
    worker = module(ROOT / 'tests/envoy-bash-reserved-state/run.py', 'reserved')
    # The existing worker reads its immutable corpus at this fixed test path.
    for mode, returning, helper in [('ordinary', False, False), ('fallback', True, False),
                                    ('helper-probe', False, True)]:
        output = directory / mode
        args = SimpleNamespace(output=output, case=None, returning_helper=returning, helper_probe=helper)
        injection = fault(AWSh, b'#!/bin/bash\nexit 0\n') if returning or helper else contextlib.nullcontext()
        with injection, (directory / (mode + '.log')).open('w') as log, contextlib.redirect_stdout(log):
            worker.worker(args)
        result = json.loads((output / 'results.json').read_text())
        for row in result['cases']:
            require(row.get('passed') is True, 'reserved case did not pass: ' + row['id'])
            proofs['reserved:' + row['id']] = {'result': str(output / row['id'] / 'result.json')}


def readline(directory, proofs):
    worker = module(ROOT / 'tests/envoy-bash-submission/run.py', 'readline')
    # These authored programs include ordinary external commands (e.g. cat).
    # The hostile environment is tested separately by the launch supervisor.
    os.environ['PATH'] = '/usr/bin:/bin'
    cases = list(worker.expanded_cases(json.loads((ROOT / 'tests/envoy-bash-submission/cases.json').read_text())))
    cases += json.loads((ROOT / 'tests/envoy-bash-start/cases.json').read_text())
    for variant in ('function', 'disabled'):
        for status in (0, 1, 255):
            cases.append({'id': f'B1-C011-suffix-colon-{variant}-{status}',
                          'source': (':() { builtin printf BAD_COLON; }; ' if variant == 'function'
                                     else 'builtin enable -n :; ') +
                                    f'set -e; (builtin exit {status}) && builtin true',
                          'result': status, 'absent': ['BAD']})
    cases += [
        {'id': 'B1-C032-first-expansion-status', 'status': 7, 'execution_shape': 'split',
         'source': 'builtin printf "FIRST:%s\\n" "$?"', 'output': ['FIRST:7\n']},
        {'id': 'B1-C032-first-command-status', 'status': 7, 'execution_shape': 'split',
         'source': '[[ $? == 7 ]]', 'result': 0},
    ]
    for shape in ('pty', 'split'):
        for status in (0, 255):
            cases.append({'id': f'B1-C010-canonical-frame-{shape}-{status}', 'status': status,
                          'execution_shape': shape,
                          'source': 'builtin printf "PRIOR:%s\\n" "$?"',
                          'output': [f'PRIOR:{status}' + ('\n' if shape == 'split' else '\r\n')]})
    for case in cases:
        helper = case.get('helper_fault')
        replacement = None
        if helper:
            replacement = ('#!/bin/bash\nif [[ $3 == source ]]; then\n'
                           f"builtin printf '%s' {shlex.quote(helper['stdout'])}\n"
                           f"builtin exit {helper['status']}\nfi\n"
                           'exec /qualification/bin/awsh-real "$@"\n').encode()
        elif 'marker' in case:
            body = 'builtin kill -s TERM -- "$$"\n' if case.get('signal') else (
                f"builtin printf '%s' {shlex.quote(case['marker'])}\nbuiltin exit {case['status']}\n")
            replacement = ('#!/bin/bash\nif [[ $3 == start-prepared ]]; then\n' + body +
                           'fi\nexec /qualification/bin/awsh-real "$@"\n').encode()
        injection = fault(AWSh, replacement) if replacement else contextlib.nullcontext()
        with injection:
            worker.readline(case, directory / case['id'])
        observed = json.loads((directory / case['id'] / 'observations.json').read_text())
        require(observed['error'] is None, 'Readline assertion failed: ' + case['id'])
        proofs['readline:' + case['id']] = {'observations': str(directory / case['id'] / 'observations.json')}
    measurement = {'id': 'keymap-measurement', 'source':
                   'while IFS=" " builtin read -r sequence binding; do '
                   'builtin printf "loader:%s\\n" "${sequence%:}"; '
                   'done < <(builtin bind -X); builtin bind -s; builtin bind -v',
                   'output': ['set keymap emacs', 'set editing-mode emacs',
                              'loader:"\\C-x\\C-a"', '"\\C-x\\C-b": "\\C-x\\C-a\\C-m"']}
    worker.readline(measurement, directory / measurement['id'])
    raw = (directory / measurement['id'] / 'terminal.raw').read_bytes()
    match = re.search(rb'set keymap ([a-z-]+)\r?\n', raw)
    require(match is not None, 'missing measured keymap')
    # These are observations of the actual reserved bindings. The worker has
    # already asserted both strings in authored output and zero redisplay.
    require('readline:B2.5-unicode-no-redisplay' in proofs, 'missing UTF-8 cursor proof')
    maximum = json.loads((directory / 'B2.5-maximum/observations.json').read_text())
    require(maximum['source_bytes'] == 786432, 'maximum source case changed')
    return {'keymap': match[1].decode(), 'loader_hex': '1801', 'submit_hex': '1802',
            'readiness': True, 'no_redisplay': True, 'utf8_cursor': True,
            'maximum_line_bytes': maximum['source_bytes']}


def collect_cases(mapping, required, proofs):
    require(type(mapping) is dict and set(mapping) == set(required), 'incomplete/extra case map')
    result = {}
    for case_id, selectors in mapping.items():
        require(type(selectors) is list and bool(selectors) and len(selectors) == len(set(selectors)),
                'invalid case selectors: ' + case_id)
        require(all(type(s) is str and s in proofs for s in selectors),
                'missing actual assertion for ' + case_id + ': ' + repr(selectors))
        result[case_id] = True
    return result


def main():
    require(PROOF.is_dir() and RUNTIME.is_dir(), 'isolated qualification image required')
    tool = module(ROOT / 'tools/bash_builds.py', 'builds')
    launch = module(ROOT / 'tests/envoy-bash-launch/run.py', 'launch')
    digest = tool.sha(Path('/bin/bash').read_bytes())
    require(digest == os.environ['OMEGAFLOW_CANDIDATE_DIGEST'], 'candidate changed')
    table = subprocess.check_output(['/bin/bash', '--noprofile', '--norc', '-c', 'builtin trap -l'], text=True)
    signals = launch._validate_signal_inventory(table, 'qualification')
    names = {name.removeprefix('SIG'): int(number) for number, name in re.findall(r'(\d+)\)\s+(\S+)', table)
             if int(number) not in (9, 19)}
    os.environ.update(ENV)
    os.environ['OMEGAFLOW_CANDIDATE_SIGNALS'] = json.dumps(signals)
    os.environ['OMEGAFLOW_NONROOT_SUPERVISOR'] = '1'
    os.environ['OMEGAFLOW_START_UID'] = '1000'
    directory = Path(tempfile.mkdtemp(prefix='probe-', dir='/evidence'))
    directory.chmod(0o755)
    proofs = {}
    measurements = startup(directory, signals)
    proofs['measurement:startup'] = {'result': str(directory / 'startup.json')}
    for binary in ['helper', 'protocol', 'bashbuild', 'submission']:
        successful_go(binary, '^Test', directory, proofs)
    for name, body in [('stdout', 'printf fault'), ('stderr', 'printf fault >&2'), ('nonzero', 'exit 1')]:
        # Deliberately malformed checker output is impossible to request from a
        # normal Bash -n invocation. Inject the existing child's fixed path in
        # the disposable image; no production command override is introduced.
        with fault(Path('/bin/bash'), ('#!/bin/sh\n' + body + '\n').encode()):
            successful_go('submission', '^TestQualificationCheckerOutput$', directory, proofs,
                          {'OMEGAFLOW_QUALIFICATION_CHECKER_FAULT': name}, label='checker-' + name)
    selected = launch.RCFILE.replace('/omegaflow-runtime/etc/observe.py', '/qualification/observe.py')
    with fault(RC, selected.encode()):
        successful_go('launch', '^Test(HandoffGrammar|RealSelectedLaunch|SelectedReaper)$',
                      directory, proofs, label='selected')
    successful_go('launch', '^Test(QualifiedLaunchFailsClosed|TerminalLeaseRestoration|RealCandidateStartup|'
                  'LivePromptStateIdentity|StartupMonitorStopPreservesFatalObservations|'
                  'StartupCancellationJoinsStartedCallback|StartupCancellationDisarmedBeforeCommit|'
                  'StartupZombieIdentity)$', directory, proofs)
    successful_go('launch', '^TestRealCandidateStartup$', directory, proofs,
                  {'OMEGAFLOW_TEST_INITIAL_RAW': '1'}, label='raw-startup')
    successful_go('launch', '^Test(StartRejectsEventsOutsideOperation|RealCandidateStart|'
                  'CompletionEventsOutsideOperation|RealCandidateCompletion|FinalInspectionPlans|'
                  'MalformedExpansionStaysLiteral|InspectionHomeExpansion|QualificationInspectionExamples)$',
                  directory, proofs, {'OMEGAFLOW_QUALIFICATION_ROOT': str(ROOT)})
    reserved(directory, proofs)
    measurements['readline'] = readline(directory, proofs)
    measurements['catchable_signals'] = names
    proofs['measurement:readline'] = {'result': measurements['readline']}
    mapping = tool.read(Path(__file__).with_name('case-map.json'))
    (directory / 'case-proofs.json').write_text(json.dumps({'assertions': proofs, 'mapping': mapping}, indent=2) + '\n')
    result = {'measurements': measurements, 'cases': collect_cases(mapping, tool.case_ids(), proofs)}
    tool.probe_result(result, tool.case_ids())
    (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    Path('/run/omegaflow/session/bash').rmdir()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        Path('/evidence/probe-error.txt').write_text(traceback.format_exc())
        raise
