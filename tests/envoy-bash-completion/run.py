#!/usr/bin/env python3
"""B2.7 real candidate completion transaction proof; qualification remains B2.8."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess


def launch_tools(root):
    source = root / "tests/envoy-bash-launch/run.py"
    spec = importlib.util.spec_from_file_location("launch_proof", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case")
    parser.add_argument("--mutations", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    tools = launch_tools(root)
    candidates = tools._validate_acquisition(json.loads(args.acquisition.read_text()))
    output = args.output.resolve()
    # Resolve every image before creating artifacts, reusing PR48's checked
    # input rules and exact image checks rather than trusting mutable tags.
    images = [tools._verify_image_identity(c) for c in candidates]
    output.mkdir(parents=True, exist_ok=False)
    context = output / "context"
    (context / "bin").mkdir(parents=True)
    (context / "etc").mkdir()
    for name in ("awsh-bashrc", "inputrc"):
        shutil.copyfile(root / "runtime/envoy/etc" / name, context / "etc" / name)
    env = {**os.environ, "CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": "amd64"}
    subprocess.run(["go", "build", "-buildvcs=false", "-o", str(context / "bin/awsh"),
                    "./cmd/awsh"], cwd=root / "runtime/envoy", env=env, check=True)
    subprocess.run(["go", "test", "-c", "-cover", "-covermode=atomic", "-o",
                    str(context / "bin/completion.test"), "./awsh/launch"],
                   cwd=root / "runtime/envoy", env=env, check=True)
    (context / "Dockerfile").write_text(
        "ARG CANDIDATE\nFROM ${CANDIDATE}\n"
        "COPY bin /omegaflow-runtime/bin\nCOPY etc /omegaflow-runtime/etc\n"
        "RUN rm /etc/bash.bashrc && mkdir -p /run/omegaflow/session "
        "/omegaflow-runtime/share/terminfo /omegaflow-runtime/lib/locale "
        "&& cp -a /usr/share/terminfo/. /omegaflow-runtime/share/terminfo/ "
        "&& if test -d /lib/terminfo; then cp -a /lib/terminfo/. /omegaflow-runtime/share/terminfo/; fi "
        "&& cp -a /usr/lib/locale/. /omegaflow-runtime/lib/locale/ "
        "&& chmod -R a-w /omegaflow-runtime "
        "&& chown 0:0 /run && chmod 0755 /run "
        "&& chown -R 1000:1000 /run/omegaflow "
        "&& chmod 0700 /run/omegaflow /run/omegaflow/session\nWORKDIR /\n"
    )
    (output / "inputs.json").write_text(json.dumps({
        "acquisition_sha256": hashlib.sha256(args.acquisition.read_bytes()).hexdigest(),
        "assets": {str(p.relative_to(context)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(context.rglob("*")) if p.is_file()},
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "launch_input_rules_sha256": hashlib.sha256((root / "tests/envoy-bash-launch/run.py").read_bytes()).hexdigest(),
    }, indent=2) + "\n")
    helper_original = root / "runtime/envoy/awsh/helper/startup_linux.go"
    helper_fixture = output / "helper-child.go"
    helper_text = helper_original.read_text().replace('"os"', '"os"\n "os/exec"')
    helper_text = helper_text.replace('// The actor supervises', 'if os.Getenv("OMEGAFLOW_PROOF_CHILD") == "1" { child := exec.Command("/bin/sleep","100"); if err := child.Start(); err != nil { return err }; defer func() { child.Process.Kill(); child.Wait() }() }\n // The actor supervises')
    helper_fixture.write_text(helper_text)
    helper_overlay = output / "helper-child-overlay.json"
    helper_overlay.write_text(json.dumps({"Replace": {str(helper_original): str(helper_fixture)}}))
    helper_fault_binary = output / "helper-child-awsh"
    subprocess.run(["go", "build", "-buildvcs=false", f"-overlay={helper_overlay}", "-o", str(helper_fault_binary), "./cmd/awsh"], cwd=root / "runtime/envoy", env=env, check=True)
    mutants = []
    if args.mutations:
        variants = [
            ("direct-exec", "etc/awsh-bashrc", 'builtin exec /omegaflow-runtime/bin/awsh bash-helper --socket=/run/omegaflow/session/bash/helper.sock prompt-state "$@"', '/omegaflow-runtime/bin/awsh bash-helper --socket=/run/omegaflow/session/bash/helper.sock prompt-state "$@"\n    builtin return "$?"', "persistent", "helper identity mismatch"),
            ("fresh-termios", "awsh/launch/completion_linux.go", 'workload, err = termios(terminalFD)', 'workload, err = s.workloadTermios, nil', "persistent", "fresh workload state lost"),
            ("control-id", "awsh/launch/completion_linux.go", 'closed.OperationID != s.active.request.OperationID || ', 'closed.OperationID == "" || ', "wrong-id", "private result after fatal transition"),
            ("source-status", "awsh/launch/completion_linux.go", 'Status: state.Status, PhysicalCWD: state.PhysicalCWD, Inspections: plans', 'Status: 0, PhysicalCWD: state.PhysicalCWD, Inspections: plans', "status-one", "source status 0 want 1"),
            ("pre-cleanup-state", "awsh/launch/completion_linux.go", 'plans, err := resolveInspections(state.PromptState, s.active.request.Inspections)', 'plans, err := resolveInspections(r.state, s.active.request.Inspections)', "allowed-trap", "final cleanup state"),
        ]
        for name, path, before, after, case, diagnostic in variants:
            original = root / "runtime/envoy" / path
            text = original.read_text()
            if text.count(before) != 1:
                raise ValueError(f"nonunique mutation {name}")
            fixture = output / f"mutation-{name}{original.suffix}"
            fixture.write_text(text.replace(before, after))
            binary = None
            if original.suffix == ".go":
                overlay = output / f"mutation-{name}.json"
                overlay.write_text(json.dumps({"Replace": {str(original): str(fixture)}}))
                binary = output / f"mutation-{name}.test"
                subprocess.run(["go", "test", "-c", f"-overlay={overlay}", "-o", str(binary), "./awsh/launch"], cwd=root / "runtime/envoy", env=env, check=True)
            mutants.append((name, binary, fixture, case, diagnostic))
    results = []
    try:
        for candidate, image in zip(candidates, images):
            release = candidate["release"]
            artifacts = tools._candidate_artifacts(output, release)
            table = subprocess.check_output(
                ["docker", "run", "--rm", "--network=none", "--entrypoint=/bin/bash",
                 image, "--noprofile", "--norc", "-c", "builtin trap -l"], text=True)
            signals = tools._validate_signal_inventory(table, release)
            artifacts["signals"].write_text(table)
            tag = f"omegaflow-completion-proof:{release}-{hashlib.sha256(str(output).encode()).hexdigest()[:12]}"
            with artifacts["build_log"].open("w") as log:
                subprocess.run(["docker", "build", "--build-arg", f"CANDIDATE={image}",
                                "--iidfile", str(artifacts["iidfile"]), "-t", tag,
                                str(context)], env={**os.environ, "DOCKER_BUILDKIT": "0"},
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            iid = artifacts["iidfile"].read_text().strip()
            if tools._IMAGE_DIGEST_RE.fullmatch(iid) is None:
                raise ValueError("invalid derived image identity")
            artifacts["coverage"].mkdir()
            with artifacts["launch_log"].open("w") as log:
                run = subprocess.run(
                    ["docker", "run", "--rm", "--network=none", "-v",
                     f"{artifacts['coverage']}:/proof-coverage", "-e", "GOCOVERDIR=/proof-coverage",
                     "-e", f"OMEGAFLOW_CANDIDATE_DIGEST={candidate['observed']['executable_sha256']}",
                     "-e", f"OMEGAFLOW_CANDIDATE_SIGNALS={json.dumps(signals)}",
                     "-e", "OMEGAFLOW_NONROOT_SUPERVISOR=1", "-e", "OMEGAFLOW_START_UID=1000",
                     "-e", f"OMEGAFLOW_COMPLETION_ONLY={args.case or ''}",
                     "--entrypoint=/omegaflow-runtime/bin/completion.test", iid,
                     "-test.v", "-test.timeout=120s", "-test.gocoverdir=/proof-coverage",
                     "-test.run=^Test(CompletionEventsOutsideOperation|RealCandidateCompletion)$"],
                    stdout=log, stderr=subprocess.STDOUT, timeout=135)
            results.append({"release": release, "source_image": image, "test_image": iid,
                            "executable_sha256": candidate["observed"]["executable_sha256"],
                            "signals": signals, "returncode": run.returncode,
                            "qualified": False})
            if run.returncode:
                raise RuntimeError(f"candidate {release} failed; see {artifacts['launch_log']}")
            if not args.case:
                for name, before, after in [
                    ("helper-int-damage", "__OMEGAFLOW_AWSH_COMPLETION_STATE() {\n    builtin trap - EXIT", "__OMEGAFLOW_AWSH_COMPLETION_STATE() {\n    builtin trap - EXIT\n    builtin trap - INT"),
                    ("wrong-first-phase", 'prompt-state "$@"', 'prompt-ready "$@"'),
                    ("duplicate-final-state", 'prompt-ready "$__OMEGAFLOW_AWSH_STATUS"', 'prompt-state "$__OMEGAFLOW_AWSH_STATUS"'),
                    ("final-status-damage", 'prompt-ready "$__OMEGAFLOW_AWSH_STATUS"', 'prompt-ready 17'),
                ]:
                    text = (context / "etc/awsh-bashrc").read_text()
                    if text.count(before) != 1:
                        raise ValueError(f"nonunique helper fault {name}")
                    fixture = output / f"fault-{name}.bash"
                    fixture.write_text(text.replace(before, after))
                    log_path = output / f"{release}-fault-{name}.log"
                    with log_path.open("w") as log:
                        run = subprocess.run(["docker", "run", "--rm", "--network=none", "-v", f"{fixture}:/omegaflow-runtime/etc/awsh-bashrc:ro",
                            "-e", f"OMEGAFLOW_CANDIDATE_DIGEST={candidate['observed']['executable_sha256']}", "-e", f"OMEGAFLOW_CANDIDATE_SIGNALS={json.dumps(signals)}",
                            "-e", f"OMEGAFLOW_COMPLETION_ONLY={name}", "--entrypoint=/omegaflow-runtime/bin/completion.test", iid,
                            "-test.v", "-test.timeout=15s", "-test.run=^TestRealCandidateCompletion$"], stdout=log, stderr=subprocess.STDOUT, timeout=25)
                    results[-1].setdefault("helper_faults", {})[name] = {"returncode": run.returncode, "log": str(log_path), "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest()}
                    if run.returncode:
                        raise RuntimeError(f"helper fault {name} did not fail closed: {log_path}")
            if not args.case:
                log_path = output / f"{release}-fault-helper-child.log"
                with log_path.open("w") as log:
                    run = subprocess.run(["docker", "run", "--rm", "--network=none", "-v", f"{helper_fault_binary}:/omegaflow-runtime/bin/awsh:ro",
                        "-e", f"OMEGAFLOW_CANDIDATE_DIGEST={candidate['observed']['executable_sha256']}", "-e", f"OMEGAFLOW_CANDIDATE_SIGNALS={json.dumps(signals)}",
                        "-e", "OMEGAFLOW_COMPLETION_ONLY=helper-child", "--entrypoint=/omegaflow-runtime/bin/completion.test", iid,
                        "-test.v", "-test.timeout=15s", "-test.run=^TestRealCandidateCompletion$"], stdout=log, stderr=subprocess.STDOUT, timeout=25)
                results[-1].setdefault("helper_faults", {})["helper-child"] = {"returncode": run.returncode, "log": str(log_path), "fixture_sha256": hashlib.sha256(helper_fixture.read_bytes()).hexdigest(), "binary_sha256": hashlib.sha256(helper_fault_binary.read_bytes()).hexdigest()}
                if run.returncode:
                    raise RuntimeError(f"helper child not rejected: {log_path}")
            for name, binary, fixture, case, diagnostic in mutants:
                log_path = output / f"{release}-mutation-{name}.log"
                destination = "/omegaflow-runtime/bin/completion.test" if binary else "/omegaflow-runtime/etc/awsh-bashrc"
                command = ["docker", "run", "--rm", "--network=none", "-v", f"{binary or fixture}:{destination}:ro",
                           "-e", f"OMEGAFLOW_CANDIDATE_DIGEST={candidate['observed']['executable_sha256']}",
                           "-e", f"OMEGAFLOW_CANDIDATE_SIGNALS={json.dumps(signals)}", "-e", f"OMEGAFLOW_COMPLETION_ONLY={case}",
                           "--entrypoint=/omegaflow-runtime/bin/completion.test", iid, "-test.v", "-test.timeout=15s", "-test.run=^TestRealCandidateCompletion$"]
                with log_path.open("w") as log:
                    run = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=25)
                results[-1].setdefault("mutations", {})[name] = {"returncode": run.returncode, "log": str(log_path), "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest()}
                if run.returncode == 0 or diagnostic not in log_path.read_text():
                    raise RuntimeError(f"mutation {name} survived or failed for another cause: {log_path}")
    finally:
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"All {len(results)} pinned candidates passed isolated completion: {output}")


if __name__ == "__main__":
    main()
