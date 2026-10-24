package protocol

import (
	"bufio"
	"encoding/binary"
	"fmt"
	"io"
)

// ReadPrivate reads one declared-arity frame. Retain the same buffered reader
// across calls so concatenated frames remain available. Between-frame EOF is
// io.EOF; a partial frame is an error. Terminal EOF/reap is an actor obligation.
func ReadPrivate(r *bufio.Reader, direction PrivateDirection) (PrivateMessage, error) {
	var frame []byte
	readField := func() (string, error) {
		start := len(frame)
		for {
			part, err := r.ReadSlice(0)
			if len(part) > MaxFrameBytes-len(frame) {
				return "", fmt.Errorf("private frame too large")
			}
			frame = append(frame, part...)
			if err == bufio.ErrBufferFull {
				continue
			}
			if err != nil {
				if err == io.EOF && len(frame) > 0 {
					err = io.ErrUnexpectedEOF
				}
				return "", err
			}
			return string(frame[start : len(frame)-1]), nil
		}
	}
	prefix, err := readField()
	if err != nil {
		return nil, err
	}
	if prefix != privatePrefix {
		return nil, fmt.Errorf("invalid private prefix")
	}
	kind, err := readField()
	if err != nil {
		return nil, err
	}
	m := privateModel(kind, direction)
	if m == nil {
		return nil, fmt.Errorf("unknown private type or wrong direction")
	}
	for range wireFields(modelValue(m)) {
		if _, err := readField(); err != nil {
			return nil, err
		}
	}
	return DecodePrivate(frame, direction)
}

// DecodeHelper validates one complete length-prefixed exchange direction.
func DecodeHelper(frame []byte, direction HelperDirection, phase HelperPhase) (HelperMessage, error) {
	if len(frame) < 4 {
		return nil, fmt.Errorf("short helper length")
	}
	n := binary.BigEndian.Uint32(frame[:4])
	if n == 0 || n > MaxFrameBytes || uint64(n)+4 != uint64(len(frame)) {
		return nil, fmt.Errorf("invalid helper length or trailing data")
	}
	fields, err := splitFields(frame[4:], helperPrefix)
	if err != nil {
		return nil, err
	}
	m := helperModel(fields[1], direction, phase)
	if m == nil {
		return nil, fmt.Errorf("unknown helper type, direction or phase")
	}
	if err := decodeFields(fields[2:], m); err != nil {
		return nil, err
	}
	return m, validateWireModel(m)
}

// EncodeHelper includes the four-byte big-endian payload length.
func EncodeHelper(m HelperMessage, direction HelperDirection, phase HelperPhase) ([]byte, error) {
	switch m.(type) {
	case HelperPromptState, *HelperPromptState, HelperStartupReady, *HelperStartupReady, HelperCompletionReady, *HelperCompletionReady, HelperSourceRequest, *HelperSourceRequest, HelperPreparedRequest, *HelperPreparedRequest,
		HelperGateRequest, *HelperGateRequest, HelperAccepted, *HelperAccepted, HelperGateAccepted, *HelperGateAccepted, HelperSourceReply, *HelperSourceReply:
	default:
		return nil, fmt.Errorf("unsupported helper model")
	}
	if nilModel(m) {
		return nil, fmt.Errorf("nil helper model")
	}
	payload, err := encodeFields(helperPrefix, m.helperType(), m)
	if err != nil {
		return nil, err
	}
	frame := make([]byte, 4, len(payload)+4)
	binary.BigEndian.PutUint32(frame, uint32(len(payload)))
	frame = append(frame, payload...)
	_, err = DecodeHelper(frame, direction, phase)
	if err != nil {
		return nil, err
	}
	return frame, nil
}

// ReadHelper requires EOF after the payload. The transport must supply its
// deadline, recvmsg ancillary checks, and request half-close independently.
func ReadHelper(r io.Reader, direction HelperDirection, phase HelperPhase) (HelperMessage, error) {
	prefix := make([]byte, 4)
	if _, err := io.ReadFull(r, prefix); err != nil {
		return nil, err
	}
	n := binary.BigEndian.Uint32(prefix)
	if n == 0 || n > MaxFrameBytes {
		return nil, fmt.Errorf("invalid helper payload length")
	}
	frame := make([]byte, int(n)+4)
	copy(frame, prefix)
	if _, err := io.ReadFull(r, frame[4:]); err != nil {
		return nil, err
	}
	var trailing [1]byte
	if count, err := io.ReadFull(r, trailing[:]); count != 0 || err != io.EOF {
		return nil, fmt.Errorf("helper trailing data or missing EOF")
	}
	return DecodeHelper(frame, direction, phase)
}
