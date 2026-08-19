---
design: envoy-protocol-v1.md
algorithm: sha256 over the document's exact bytes
sha256: e3f3065ac6e0982026f8f1ca31b3a247cf49c3dc11f64be0386ee6be96e45604
reviewed: 2026-08-20, deep-design-review-loop rounds 1-4
verdict: clean
---

# Review record — envoy-protocol-v1.md

Attestation: the sha256 above is the document as the final clean round
(round 4) reviewed it. Re-hash the document against it to verify the design
is unchanged since its last clean review; a mismatch means the verdict no
longer attaches.

## Review decisions

No standing rejections or deferrals: all twelve findings of the
2026-08-20 review campaign (R1-1..R1-11, R3-1) were accepted by owner
disposition and fixed in the successor PR. The full campaign trail lived in
temp/reviews/pr-1 (working state; reconstructible).
