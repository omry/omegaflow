//go:build linux

package submission

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"
)

// The native qualification runner injects each output/status fault into a
// disposable image, restores /bin/bash, and rechecks its bound digest. This
// test-only bypass does not admit the fixture into either production consumer.
func TestQualificationCheckerOutput(t *testing.T) {
	fault := os.Getenv("OMEGAFLOW_QUALIFICATION_CHECKER_FAULT")
	if fault == "" {
		return
	}
	if fault != "stdout" && fault != "stderr" && fault != "nonzero" {
		t.Fatal("unknown checker fault")
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	calls := 0
	_, err := check(ctx, example("#"), func() error { calls++; return nil })
	var rejected *Rejection
	if !errors.As(err, &rejected) || rejected.Code != "source-syntax" || calls != 1 {
		t.Fatalf("%s output accepted: calls=%d error=%v", fault, calls, err)
	}
}
