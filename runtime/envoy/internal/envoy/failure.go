package envoy

import "fmt"

// FailureClass is the stable outer classification reported by the Envoy.
type FailureClass string

const (
	FailureConnection FailureClass = "connection"
	FailureHandshake  FailureClass = "handshake"
	FailureProtocol   FailureClass = "protocol"
	FailurePTY        FailureClass = "pty"
	FailureAwsh       FailureClass = "awsh"
	FailureShell      FailureClass = "shell"
	FailureResize     FailureClass = "resize"
	FailureCancel     FailureClass = "cancellation"
	FailureDrain      FailureClass = "drain"
	FailureCleanup    FailureClass = "cleanup"
)

// Failure retains a stable class and diagnostic code while preserving the
// underlying cause for the Reploy bootstrap log.
type Failure struct {
	Class FailureClass
	Code  string
	Err   error
}

func (failure *Failure) Error() string {
	return fmt.Sprintf("%s/%s: %v", failure.Class, failure.Code, failure.Err)
}

func (failure *Failure) Unwrap() error { return failure.Err }

func fail(class FailureClass, code string, err error) error {
	return &Failure{Class: class, Code: code, Err: err}
}

// ExitCode gives each stable failure class a distinct process result.
func ExitCode(err error) int {
	failure, ok := err.(*Failure)
	if !ok {
		return 1
	}
	switch failure.Class {
	case FailureConnection:
		return 10
	case FailureHandshake:
		return 11
	case FailureProtocol:
		return 12
	case FailurePTY:
		return 13
	case FailureAwsh:
		return 14
	case FailureShell:
		return 15
	case FailureResize:
		return 16
	case FailureCancel:
		return 17
	case FailureDrain:
		return 18
	case FailureCleanup:
		return 19
	default:
		return 1
	}
}
