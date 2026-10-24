package helper

import "syscall"

// FailStop emits no bytes, opens no socket and never returns. After each
// SIGCONT it stops again; only a terminating signal ends the process. Preserve
// inherited signal dispositions, including the completion helper's ignored INT.
func FailStop() {
	for {
		_ = syscall.Kill(syscall.Getpid(), syscall.SIGSTOP)
	}
}
