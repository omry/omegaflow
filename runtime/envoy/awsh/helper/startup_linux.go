package helper

import (
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

const SocketPath = "/run/omegaflow/session/bash/helper.sock"

// StartupCommand implements only startup arities; later helper phases belong
// to their owning implementation leaves. The caller emits no terminal output.
func StartupCommand(args []string) error {
	if len(args) < 2 || args[0] != "--socket="+SocketPath {
		return fmt.Errorf("invalid helper invocation")
	}
	var request protocol.HelperMessage
	switch args[1] {
	case "prompt-state":
		if len(args) != 5 {
			return fmt.Errorf("invalid prompt-state arity")
		}
		status, err := strconv.ParseInt(args[2], 10, 64)
		if err != nil || strconv.FormatInt(status, 10) != args[2] {
			return fmt.Errorf("invalid status")
		}
		state, err := CapturePromptState(status, args[3], args[4])
		if err != nil {
			return err
		}
		request = protocol.HelperPromptState{PromptState: state}
	case "prompt-ready":
		if len(args) != 2 {
			return fmt.Errorf("invalid startup prompt-ready arity")
		}
		request = protocol.HelperStartupReady{}
	default:
		return fmt.Errorf("unsupported helper request")
	}
	// The actor supervises the blocked helper under its current phase deadline.
	c, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: SocketPath, Net: "unix"})
	if err != nil {
		return err
	}
	reply, err := Exchange(c, protocol.HelperStartup, request)
	if err != nil {
		return err
	}
	if _, ok := reply.(*protocol.HelperAccepted); !ok {
		return fmt.Errorf("wrong helper reply")
	}
	return nil
}

func CapturePromptState(status int64, history, editing string) (protocol.PromptState, error) {
	// syscall.Getwd obtains the physical path; os.Getwd may prefer logical PWD.
	cwd, err := syscall.Getwd()
	if err != nil {
		return protocol.PromptState{}, err
	}
	env := map[string]string{}
	for _, entry := range os.Environ() {
		name, value, ok := strings.Cut(entry, "=")
		if !ok || name == "" {
			return protocol.PromptState{}, fmt.Errorf("invalid exported environment")
		}
		if _, duplicate := env[name]; duplicate {
			return protocol.PromptState{}, fmt.Errorf("duplicate exported name")
		}
		env[name] = value
	}
	logical := env["PWD"]
	if !filepath.IsAbs(logical) {
		logical = ""
	} else {
		physical, e1 := os.Stat(cwd)
		named, e2 := os.Stat(logical)
		if e1 != nil || e2 != nil || !os.SameFile(physical, named) {
			logical = ""
		}
	}
	state := protocol.PromptState{Status: status, HistExpand: history, EditingMode: editing, PhysicalCWD: cwd, LogicalCWD: logical, ExportedEnv: env}
	_, err = protocol.EncodeHelper(protocol.HelperPromptState{PromptState: state}, protocol.HelperToAwsh, protocol.HelperStartup)
	return state, err
}
