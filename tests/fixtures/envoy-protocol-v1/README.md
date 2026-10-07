# Envoy v1 static conformance corpus

This corpus freezes the approved A2.7 protocol and A2.8/A2.9 delivery order.
It contains declarative inputs and expectations. Passing its Go checks proves
static corpus consistency and codec behavior; it does not prove Bash execution,
socket behavior, process cleanup, deadline dispatch, or complete v1 conformance.

Run from `runtime/envoy`: `go test -count=1 -race ./...`.
The fixtures live outside the Go module, so disable test caching to ensure
fixture-only changes execute the checks.

`controller.jsonl`, `envoy.jsonl`, `public-invalid.jsonl`, and
`public-maximum.jsonl` retain the B1.1 public wire corpus. `private.jsonl` and
`helper.jsonl` retain the B1.2 ordered NUL fields. `awsh-frames.json` freezes
their exact hexadecimal payloads and frames, including the helper's four-byte
big-endian payload-length prefix. It includes both empty and active terminal
operation IDs and the startup/completion helper arities.

`inventory.json` pins the complete shared inventory's normative clauses and
the delivery catalogue's leaf obligations to their approved source bytes. It
also binds each of the four approved normative documents by path and SHA-256;
any missing, duplicate, unknown, or changed authority requires explicit
reconciliation before the corpus can be accepted.
`cases.jsonl` assigns stable case IDs, static fixture references, implementation
owners, exact prerequisite text, and last-prerequisite closure owners.
`traces.jsonl` retains scenarios and expected ordering, deadline, failure, and
crossed-outcome predicates. Clause records preserve the full requirement;
expanded matrix records bind a concrete synthetic input to its mapped
requirement, winner, result eligibility, fatality, signal count, publication
decision, and ordered events. Active deadline cases retain their original
non-resetting owner/name/budget; deadline-free cases retain no epoch. Package
records retain isolated acceptance and pending crossings rather than replacing
the cases with a package-level pass.
Every startup, digest, and hexadecimal frame example also has a direct case
binding. Startup traces distinguish independent controller and Envoy deadlines;
an individual sender's control-write timer never governs a lifecycle crossing.

`startup.jsonl` uses synthetic ASCII bytes only. Build identity is absent.
`inspection.jsonl` freezes literal hash inputs and lowercase SHA-256 results:
file bytes, the native `directory` tag/encoding, and protocol `directory-v2`
fixed 65-byte entries. It includes an omitted nested FIFO. These are algorithm
examples, not observations of a filesystem or live Bash path expansion.

Every runtime-test record is pending and has no executable test locator.
Later owners must replace that status with an actual test and retained result
when their implementation exists. The closure owner must rerun crossings with
their real actors; a deterministic peer cannot satisfy the final B8.4 gate.
Build-dependent status, diagnostic, signal-table, startup-export, and Readline
values remain absent until genuine B2.8 qualification supplies them. Numeric
signal scenarios refer to the selected build, without advertising a build or
inventing a supported digest. C/D obligations stay with their existing owners.

IDs are durable: append new cases, preserve existing meanings, and do not reuse
an ID for a different contract. A design amendment requires explicit source
reconciliation and fresh review; a changed source hash fails completeness.
Static checks reject duplicate IDs, missing clauses or catalogue obligations,
unmapped traces, dangling fixture references, ownership/prerequisite drift,
invented runtime passes, concrete unqualified build values, and altered wire
bytes. Runtime proofs outside B1 stay pending even when all static checks pass.
