package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestInvalidTerminalSizeExitsAsCommandLineUsage(t *testing.T) {
	var stderr bytes.Buffer
	if status := run([]string{"--columns", "0"}, &stderr); status != 2 {
		t.Fatalf("run returned %d, want 2", status)
	}
	if output := stderr.String(); !strings.Contains(output, "terminal size is out of range") {
		t.Fatalf("stderr did not explain invalid terminal size: %q", output)
	}
}

func TestUnsupportedPositionalArgumentExitsAsCommandLineUsage(t *testing.T) {
	var stderr bytes.Buffer
	if status := run([]string{"unexpected"}, &stderr); status != 2 {
		t.Fatalf("run returned %d, want 2", status)
	}
	if output := stderr.String(); !strings.Contains(output, "positional arguments are not supported") {
		t.Fatalf("stderr did not explain unsupported argument: %q", output)
	}
}

func TestHelpExitsSuccessfully(t *testing.T) {
	var stderr bytes.Buffer
	if status := run([]string{"--help"}, &stderr); status != 0 {
		t.Fatalf("run returned %d, want 0", status)
	}
	if output := stderr.String(); !strings.Contains(output, "Usage of omegaflow-envoy:") {
		t.Fatalf("stderr did not contain help: %q", output)
	}
}
