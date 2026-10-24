//go:build linux

package inspection

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"unicode/utf8"

	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

// This bounded pipe exchange is internal to Envoy. It publishes no session
// event: R carries the existing result array; F carries exactly code/message.
func encodeWorkerReply(results []protocol.InspectionResult, runErr error) ([]byte, error) {
	tag := byte('R')
	var payload any = results
	if runErr != nil {
		var failed *Failure
		if !errors.As(runErr, &failed) || !validWorkerFailure(failed.Code, failed.Message) {
			return nil, fmt.Errorf("invalid inspection worker failure")
		}
		tag, payload = 'F', []string{failed.Code, failed.Message}
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	if len(data)+1 > protocol.MaxFrameBytes {
		return encodeWorkerReply(nil, &Failure{"inspection-limit", "inspection result exceeds frame bound"})
	}
	return append([]byte{tag}, data...), nil
}

func decodeWorkerReply(data []byte, plans []protocol.ResolvedInspection) ([]protocol.InspectionResult, error) {
	if len(data) < 2 || len(data) > protocol.MaxFrameBytes || !utf8.Valid(data) {
		return nil, fmt.Errorf("invalid inspection worker result size or encoding")
	}
	switch data[0] {
	case 'F':
		var fields []json.RawMessage
		if err := json.Unmarshal(data[1:], &fields); err != nil || len(fields) != 2 {
			return nil, fmt.Errorf("invalid inspection worker failure")
		}
		frame := []byte(`{"schema":"` + protocol.Schema + `","type":"operation_failed","seq":1,"operation_id":"inspection","cwd":"/","output_start":0,"output_through":0,"code":`)
		frame = append(frame, fields[0]...)
		frame = append(frame, []byte(`,"message":`)...)
		frame = append(frame, fields[1]...)
		frame = append(frame, []byte("}\n")...)
		message, err := protocol.DecodeEnvoy(frame)
		if err != nil {
			return nil, err
		}
		failed := message.(*protocol.OperationFailed)
		if !validWorkerFailure(failed.Code, failed.Message) {
			return nil, fmt.Errorf("invalid inspection worker failure code")
		}
		return nil, &Failure{failed.Code, failed.Message}
	case 'R':
		var entries []json.RawMessage
		if err := json.Unmarshal(data[1:], &entries); err != nil || entries == nil || len(entries) != len(plans) || len(entries) > protocol.MaxInspections {
			return nil, fmt.Errorf("invalid inspection worker results")
		}
		results := make([]protocol.InspectionResult, len(entries))
		for i, entry := range entries {
			result, err := decodeWorkerResult(entry)
			if err != nil {
				return nil, err
			}
			plan := plans[i]
			if result.InspectionID != plan.InspectionID || result.Kind != plan.Kind || result.ProducerID != plan.ProducerID || result.OutputID != plan.OutputID {
				return nil, fmt.Errorf("inspection worker result does not match plan")
			}
			results[i] = result
		}
		return results, nil
	default:
		return nil, fmt.Errorf("unknown inspection worker result tag")
	}
}

// A local validation envelope supplies the existing full codec's Unicode,
// duplicate-key, scalar and shape checks without publishing a session event.
// Keep the raw entry: decoding and re-encoding first could hide invalid Unicode.
func decodeWorkerResult(entry []byte) (protocol.InspectionResult, error) {
	frame := []byte(`{"schema":"` + protocol.Schema + `","type":"operation_completed","seq":1,"operation_id":"inspection","status":0,"cwd":"/","output_start":0,"output_through":0,"inspection_results":[`)
	frame = append(frame, entry...)
	frame = append(frame, []byte("]}\n")...)
	message, err := protocol.DecodeEnvoy(frame)
	if err != nil {
		return protocol.InspectionResult{}, err
	}
	return message.(*protocol.OperationCompleted).InspectionResults[0], nil
}

func validWorkerFailure(code, message string) bool {
	if len(message) == 0 || len(message) > protocol.MaxMessageBytes || !utf8.ValidString(message) || strings.ContainsRune(message, 0) {
		return false
	}
	switch code {
	case "inspection-resolution", "inspection-missing", "inspection-type", "inspection-limit", "inspection-unstable", "inspection-read":
		return true
	}
	return false
}
