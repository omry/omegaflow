# B8.3.1 captured shell-exit deadlines

The runner uses the existing qualified-native staging. Each case runs a
dedicated Envoy test process with actual Awsh, selected Bash, private codec,
process census, split resources, public sockets and Awsh Wait. It introduces
no production entrypoint or actor.

The case map assigns the exact 67 B8.3.1 obligations from the original B8.3
corpus. Start crossings cover both PTY and split execution, queued cancel,
partial execute, every handshake boundary and the accepted start-released
boundary. Outstanding execute and continue watermarks are distinct cases.
Captured and final-drain expiry cases use real original deadlines. They test
both due in one dispatch turn, and closure completion before later final-drain
expiry. No operation result is allowed for a discarded or fatal start.

Retained Go overlays freeze existing writer and owner boundaries and observe
the original private decoder. A partial-write case writes an actual one-byte
prefix through the existing exact write loop. A complete-execute case waits
for actual Awsh registration; sender write completion alone is insufficient.
Queued-cancel cases retain both an observed terminal fact and the request at
the specified owner boundary. They do not replace the shell with a peer or
claim every possible network arrival order is the same dispatch turn.

Fatal start tests join the original private reader before reading its
remaining buffered bytes through that same decoder. They require actual
terminal frame, EOF, zero-status Awsh reap and original-epoch disposal.
Successful pre-write shell exits consume the exact Start handoff. Private
closure and split rollback retain their captured epoch, while remaining output
and public closure use the independently running final-drain epoch. A late
continuation wait must retain its cancellation after terminal input ownership
has been reset; live invalid ownership still rejects.

Run the complete native gate:

```sh
python3 tests/envoy-captured-closure/run.py \
  --qualification /absolute/qualification-directory \
  --output /new/evidence-directory
```

The complete runner requires all 67 mapped cases and an additional withheld
continuation-reap proof on each of the three genuine Linux amd64 Bash targets.
It also requires six omission controls to fail with their intended invariant
diagnostics. Five controls run actual actors; the cancellation-entry control
uses the deterministic focused regression for the race observed by the native
matrix. Source overlays do not modify the owning files.

`--release` and repeated `--case` select explicit development subsets.
`--native-only` omits controls; `--proof-only` runs controls alone. These results
cannot set `native_acceptance_complete`. A full native pass still needs the
remaining frozen-source validation, full reviews and exact-head PR approval
before B8.3.1 is complete. ARM64 compilation is not qualification.

B8.3.2 owns the separate fatal protocol-error phase matrix. B8.4 still owns the
full actual-actor B1–B8 conformance gate. No corpus requirement is removed by
the early B8.3 split. Reploy termination integration remains with its later
owner; this assembly cannot claim a production capture entrypoint.
