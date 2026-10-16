//go:build linux

package session

import (
	"context"
	"errors"
	"fmt"
	"os"
	"syscall"
	"unsafe"
)

// foregroundMember is a live process whose lifetime and controlled-tree
// classification the census owner has pinned for this proof. B6 supplies the
// real pidfd/census implementation; B4 isolated assembly requires an explicit
// peer. No missing proof is treated as permission to signal.
type foregroundMember struct {
	PID, PGID, SessionID int64
	Controlled           bool
}

type foregroundCensus interface {
	// ProveBoundary establishes that every eligible foreground group belongs
	// to the controlled session/tree. Members returns all live group members
	// with retained lifetime proofs; an empty list is a permitted stale group.
	ProveBoundary(context.Context, int64) error
	Members(context.Context, int64) ([]foregroundMember, error)
}

// signalForeground performs one proof/sample attempt. False with no error
// retains an empty positive group for the same owner to resample while racing
// return facts and the original grace. It never chooses timeout itself.
func (l *operationLifecycle) signalForeground(master *os.File, sessionID int64, census foregroundCensus) (bool, error) {
	if l.failure != nil {
		return false, l.failure
	}
	if !l.signalNeeded() {
		return false, nil
	}
	ctx := l.grace
	deadline, _ := ctx.Deadline()
	fail := func(cause error) (bool, error) {
		l.failure = &StartError{Code: "lifecycle-signal", Deadline: deadline, Cause: cause}
		return false, l.failure
	}
	if err := ctx.Err(); err != nil {
		return false, err // The existing owner selects expiry or session failure.
	}
	if master == nil || sessionID <= 0 || census == nil {
		return fail(fmt.Errorf("missing controlled foreground boundary"))
	}
	if err := census.ProveBoundary(ctx, sessionID); err != nil {
		if ctx.Err() != nil {
			return false, ctx.Err()
		}
		return fail(err)
	}
	var sid, pgid int32
	if err := ptyIOCTL(master, syscall.TIOCGSID, unsafe.Pointer(&sid)); err != nil {
		return fail(err)
	}
	if int64(sid) != sessionID {
		return fail(fmt.Errorf("terminal session does not match controlled session"))
	}
	if err := ptyIOCTL(master, syscall.TIOCGPGRP, unsafe.Pointer(&pgid)); err != nil {
		return fail(err)
	}
	if pgid <= 0 {
		return fail(fmt.Errorf("invalid foreground group"))
	}
	members, err := census.Members(ctx, int64(pgid))
	if err != nil {
		if ctx.Err() != nil {
			return false, ctx.Err()
		}
		return fail(err)
	}
	if err := validateForeground(sessionID, int64(sid), int64(pgid), members); err != nil {
		return fail(err)
	}
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if len(members) == 0 {
		return false, nil
	}
	// The kernel atomically selects a foreground group in this controlling
	// session. The complete boundary proof covers eligible switches as well.
	l.attempted = true // Even an ioctl failure cannot authorize another signal.
	if err := signalPTY(master); err != nil {
		return fail(err)
	}
	return true, nil
}

func validateForeground(sessionID, observedSession, pgid int64, members []foregroundMember) error {
	if sessionID <= 0 || sessionID != observedSession || pgid <= 0 {
		return fmt.Errorf("invalid controlled foreground identity")
	}
	for _, member := range members {
		if member.PID <= 0 || member.PID > 2147483647 || member.PGID != pgid ||
			member.SessionID != sessionID || !member.Controlled {
			return fmt.Errorf("unproved foreground member")
		}
	}
	return nil
}

func signalPTY(master *os.File) error {
	raw, err := master.SyscallConn()
	if err != nil {
		return err
	}
	var native error
	err = raw.Control(func(fd uintptr) {
		_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, fd, syscall.TIOCSIG, uintptr(syscall.SIGINT))
		if errno != 0 {
			native = errno
		}
	})
	return errors.Join(err, native)
}
