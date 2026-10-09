package submission

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/omry/omegaflow/runtime/envoy/bashbuild"
	"github.com/omry/omegaflow/runtime/envoy/protocol"
)

func TestCheckFailsClosedWithoutQualification(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if _, err := Check(ctx, example(":"), "unknown", bashbuild.Target{}, nil); err == nil {
		t.Fatal("unqualified production check")
	}
	if _, err := check(context.Background(), example(":"), func() error { return nil }); err == nil {
		t.Fatal("checker created its own epoch")
	}
}

func selectedDigest(t *testing.T) func() error {
	t.Helper()
	b, err := os.ReadFile("/bin/bash")
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(b)
	if expected := os.Getenv("OMEGAFLOW_CANDIDATE_DIGEST"); expected != "" && hex.EncodeToString(digest[:]) != expected {
		t.Fatal("candidate digest mismatch")
	}
	// Only this test binary admits an unqualified measured candidate. The public
	// Check entrypoint always uses the generated qualified table.
	return func() error {
		b, err := os.ReadFile("/bin/bash")
		if err != nil {
			return err
		}
		if sha256.Sum256(b) != digest {
			return errors.New("digest changed")
		}
		return nil
	}
}

func TestSelectedBashChecker(t *testing.T) {
	verify := selectedDigest(t)
	for _, tc := range []struct{ name, source, code string }{
		{"minimum", "#", ""}, {"maximum", strings.Repeat("#", protocol.MaxSourceBytes), ""},
		{"multiline", "printf 'é\\n'\nprintf 'second\\n'", ""}, {"comments", "# } fake suffix\n# trailing comment", ""},
		{"quotes", "printf '%s' '} \"quoted\"'", ""}, {"heredoc", "cat <<'EOF'\n} text\nEOF", ""},
		{"trailing-LF", "printf ok\n", ""}, {"backslash", "printf ok \\\n", ""},
		{"incomplete", "if true; then", "source-syntax"}, {"frame-only", "}", "source-syntax"},
		{"unterminated-heredoc", "cat <<'EOF'\nmissing terminator", "source-syntax"},
		{"extglob", "printf @(a|b)", "source-syntax"}, {"reserved", "printf __OMEGAFLOW_AWSH_BAD", "source-policy"},
		{"no-execution", "printf ran > /tmp/omegaflow-checker-must-not-execute", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			calls := 0
			v := func() error { calls++; return verify() }
			_, err := check(ctx, example(tc.source), v)
			if tc.code == "" {
				if err != nil {
					t.Fatal(err)
				}
				if calls != 2 {
					t.Fatalf("rehash calls %d", calls)
				}
			} else {
				var rejected *Rejection
				if !errors.As(err, &rejected) || rejected.Code != tc.code {
					t.Fatalf("want %s, got %v", tc.code, err)
				}
			}
		})
	}
	if _, err := os.Stat("/tmp/omegaflow-checker-must-not-execute"); !os.IsNotExist(err) {
		t.Fatal("checker executed authored source")
	}
}

func TestCheckerKeepsDeadlineAndIntegrityErrors(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	deadline, _ := ctx.Deadline()
	calls := 0
	integrity := errors.New("second digest mismatch")
	_, err := check(ctx, example(":"), func() error {
		calls++
		now, _ := ctx.Deadline()
		if now != deadline {
			t.Fatal("epoch reset")
		}
		if calls == 2 {
			return integrity
		}
		return nil
	})
	if err != integrity || calls != 2 {
		t.Fatalf("%d %v", calls, err)
	}
	ctx, cancel = context.WithTimeout(context.Background(), time.Second)
	cancel()
	calls = 0
	_, err = check(ctx, example(":"), func() error { calls++; return nil })
	if err != context.Canceled || calls != 0 {
		t.Fatal("cancelled checker launched")
	}
}

func TestCheckerRejectsDescriptorInheritance(t *testing.T) {
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	defer w.Close()
	if err := protectedDescriptors(); err != nil {
		t.Fatal(err)
	}
	fd := int(r.Fd())
	if _, _, e := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fd), syscall.F_SETFD, 0); e != 0 {
		t.Fatal(e)
	}
	defer syscall.CloseOnExec(fd)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_, err = check(ctx, example(":"), func() error { return nil })
	var rejection *Rejection
	if err == nil || errors.As(err, &rejection) {
		t.Fatalf("descriptor integrity relabelled: %v", err)
	}
}
