package launch

import (
	"fmt"
	"os"
	"os/user"
	"strconv"
	"strings"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// resolveInspections expands only exported names and a leading home expression.
// Paths remain lexical plans: only Envoy may inspect or canonicalize them.
func resolveInspections(state protocol.PromptState, requests []protocol.Inspection) ([]protocol.ResolvedInspection, error) {
	return resolvePlans(state, requests, func(name string) (string, bool) {
		var u *user.User
		var err error
		if name == "" {
			u, err = user.LookupId(strconv.Itoa(os.Geteuid()))
		} else {
			u, err = user.Lookup(name)
		}
		if err != nil {
			return "", false
		}
		return u.HomeDir, true
	})
}

func resolvePlans(state protocol.PromptState, requests []protocol.Inspection, home func(string) (string, bool)) ([]protocol.ResolvedInspection, error) {
	result := make([]protocol.ResolvedInspection, 0, len(requests))
	for _, request := range requests {
		path := expandExported(request.Path, state.ExportedEnv)
		if strings.HasPrefix(path, "~") {
			name, suffix, found := strings.Cut(path[1:], "/")
			value, ok := state.ExportedEnv["HOME"]
			if name != "" || !ok {
				value, ok = home(name)
			}
			if ok {
				path = strings.TrimRight(value, "/")
				if found {
					path += "/" + suffix
				}
				if path == "" {
					path = "/"
				}
			}
		}
		if !strings.HasPrefix(path, "/") {
			path = state.PhysicalCWD + "/" + path
		}
		result = append(result, protocol.ResolvedInspection{InspectionID: request.InspectionID, Kind: request.Kind, ResolvedPath: path, ProducerID: request.ProducerID, OutputID: request.OutputID})
	}
	// Reuse the exact private-plan bounds and duplicate-identifier checks.
	_, err := protocol.EncodePrivate(protocol.PrivateCompleted{OperationID: "plan", Status: state.Status, PhysicalCWD: state.PhysicalCWD, Inspections: result}, protocol.AwshToEnvoy)
	if err != nil {
		return nil, fmt.Errorf("resolved inspection plan: %w", err)
	}
	return result, nil
}

func expandExported(path string, env map[string]string) string {
	var out strings.Builder
	for i := 0; i < len(path); {
		if path[i] != '$' {
			out.WriteByte(path[i])
			i++
			continue
		}
		start := i
		i++
		braced := i < len(path) && path[i] == '{'
		if braced {
			i++
		}
		nameStart := i
		if braced {
			for i < len(path) && path[i] != '}' {
				i++
			}
		} else {
			for i < len(path) && nameByte(path[i], i == nameStart) {
				i++
			}
		}
		end := i
		valid := end > nameStart
		for n := nameStart; n < end; n++ {
			valid = valid && nameByte(path[n], n == nameStart)
		}
		if braced {
			valid = valid && i < len(path)
			if i < len(path) {
				i++
			}
		}
		if value, ok := env[path[nameStart:end]]; valid && ok {
			out.WriteString(value)
		} else {
			out.WriteString(path[start:i])
		}
	}
	return out.String()
}

func nameByte(b byte, first bool) bool {
	return b == '_' || b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z' || !first && b >= '0' && b <= '9'
}
