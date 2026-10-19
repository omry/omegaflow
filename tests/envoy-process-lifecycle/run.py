#!/usr/bin/env python3
"""Measure B6.5 lifecycle assembly with concrete process ownership."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/envoy-session-startup'))
from staging import build_peer, save, sha, stage, verified_rows  # noqa: E402

CASES = ('cancel-foreground', 'finalize-foreground', 'cancel-split',
         'finalize-split', 'cancel-helper', 'finalize-helper',
         'cancel-after-return', 'finalize-after-return', 'cancel-timeout',
         'finalize-timeout', 'natural-exit', 'fast-exit', 'cancel-foreground-switch',
         'cancel-stale-return', 'cancel-stale-live', 'cancel-stale-timeout',
         'exit-after-return-cwd')


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise ValueError('stamp02 overlay site changed: ' + label)
    return text.replace(old, new, 1)


def run_stamp02_regression(output):
    """Prove accepted-reap cancellation across completion peer registration."""
    completion = ROOT / 'runtime/envoy/awsh/launch/completion_linux.go'
    shell_test = ROOT / 'runtime/envoy/awsh/launch/shell_exit_linux_test.go'
    before = {completion: completion.read_bytes(), shell_test: shell_test.read_bytes()}
    output.mkdir(parents=True, exist_ok=False)
    test = r'''

var stamp02Accepted chan struct{}
var stamp02Resume chan struct{}

// Pause only after the real Unix accept. The selected-child wait owner,
// cancellation callback, failStart and ReportShellExit remain production code.
func TestStamp02AcceptedPeerCrossesReap(t *testing.T) {
	command := exec.Command("/bin/sleep", "60")
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = command.Process.Kill()
		_ = command.Process.Release()
	})
	child := &selectedChild{PID: command.Process.Pid, Signals: make(chan os.Signal, 1),
		Reaped: make(chan shellReap, 1), done: make(chan struct{})}
	child.startReaper()
	defer child.close()
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: t.TempDir() + "/helper.sock", Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	operation, cancel := context.WithCancel(context.Background())
	defer cancel()
	s := &Session{child: child, ShellPID: child.PID, result: writer, listener: listener,
		parentControlled: true, State: protocol.PromptState{PhysicalCWD: "/"},
		active: &startRecord{ctx: operation, phase: startReleased,
			request: protocol.PrivateExecute{OperationID: "op"}}}
	stamp02Accepted = make(chan struct{})
	stamp02Resume = make(chan struct{})
	defer func() { stamp02Accepted = nil; stamp02Resume = nil }()
	job := make(chan error, 1)
	go func() {
		_, err := s.BeginOperationEvent(operation)
		job <- err
	}()
	conn, err := net.DialUnix("unix", nil, listener.Addr().(*net.UnixAddr))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	select {
	case <-stamp02Accepted:
	case <-time.After(time.Second):
		t.Fatal("actual peer was not accepted")
	}
	if err := command.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	select {
	case reap := <-child.Reaped:
		if reap.Err != nil || !reap.Status.Signaled() || reap.Status.Signal() != syscall.SIGKILL {
			t.Fatal("missing actual child reap", reap)
		}
	case <-time.After(time.Second):
		t.Fatal("actual child was not reaped")
	}
	if err := s.AcceptShellExit(); err != nil {
		t.Fatal(err)
	}
	cancel()
	select {
	case <-s.completion.done:
	case <-time.After(time.Second):
		t.Fatal("cancellation callback was not joined")
	}
	close(stamp02Resume)
	var jobErr error
	select {
	case jobErr = <-job:
	case <-time.After(time.Second):
		t.Fatal("completion job was not joined")
	}
	ctx, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	if err := s.ReportShellExit(ctx, 137, jobErr); err != nil {
		t.Fatalf("accepted actual reap plus cancelled peer registration became substantive failure: job=%v report=%v", jobErr, err)
	}
	message, err := protocol.ReadPrivate(bufio.NewReader(reader), protocol.AwshToEnvoy)
	exit, ok := message.(*protocol.PrivateShellExit)
	if err != nil || !ok || exit.Status != 137 || exit.ActiveOperationID != "op" || exit.CWD != "/" {
		t.Fatalf("wrong terminal shell_exit fact: %v %v", message, err)
	}
}
'''
    anchor = ('\tc, err := s.listener.AcceptUnix()\n'
              '\tif err != nil {\n'
              '\t\treturn nil, nil, err\n'
              '\t}\n'
              '\tunregister, closing := s.registerPeer(c)')
    pause = ('\tc, err := s.listener.AcceptUnix()\n'
             '\tif err != nil {\n'
             '\t\treturn nil, nil, err\n'
             '\t}\n'
             '\tif stamp02Accepted != nil { close(stamp02Accepted); <-stamp02Resume }\n'
             '\tunregister, closing := s.registerPeer(c)')
    positive_completion = replace_once(completion.read_text(), anchor, pause,
                                       'completion AcceptUnix/registerPeer boundary')
    wrapped = ('\t\tif err := r.ctx.Err(); err != nil {\n'
               '\t\t\treturn nil, nil, fmt.Errorf("completion interrupted: %w", err)\n'
               '\t\t}\n')
    negative_completion = replace_once(positive_completion, wrapped, '',
                                      'completion cancellation provenance')
    results = []
    try:
        for name, source in (('positive', positive_completion),
                             ('negative-original-defect', negative_completion)):
            context = output / name
            context.mkdir()
            completion_fixture = context / completion.name
            completion_fixture.write_text(source)
            shell_fixture = context / shell_test.name
            shell_fixture.write_bytes(before[shell_test] + test.encode())
            overlay_path = context / 'overlay.json'
            save(overlay_path, {'Replace': {str(completion): str(completion_fixture),
                                             str(shell_test): str(shell_fixture)}})
            argv = ['go', '-C', str(ROOT / 'runtime/envoy'), 'test', '-race',
                    '-buildvcs=false', '-overlay', str(overlay_path), '-run',
                    '^TestStamp02AcceptedPeerCrossesReap$', '-count=1',
                    '-timeout=20s', '-v', './awsh/launch']
            log = context / 'stamp02.log'
            started = time.time()
            with log.open('w') as stream:
                result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT,
                                        timeout=60, env=dict(os.environ))
            text = log.read_text()
            if name == 'positive':
                proved = result.returncode == 0 and '--- PASS: TestStamp02AcceptedPeerCrossesReap' in text
            else:
                proved = result.returncode != 0 and 'accepted actual reap plus cancelled peer registration became substantive failure' in text
            row = {'name': name, 'argv': argv, 'returncode': result.returncode,
                   'proved': proved, 'started_unix': started,
                   'completed_unix': time.time(),
                   'source_sha256': sha(completion),
                   'completion_overlay_sha256': sha(completion_fixture),
                   'shell_test_overlay_sha256': sha(shell_fixture),
                   'overlay_sha256': sha(overlay_path),
                   'log_path': str(log), 'log_sha256': sha(log),
                   'scope': 'Disposable Go overlay pauses only after actual AcceptUnix; '
                            'negative variant restores the demonstrated plain-error defect.'}
            results.append(row)
            save(output / 'results.json', results)
            if not proved:
                raise ValueError('stamp02 regression proof failed: ' + str(log))
    finally:
        if any(path.read_bytes() != contents for path, contents in before.items()):
            raise ValueError('stamp02 disposable overlay changed production sources')
    return results


def run_stamp03_regression(output):
    """Prove final prompt_ready read cancellation preserves shell_exit."""
    completion = ROOT / 'runtime/envoy/awsh/launch/completion_linux.go'
    shell_test = ROOT / 'runtime/envoy/awsh/launch/shell_exit_linux_test.go'
    before = {completion: completion.read_bytes(), shell_test: shell_test.read_bytes()}
    output.mkdir(parents=True, exist_ok=False)
    final_read = (
        '\tmessage, err := helper.ReadRequest(c, protocol.HelperCompletion)\n'
        '\tif err != nil {\n'
        '\t\treturn s.failStart(fmt.Errorf("invalid final prompt_ready: %w", err))\n'
        '\t}\n'
        '\tstate, ok := message.(*protocol.HelperCompletionReady)\n'
        '\tif !ok {\n'
        '\t\treturn s.failStart(fmt.Errorf("invalid final prompt_ready: expected HelperCompletionReady"))\n'
        '\t}\n'
        '\tif state.Status != r.state.Status {\n'
        '\t\treturn s.failStart(fmt.Errorf("invalid final prompt_ready status: got %d want %d", state.Status, r.state.Status))\n'
        '\t}\n')
    call = ('\tstate, err := s.stamp03FinalReadBoundary(c, r)\n'
            '\tif err != nil { return err }\n')
    positive_completion = replace_once(completion.read_text(), final_read, call,
                                       'final prompt_ready read/error boundary')
    positive_helper = r'''

// Test-only extraction of the exact final ReadRequest boundary. The native
// FinishCompletion setup is intentionally outside this disposable probe.
func (s *Session) stamp03FinalReadBoundary(c *net.UnixConn, r *completionRecord) (*protocol.HelperCompletionReady, error) {
	if stamp03ReadStarted != nil {
		close(stamp03ReadStarted)
	}
	message, err := helper.ReadRequest(c, protocol.HelperCompletion)
	if err != nil {
		return nil, s.failStart(fmt.Errorf("invalid final prompt_ready: %w", err))
	}
	state, ok := message.(*protocol.HelperCompletionReady)
	if !ok {
		return nil, s.failStart(fmt.Errorf("invalid final prompt_ready: expected HelperCompletionReady"))
	}
	if state.Status != r.state.Status {
		return nil, s.failStart(fmt.Errorf("invalid final prompt_ready status: got %d want %d", state.Status, r.state.Status))
	}
	return state, nil
}
'''
    negative_helper = r'''

// Test-only extraction of the original lossy final ReadRequest boundary.
func (s *Session) stamp03FinalReadBoundary(c *net.UnixConn, r *completionRecord) (*protocol.HelperCompletionReady, error) {
	if stamp03ReadStarted != nil {
		close(stamp03ReadStarted)
	}
	message, err := helper.ReadRequest(c, protocol.HelperCompletion)
	state, ok := message.(*protocol.HelperCompletionReady)
	if err != nil || !ok || state.Status != r.state.Status {
		return nil, s.failStart(fmt.Errorf("invalid final prompt_ready: %v", err))
	}
	return state, nil
}
'''
    test = r'''

var stamp03ReadStarted chan struct{}

// This probe uses the real selected-child wait owner, Unix socket read,
// cancellation callback, failStart and ReportShellExit. It intentionally
// bypasses the earlier FinishCompletion readiness and identity setup.
func TestStamp03FinalReadAcceptedReap(t *testing.T) {
	command := exec.Command("/bin/sleep", "60")
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = command.Process.Kill()
		_ = command.Process.Release()
	})
	child := &selectedChild{PID: command.Process.Pid, Signals: make(chan os.Signal, 1),
		Reaped: make(chan shellReap, 1), done: make(chan struct{})}
	child.startReaper()
	defer child.close()
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: t.TempDir() + "/peer.sock", Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	client, err := net.DialUnix("unix", nil, listener.Addr().(*net.UnixAddr))
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	peer, err := listener.AcceptUnix()
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	defer writer.Close()
	operation, cancel := context.WithCancel(context.Background())
	defer cancel()
	record := &completionRecord{ctx: operation, pidfd: -1, done: make(chan struct{})}
	s := &Session{child: child, result: writer, listener: listener,
		State: protocol.PromptState{PhysicalCWD: "/"}, completion: record,
		active: &startRecord{ctx: operation, phase: startReleased,
			request: protocol.PrivateExecute{OperationID: "op"}}}
	unregister, closing := s.registerPeer(peer)
	if closing {
		t.Fatal("peer registered after close")
	}
	defer unregister()
	record.stop = context.AfterFunc(operation, func() {
		defer close(record.done)
		s.interruptIO()
		if !s.shellEnding.Load() {
			_ = s.result.Close()
		}
	})
	stamp03ReadStarted = make(chan struct{})
	defer func() { stamp03ReadStarted = nil }()
	job := make(chan error, 1)
	go func() {
		s.startMu.Lock()
		_, err := s.stamp03FinalReadBoundary(peer, record)
		s.startMu.Unlock()
		job <- err
	}()
	select {
	case <-stamp03ReadStarted:
	case <-time.After(time.Second):
		t.Fatal("final helper read did not start")
	}
	if err := command.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	select {
	case reap := <-child.Reaped:
		if reap.Err != nil || !reap.Status.Signaled() || reap.Status.Signal() != syscall.SIGKILL {
			t.Fatal("missing actual selected-child reap", reap)
		}
	case <-time.After(time.Second):
		t.Fatal("actual child was not reaped")
	}
	if err := s.AcceptShellExit(); err != nil {
		t.Fatal(err)
	}
	cancel()
	select {
	case <-record.done:
	case <-time.After(time.Second):
		t.Fatal("cancellation callback was not joined")
	}
	jobErr := <-job
	if jobErr == nil {
		t.Fatal("final helper read did not return an error")
	}
	ctx, end := context.WithTimeout(context.Background(), time.Second)
	defer end()
	if err := s.ReportShellExit(ctx, 137, jobErr); err != nil {
		t.Fatalf("accepted reap cancelled final helper read but lost shell_exit: job=%v report=%v", jobErr, err)
	}
	message, err := protocol.ReadPrivate(bufio.NewReader(reader), protocol.AwshToEnvoy)
	exit, ok := message.(*protocol.PrivateShellExit)
	if err != nil || !ok || exit.Status != 137 || exit.ActiveOperationID != "op" || exit.CWD != "/" {
		t.Fatalf("wrong terminal shell_exit fact: %v %v", message, err)
	}
}
'''
    results = []
    try:
        for name, helper_source, source in (
                ('positive', positive_helper, positive_completion),
                ('negative-original-defect', negative_helper, positive_completion)):
            context = output / name
            context.mkdir()
            completion_fixture = context / completion.name
            completion_fixture.write_text(source + helper_source)
            shell_fixture = context / shell_test.name
            shell_fixture.write_bytes(before[shell_test] + test.encode())
            overlay_path = context / 'overlay.json'
            save(overlay_path, {'Replace': {str(completion): str(completion_fixture),
                                             str(shell_test): str(shell_fixture)}})
            argv = ['go', '-C', str(ROOT / 'runtime/envoy'), 'test', '-race',
                    '-buildvcs=false', '-overlay', str(overlay_path), '-run',
                    '^TestStamp03FinalReadAcceptedReap$', '-count=1',
                    '-timeout=20s', '-v', './awsh/launch']
            log = context / 'stamp03.log'
            started = time.time()
            with log.open('w') as stream:
                result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT,
                                        timeout=60, env={**os.environ,
                                                        'GOCACHE': '/tmp/omegaflow-go-cache',
                                                        'CHGDISABLE': '1'})
            text = log.read_text()
            if name == 'positive':
                proved = result.returncode == 0 and '--- PASS: TestStamp03FinalReadAcceptedReap' in text
            else:
                proved = (result.returncode != 0 and
                          'accepted reap cancelled final helper read but lost shell_exit' in text)
            row = {'name': name, 'argv': argv, 'returncode': result.returncode,
                   'proved': proved, 'started_unix': started,
                   'completed_unix': time.time(),
                   'source_sha256': sha(completion),
                   'completion_overlay_sha256': sha(completion_fixture),
                   'shell_test_overlay_sha256': sha(shell_fixture),
                   'overlay_sha256': sha(overlay_path),
                   'log_path': str(log), 'log_sha256': sha(log),
                   'scope': 'Disposable Go overlay extracts the exact final ReadRequest boundary; '
                            'the probe uses real child reap, Unix read, cancellation callback, '
                            'failStart and ReportShellExit, but bypasses earlier FinishCompletion '
                            'readiness and identity setup.'}
            results.append(row)
            save(output / 'results.json', results)
            if not proved:
                raise ValueError('stamp03 regression proof failed: ' + str(log))
    finally:
        if any(path.read_bytes() != contents for path, contents in before.items()):
            raise ValueError('stamp03 disposable overlay changed production sources')
    return results


def overlay(output, mutation=None):
    sources = {
        'signal_linux.go': (
            'if err := signalPTY(master); err != nil {',
            'nativeLifecycleBeforeSignal(master); if err := signalPTY(master); err != nil {'),
        'process_census_linux.go': (
            'func (c *processCensus) Members(ctx context.Context, pgid int64) ([]foregroundMember, error) {\n\tc.mu.Lock()',
            'func (c *processCensus) Members(ctx context.Context, pgid int64) ([]foregroundMember, error) {\n\tc.mu.Lock()\n\tnativeLifecycleSample(pgid)'),
        'completion_linux.go': (
            'if err := cleanup(job, proposal.OperationID, proposal.CompletionHelperPID); err != nil {',
            'if err := nativeLifecycleCleanup(job); err != nil { return err }; '
            'if err := cleanup(job, proposal.OperationID, proposal.CompletionHelperPID); err != nil {'),
    }
    if mutation == 'omit-descendants':
        sources['completion_linux.go'] = (
            'return census.Cleanup(job, helperPID)', 'return nil')
    elif mutation == 'omit-shell-kill':
        sources['operation_process_linux.go'] = (
            'return ref.signal(ctx, syscall.SIGKILL)',
            'return ref.signal(ctx, syscall.Signal(0))')
    replacements, inventory = {}, []
    for name, (old, new) in sources.items():
        source_name = name.split(':', 1)[0]
        source = ROOT / 'runtime/envoy/session' / source_name
        text = source.read_text()
        if text.count(old) != 1:
            raise ValueError('lifecycle overlay site changed: ' + name)
        if source_name == 'signal_linux.go':
            observe = 'return errors.Join(err, native)'
            if text.count(observe) != 1:
                raise ValueError('lifecycle overlay site changed: signal observation')
            text = text.replace(observe,
                                'result := errors.Join(err, native); nativeLifecycleSignal(result); return result', 1)
        text = text.replace(old, new, 1)
        if source_name == 'process_census_linux.go':
            observe_members = 'return c.membersAfterRefresh(ctx, pgid)'
            if text.count(observe_members) != 1:
                raise ValueError('lifecycle overlay site changed: census observation')
            text = text.replace(observe_members,
                                'members, err := c.membersAfterRefresh(ctx, pgid)\n\tnativeLifecycleMembers(pgid, len(members))\n\treturn members, err', 1)
        if mutation == 'omit-foreground-switch' and source_name == 'signal_linux.go':
            text = text.replace('nativeLifecycleBeforeSignal(master); ', '', 1)
        if mutation == 'omit-empty-membership' and source_name == 'process_census_linux.go':
            text = text.replace('\tnativeLifecycleMembers(pgid, len(members))\n', '', 1)
        target = output / source_name
        target.write_text(text)
        replacements[str(source)] = str(target)
        inventory.append({'source': str(source), 'source_sha256': sha(source),
                          'overlay_sha256': sha(target)})
    path = output / 'overlay.json'
    save(path, {'Replace': replacements})
    save(output / 'overlay-inventory.json', inventory)
    return path


def measure(output, rows, binary, awsh, case=None, diagnostic=None):
    results = []
    for row, entry, actual in rows:
        iid, context = stage(output, row['release'], entry, actual, binary)
        argv = ['docker', 'run', '--rm', '--network=none',
                '-e', 'OMEGAFLOW_NATIVE_PROCESS_LIFECYCLE=1',
                '-e', 'OMEGAFLOW_PROCESS_LIFECYCLE_CASE=' + (case or ''),
                '--entrypoint=/omegaflow-runtime/bin/envoy', iid,
                '-test.run=^TestNativeProcessLifecycle$', '-test.v', '-test.timeout=360s']
        started = time.time()
        log = context / 'native.log'
        with log.open('w') as stream:
            result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=380)
        text = log.read_text()
        proved = (result.returncode != 0 and diagnostic in text) if diagnostic else (
            result.returncode == 0 and text.count('PROCESS LIFECYCLE ') == (1 if case else len(CASES))
            and '--- PASS: TestNativeProcessLifecycle (' in text)
        results.append({'release': row['release'], 'case': case, 'cases': 1 if case else len(CASES),
                        'proved': proved, 'expected_failure': diagnostic,
                        'returncode': result.returncode, 'argv': argv,
                        'started_unix': started, 'completed_unix': time.time(),
                        'qualified_awsh_sha256': awsh,
                        'qualification_receipt_sha256': row['receipt_sha256'],
                        'qualified_base_image': actual, 'native_image': iid,
                        'test_binary_sha256': sha(binary),
                        'log_path': str(log), 'log_sha256': sha(log)})
        save(output / 'results.json', results)
        if not proved:
            raise ValueError('actual process lifecycle proof failed: ' + str(log))


def build(binary, path, output):
    argv = ['go', '-C', str(ROOT / 'runtime/envoy'), 'test', '-buildvcs=false',
            '-c', '-overlay', str(path), '-o', str(binary), './session']
    subprocess.run(argv, check=True, env={**os.environ, 'CGO_ENABLED': '0',
                                        'GOOS': 'linux', 'GOARCH': 'amd64'})
    save(output / 'build-request.json', {'argv': argv, 'overlay_sha256': sha(path)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release', choices=('5.2.15', '5.2.37', '5.3.9'))
    parser.add_argument('--case', choices=CASES)
    parser.add_argument('--no-negative', action='store_true', help='partial development only')
    parser.add_argument('--stamp02-regression-only', action='store_true',
                        help='run the accepted-reap registration overlay without native qualification')
    parser.add_argument('--stamp03-regression-only', action='store_true',
                        help='run the final prompt_ready read cancellation overlay without native qualification')
    args = parser.parse_args()
    if args.stamp02_regression_only:
        output = args.output.resolve()
        run_stamp02_regression(output)
        print('Stamp02 accepted-reap evidence: ' + str(output))
        return
    if args.stamp03_regression_only:
        output = args.output.resolve()
        run_stamp03_regression(output)
        print('Stamp03 final-read evidence: ' + str(output))
        return
    if args.qualification is None:
        parser.error('--qualification is required unless --stamp02-regression-only is set')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = [ROOT / 'runtime/envoy/session' / name for name in
               ('signal_linux.go', 'process_census_linux.go', 'completion_linux.go', 'operation_process_linux.go')]
    before = [p.read_bytes() for p in sources]
    try:
        with build_peer(output, './session') as (binary, awsh):
            rows = [r for r in verified_rows(args.qualification, awsh)
                    if not args.release or r[0]['release'] == args.release]
            if not rows:
                raise ValueError('no selected genuine target')
            observations = output / 'observations'
            observations.mkdir()
            build(binary, overlay(observations), observations)
            measure(output, rows, binary, awsh, args.case)
            if not args.no_negative:
                for name, case, diagnostic in (
                    ('omit-descendants', 'cancel-foreground', 'operation process remains at idle boundary'),
                    ('omit-shell-kill', 'cancel-timeout', 'actual lifecycle transaction failed'),
                    ('omit-foreground-switch', 'cancel-foreground-switch', 'native lifecycle coordination failed'),
                    ('omit-empty-membership', 'cancel-stale-live', 'native stale empty membership observation missing'),
                ):
                    target = output / 'negative' / name
                    target.mkdir(parents=True)
                    build(binary, overlay(target, name), target)
                    measure(target, rows[:1], binary, awsh, case, diagnostic)
    finally:
        if [p.read_bytes() for p in sources] != before:
            raise ValueError('disposable overlay changed production sources')
    print('Actual process lifecycle evidence: ' + str(output))


if __name__ == '__main__':
    main()
