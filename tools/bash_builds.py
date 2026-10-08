"""Build-table admission, consumer generation and isolated qualification tooling.

This developer tool never acquires Bash or enables a runtime session. A probe
is trusted local qualification code; its evidence is compatibility evidence,
not protection against a workload author forging a table.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import stat
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "runtime/envoy/bash-builds.json"
BASH = Path("/bin/bash")
LIMIT = 2 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def shape(value, fields):
    require(type(value) is dict and set(value) == set(fields.split()), "invalid object fields")


def text(value):
    require(type(value) is str and 0 < len(value.encode("utf-8")) <= 4096 and "\0" not in value, "invalid text")


def digest(value):
    require(type(value) is str and HEX.fullmatch(value), "invalid lowercase SHA-256")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pairs(items):
    out = {}
    for key, value in items:
        require(key not in out, "duplicate JSON field")
        out[key] = value
    return out


def decode(data):
    require(len(data) <= LIMIT, "JSON size limit")
    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, "nonfinite JSON"))


def read(path):
    with Path(path).open("rb") as stream:
        return decode(stream.read(LIMIT + 1))


def absolute(value):
    text(value)
    require(value.startswith("/") and str(Path(value)) == value and ".." not in Path(value).parts, "invalid absolute path")


def target(value):
    shape(value, "os arch os_release_id version_id")
    require(type(value["os"]) is str and type(value["arch"]) is str
            and value["os"] == "linux" and value["arch"] in {"amd64", "arm64"}, "unsupported target")
    for field in ("os_release_id", "version_id"):
        text(value[field])


def candidate(value):
    shape(value, "release target artifact_sha256 executable_sha256 definition_sha256 lock_sha256 resolved_path system_rc")
    text(value["release"])
    target(value["target"])
    for field in ("artifact_sha256", "executable_sha256", "definition_sha256", "lock_sha256"):
        digest(value[field])
    absolute(value["resolved_path"])
    if value["system_rc"] != "none":
        absolute(value["system_rc"])
    return value


def identities(value):
    require(type(value) is dict and bool(value), "missing input identities")
    for path, identity in value.items():
        absolute(path)
        digest(identity)


def adapter_inputs(value):
    identities(value)
    required = {"/omegaflow-runtime/bin/awsh", "/omegaflow-runtime/etc/awsh-bashrc", "/omegaflow-runtime/etc/inputrc"}
    require(required <= set(value), "missing adapter inputs")
    for prefix in ("/omegaflow-runtime/share/terminfo/", "/omegaflow-runtime/lib/locale/"):
        require(any(path.startswith(prefix) for path in value), "missing trusted input tree")
    require(all(path.startswith("/omegaflow-runtime/") for path in value), "input outside runtime tree")


def measurements(value):
    shape(value, "startup_pty_base64 startup_export catchable_signals readline")
    raw = value["startup_pty_base64"]
    require(type(raw) is str, "invalid startup bytes")
    data = base64.b64decode(raw, validate=True)
    require(len(data) <= 4096 and base64.b64encode(data).decode() == raw, "startup byte bound/encoding")
    export = value["startup_export"]
    shape(export, "unset set")
    require(type(export["unset"]) is list, "invalid export removals")
    require(type(export["set"]) is dict, "invalid export assignments")
    for name in export["unset"] + list(export["set"]):
        require(type(name) is str and NAME.fullmatch(name), "invalid environment name")
    require(len(export["unset"]) == len(set(export["unset"])), "duplicate export removals")
    require(not set(export["unset"]) & set(export["set"]), "overlapping export transform")
    for val in export["set"].values():
        require(type(val) is str and "\0" not in val, "invalid environment value")
    signals = value["catchable_signals"]
    require(type(signals) is dict and {"INT", "CHLD", "USR1"} <= set(signals), "missing signal inventory")
    for name, number in signals.items():
        require(type(name) is str and re.fullmatch(r"(?:[A-Z][A-Z0-9]*|(?:SIG)?RT(?:MIN\+|MAX-)[0-9]+)", name), "invalid signal name")
        require(type(number) is int and 1 <= number <= 64, "invalid signal number")
    require(not {"KILL", "STOP", "SIGKILL", "SIGSTOP"} & set(signals), "uncatchable signal")
    line = value["readline"]
    shape(line, "keymap loader_hex submit_hex readiness no_redisplay utf8_cursor maximum_line_bytes")
    text(line["keymap"])
    require(line["loader_hex"] == "1801" and line["submit_hex"] == "1802", "wrong private bindings")
    for field in ("readiness", "no_redisplay", "utf8_cursor"):
        require(line[field] is True, "missing Readline proof")
    require(type(line["maximum_line_bytes"]) is int and line["maximum_line_bytes"] >= 786432, "insufficient line proof")


def case_ids():
    return sorted(read_case["id"] for read_case in
                  (json.loads(line) for line in (ROOT / "tests/fixtures/envoy-protocol-v1/cases.jsonl").read_text(encoding="utf-8").splitlines())
                  if read_case["package"] == "B2")


def probe_result(value, required):
    shape(value, "measurements cases")
    measurements(value["measurements"])
    require(type(value["cases"]) is dict and set(value["cases"]) == set(required), "incomplete or extra qualification cases")
    require(all(passed is True for passed in value["cases"].values()), "failed qualification case")


def receipt(value, build, required):
    shape(value, "version candidate_sha256 inputs assets adapter_inputs target probe repetitions result result_sha256")
    require(type(value["version"]) is int and value["version"] == 1, "unsupported receipt")
    require(value["candidate_sha256"] == sha(canonical(build)), "stale candidate evidence")
    identities(value["inputs"])
    adapter_inputs(value["adapter_inputs"])
    require(type(value["assets"]) is dict and set(value["assets"]) == set(value["adapter_inputs"]), "asset inventory mismatch")
    for name, path in value["assets"].items():
        absolute(path)
        require(value["inputs"].get(path) == value["adapter_inputs"][name], "unbound asset evidence")
    require(value["inputs"].get(build["resolved_path"]) == build["executable_sha256"], "unbound Bash evidence")
    target(value["target"])
    require(value["target"] == build["target"], "wrong measured target")
    require(type(value["probe"]) is list and bool(value["probe"]), "missing probe")
    for arg in value["probe"]:
        text(arg)
    absolute(value["probe"][0])
    require(value["probe"][0] in value["inputs"], "unbound probe executable")
    require(type(value["repetitions"]) is int and value["repetitions"] == 2, "missing repeated measurement")
    probe_result(value["result"], required)
    require(value["result_sha256"] == sha(canonical(value["result"])), "measurement digest mismatch")


def validate_table(value, required=None):
    shape(value, "version builds")
    require(type(value["version"]) is int and value["version"] == 1, "unsupported table")
    require(type(value["builds"]) is dict, "invalid builds")
    required = case_ids() if required is None else required
    for key, entry in value["builds"].items():
        digest(key)
        shape(entry, "candidate qualification")
        build = candidate(entry["candidate"])
        require(key == build["executable_sha256"], "build key mismatch")
        if entry["qualification"] is not None:
            receipt(entry["qualification"], build, required)
    return value


def qualified(value):
    validate_table(value)
    return {key: entry for key, entry in value["builds"].items() if entry["qualification"] is not None}


def file_hash(path):
    # Follow /bin/bash's placement symlink, then hash its exact regular target.
    resolved = Path(path).resolve(strict=True)
    require(stat.S_ISREG(resolved.stat().st_mode), "nonregular qualification input")
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "nonregular qualification input")
        identity = hashlib.file_digest(stream, "sha256").hexdigest()
    return str(resolved), identity


def verify_inputs(inputs):
    identities(inputs)
    for path, identity in inputs.items():
        resolved, observed = file_hash(path)
        require(resolved == path and observed == identity, "changed or unresolved qualification input")


def system_rc_absent(path):
    if path == "none":
        return True
    try:
        os.lstat(path)
    except FileNotFoundError:
        return True
    return False


def run_probe(argv, timeout):
    require(type(timeout) in {int, float} and math.isfinite(timeout) and 0 < timeout <= 300, "invalid probe timeout")
    child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector()
    buffers = [bytearray(), bytearray()]
    for index, stream in enumerate((child.stdout, child.stderr)):
        selector.register(stream, selectors.EVENT_READ, index)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            require(remaining > 0, "qualification probe timeout")
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    buffers[key.data].extend(chunk)
                    require(sum(map(len, buffers)) <= LIMIT, "qualification probe output bound")
        require(child.wait(timeout=max(0.001, deadline - time.monotonic())) == 0, "qualification probe failed")
        require(not buffers[1], "qualification probe diagnostic output")
        return decode(buffers[0])
    finally:
        # Kill probe descendants too, including writers after the parent exits.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
        selector.close()
        child.stdout.close()
        child.stderr.close()


def qualify(build, plan):
    candidate(build)
    shape(plan, "inputs assets probe timeout_seconds")
    verify_inputs(plan["inputs"])
    require(type(plan["assets"]) is dict, "invalid runtime assets")
    assets = {}
    for name, path in plan["assets"].items():
        absolute(path)
        require(path in plan["inputs"], "unbound runtime asset")
        assets[name] = plan["inputs"][path]
    adapter_inputs(assets)
    argv = plan["probe"]
    require(type(argv) is list and bool(argv), "missing probe command")
    for arg in argv:
        text(arg)
    absolute(argv[0])
    require(argv[0] in plan["inputs"], "unbound probe command")
    for arg in argv[1:]:
        if os.path.isfile(arg):
            require(str(Path(arg).resolve()) in plan["inputs"], "unbound probe file argument")
    release = platform.freedesktop_os_release()
    host = {"os": platform.system().lower(), "arch": {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine()),
            "os_release_id": release.get("ID"), "version_id": release.get("VERSION_ID")}
    require(host == build["target"], "qualification requires genuine target execution")
    resolved, observed = file_hash(BASH)
    require(resolved == build["resolved_path"] and observed == build["executable_sha256"], "resolved /bin/bash mismatch")
    require(resolved in plan["inputs"], "unbound Bash executable")
    require(system_rc_absent(build["system_rc"]), "present system rc")
    first = run_probe(argv, plan["timeout_seconds"])
    probe_result(first, case_ids())
    second = run_probe(argv, plan["timeout_seconds"])
    require(canonical(first) == canonical(second), "nonrepeatable qualification")
    verify_inputs(plan["inputs"])
    require(file_hash(BASH) == (resolved, observed), "Bash changed during qualification")
    require(system_rc_absent(build["system_rc"]), "system rc appeared during qualification")
    result = {"version": 1, "candidate_sha256": sha(canonical(build)), "inputs": plan["inputs"], "assets": plan["assets"], "adapter_inputs": assets,
              "target": host, "probe": argv, "repetitions": 2, "result": first, "result_sha256": sha(canonical(first))}
    receipt(result, build, case_ids())
    return result


def generated(value):
    data = canonical({"version": 1, "builds": qualified(value)})
    table_hash = sha(canonical(value))
    header = f"Generated by tools/bash_builds.py; source SHA-256 {table_hash}. DO NOT EDIT."
    go = '// ' + header + '\npackage bashbuild\n\nconst tableJSON = ' + json.dumps(data.decode(), ensure_ascii=False) + '\n'
    host = '# ' + header + '\n' + PYTHON_CONSUMER.replace('TABLE_LITERAL', repr(data.decode()))
    outputs = {"runtime/envoy/bashbuild/table_generated.go": go,
            "runtime/envoy/bashbuild/envoy_generated.go": '// '+header+'\npackage bashbuild\n\nfunc LookupEnvoy(path, digest string, target Target, inputs map[string]string) (Entry, error) { return lookup(path, digest, target, inputs) }\n',
            "runtime/envoy/bashbuild/awsh_generated.go": '// '+header+'\npackage bashbuild\n\nfunc LookupAwsh(path, digest string, target Target, inputs map[string]string) (Entry, error) { return lookup(path, digest, target, inputs) }\n',
            "src/omegaflow/bash_builds.py": host}
    for name in outputs:
        if name.endswith(".go"):
            outputs[name] = subprocess.run(["gofmt"], input=outputs[name], text=True, encoding="utf-8", capture_output=True, check=True).stdout
    return outputs


PYTHON_CONSUMER = '''"""Generated host-preparation consumer; only measured entries are present."""
import hashlib
import json
import os
from pathlib import Path
import stat

_TABLE = TABLE_LITERAL


def lookup(path: str, expected_digest: str, target: dict[str, str], inputs: dict[str, str]) -> dict:
    """inputs contains current manifest-verified adapter/trusted-input hashes."""
    resolved = Path(path).resolve(strict=True)
    if not stat.S_ISREG(resolved.stat().st_mode):
        raise ValueError("nonregular Bash")
    descriptor = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("nonregular Bash")
        observed = hashlib.file_digest(stream, "sha256").hexdigest()
    entry = json.loads(_TABLE)["builds"].get(observed)
    if observed != expected_digest or entry is None:
        raise ValueError("unknown or mismatched Bash build")
    build = entry["candidate"]
    if build["target"] != target:
        raise ValueError("wrong Bash target")
    if str(Path(path).resolve(strict=True)) != build["resolved_path"]:
        raise ValueError("wrong resolved Bash path")
    if build["system_rc"] != "none":
        try:
            os.lstat(build["system_rc"])
        except FileNotFoundError:
            pass
        else:
            raise ValueError("present system Bash rc")
    if entry["qualification"]["adapter_inputs"] != inputs:
        raise ValueError("adapter or trusted inputs require requalification")
    return entry
'''


def generate(value, root, check=False):
    outputs = generated(value)
    for name, content in outputs.items():
        path = root / name
        if check:
            require(path.is_file() and path.read_bytes() == content.encode(), "stale generated consumer: " + name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate")
    gen.add_argument("--table", type=Path, default=TABLE)
    gen.add_argument("--check", action="store_true")
    val = commands.add_parser("validate")
    val.add_argument("table", type=Path)
    run = commands.add_parser("qualify")
    run.add_argument("candidate", type=Path)
    run.add_argument("plan", type=Path)
    run.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "generate":
            generate(read(args.table), ROOT, args.check)
        elif args.command == "validate":
            validate_table(read(args.table))
        else:
            result = qualify(read(args.candidate), read(args.plan))
            # A runner receipt is evidence only; admission is an explicit table edit.
            with args.output.open("xb") as stream:
                stream.write(canonical(result) + b"\n")
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
