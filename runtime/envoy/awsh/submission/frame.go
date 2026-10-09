// Package submission implements the checked bytes at the Bash submission boundary.
// Active-operation ownership and start coordination belong to Awsh's caller.
package submission

import (
	"fmt"
	"strings"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// Frame returns the exact canonical unit, without the helper's final x marker.
// The strict helper codec also validates scalar, source and aggregate bounds.
func Frame(m protocol.HelperSourceReply) (string, error) {
	if _, err := protocol.EncodeHelper(m, protocol.AwshToHelper, protocol.HelperSource); err != nil {
		return "", err
	}
	redirect := ""
	if m.ExecutionShape == "split" {
		redirect = " > '" + m.StdoutFIFO + "' 2> '" + m.StderrFIFO + "'"
	}
	return fmt.Sprintf("{\n"+
		"__OMEGAFLOW_AWSH_START_RELEASED\n"+
		"__OMEGAFLOW_AWSH_ENTER %d %s %s\n"+
		"__OMEGAFLOW_AWSH_INNER_ENTERED=0\n"+
		"{\n"+
		"__OMEGAFLOW_AWSH_INNER_ENTERED=1\n"+
		"__OMEGAFLOW_AWSH_RETURN %d\n"+
		"%s\n\n"+
		"__OMEGAFLOW_AWSH_RETURN \"$?\" && (( 1 ))\n"+
		"}%s\n"+
		"__OMEGAFLOW_AWSH_STATUS=$?\n"+
		"if [[ $__OMEGAFLOW_AWSH_INNER_ENTERED != 1 ]]; then\n"+
		"__OMEGAFLOW_AWSH_FAIL_STOP\n"+
		"fi\n"+
		"__OMEGAFLOW_AWSH_RETURN \"$__OMEGAFLOW_AWSH_STATUS\" && (( 1 ))\n"+
		"}", m.Status, m.HistExpand, m.EditingMode, m.Status, m.Source, redirect), nil
}

// Rejection is recoverable only before submit. Runtime/checker integrity errors
// are ordinary errors, so a caller cannot relabel them as source rejection.
type Rejection struct{ Code, Message string }

func (r *Rejection) Error() string { return r.Code + ": " + r.Message }

func checkedFrame(m protocol.HelperSourceReply) (string, error) {
	if strings.Contains(m.Source, "__OMEGAFLOW_AWSH_") {
		return "", &Rejection{"source-policy", "reserved adapter namespace in source"}
	}
	return Frame(m)
}
