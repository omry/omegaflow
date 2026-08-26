package protocol

import (
	"testing"
	"time"
)

func TestDirectoryDigestInjectivity(t *testing.T) {
	// The delimited native encoding is not injective over arbitrary file
	// bytes: one file whose contents contain the next entry's framing
	// hashes identically to two files. The framed directory-v2 encoding
	// keeps the two trees distinct.
	oneFile := []TreeEntry{
		{Path: "a", Kind: TreeEntryFile, Content: []byte("first\x00file\x00b\x00second")},
	}
	twoFiles := []TreeEntry{
		{Path: "a", Kind: TreeEntryFile, Content: []byte("first")},
		{Path: "b", Kind: TreeEntryFile, Content: []byte("second")},
	}
	nativeOne, err := DirectoryDigestNative(oneFile)
	if err != nil {
		t.Fatalf("native one: %v", err)
	}
	nativeTwo, err := DirectoryDigestNative(twoFiles)
	if err != nil {
		t.Fatalf("native two: %v", err)
	}
	if nativeOne != nativeTwo {
		t.Fatal("expected the native delimited encoding to collide on this framing-shaped content")
	}
	framedOne, err := DirectoryDigestV2(oneFile)
	if err != nil {
		t.Fatalf("framed one: %v", err)
	}
	framedTwo, err := DirectoryDigestV2(twoFiles)
	if err != nil {
		t.Fatalf("framed two: %v", err)
	}
	if framedOne == framedTwo {
		t.Fatal("directory-v2 must keep distinct trees distinct")
	}
}

func TestDirectoryDigestRules(t *testing.T) {
	tree := fixtureTree()

	// Entry order does not affect the digest: traversal is sorted
	// relative-path order.
	reversed := make([]TreeEntry, 0, len(tree))
	for index := len(tree) - 1; index >= 0; index-- {
		reversed = append(reversed, tree[index])
	}
	sorted, err := DirectoryDigestV2(tree)
	if err != nil {
		t.Fatalf("sorted: %v", err)
	}
	shuffled, err := DirectoryDigestV2(reversed)
	if err != nil {
		t.Fatalf("reversed: %v", err)
	}
	if sorted != shuffled {
		t.Fatal("digest must be order independent")
	}

	// A nested special entry is omitted from the digest.
	withoutSpecial := make([]TreeEntry, 0, len(tree))
	for _, entry := range tree {
		if entry.Kind != TreeEntrySpecial {
			withoutSpecial = append(withoutSpecial, entry)
		}
	}
	pruned, err := DirectoryDigestV2(withoutSpecial)
	if err != nil {
		t.Fatalf("pruned: %v", err)
	}
	if pruned != sorted {
		t.Fatal("special entries are omitted from the digest")
	}

	// A symlink is recorded as a link and never followed: changing its
	// target changes the digest.
	retargeted := make([]TreeEntry, len(tree))
	copy(retargeted, tree)
	for index := range retargeted {
		if retargeted[index].Kind == TreeEntryLink {
			retargeted[index].Target = "elsewhere.svg"
		}
	}
	changed, err := DirectoryDigestV2(retargeted)
	if err != nil {
		t.Fatalf("retargeted: %v", err)
	}
	if changed == sorted {
		t.Fatal("a symlink's exact target participates in the digest")
	}

	// Invalid trees fail closed.
	if _, err := DirectoryDigestV2([]TreeEntry{{Path: "/abs", Kind: TreeEntryFile}}); err == nil {
		t.Fatal("absolute entry paths must fail")
	}
	if _, err := DirectoryDigestV2([]TreeEntry{{Path: "a", Kind: "socket"}}); err == nil {
		t.Fatal("unknown entry kinds must fail")
	}
	if _, err := DirectoryDigestV2([]TreeEntry{{Path: "a", Kind: TreeEntryFile}, {Path: "a", Kind: TreeEntryDir}}); err == nil {
		t.Fatal("duplicate entry paths must fail")
	}
}

func TestDeadlineTable(t *testing.T) {
	if ConnectDeadline != 10*time.Second || HelloReadyDeadline != 10*time.Second {
		t.Fatal("the connect and handshake deadlines are 10 seconds")
	}
	fiveSecond := []time.Duration{
		ControlWriteDeadline, HelperExchangeDeadline, OperationStartDeadline,
		InputBarrierWait, CancellationGracePeriod, OperationCleanupDeadline,
		ResizeTransactionDeadline, FinalDrainDeadline, ReadlineEntryDeadline,
	}
	for _, deadline := range fiveSecond {
		if deadline != 5*time.Second {
			t.Fatalf("expected a five-second deadline, got %s", deadline)
		}
	}
	if OutputMarkCadence != 10*time.Millisecond {
		t.Fatal("the output-mark cadence is 10 milliseconds")
	}
	seen := make(map[string]bool)
	for _, row := range DeadlineTable {
		if row.Name == "" || row.Owner == "" || row.Epoch == "" || row.Covers == "" || row.Expiry == "" || row.Duration <= 0 {
			t.Fatalf("incomplete deadline row: %+v", row)
		}
		if seen[row.Name] {
			t.Fatalf("duplicate deadline row %s", row.Name)
		}
		seen[row.Name] = true
	}
	if len(DeadlineTable) != 15 {
		t.Fatalf("the deadline table freezes 15 owned epochs, found %d", len(DeadlineTable))
	}
}
