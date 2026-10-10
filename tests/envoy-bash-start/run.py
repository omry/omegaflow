#!/usr/bin/env python3
"""B2.6 real candidate start transaction proof; qualification remains B2.8."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
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
    shutil.copyfile(root / "tests/envoy-bash-submission/run.py", context / "etc/prepared-worker.py")
    shutil.copyfile(Path(__file__).with_name("cases.json"), context / "etc/cases.json")
    env = {**os.environ, "CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": "amd64"}
    subprocess.run(["go", "build", "-buildvcs=false", "-o", str(context / "bin/awsh"),
                    "./cmd/awsh"], cwd=root / "runtime/envoy", env=env, check=True)
    shutil.copyfile(context / "bin/awsh", context / "bin/awsh-real")
    (context / "bin/awsh-real").chmod(0o555)
    subprocess.run(["go", "test", "-c", "-cover", "-covermode=atomic", "-o",
                    str(context / "bin/start.test"), "./awsh/launch"],
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
    mutants = []
    if args.mutations:
        variants = [
            ("nonroot-predicate", "awsh/launch/operation_linux.go",
             'if !ok || info.Mode()&os.ModeSymlink != 0 || (path != "/run" && stat.Uid != uint32(os.Geteuid())) {',
             'if !ok || stat.Uid != uint32(os.Geteuid()) || info.Mode()&os.ModeSymlink != 0 {',
             "split-status-one", "split path identity mismatch", False),
            ("restoration", "awsh/launch/operation_linux.go", 's.RestoreWorkloadTermios(r.ctx)',
             'error(nil)', "normal", "workload state not restored", False),
            ("release-id", "awsh/launch/operation_linux.go",
             'r.phase != startPrepared || v.OperationID != r.request.OperationID',
             'r.phase != startPrepared', "wrong-release", "private result after fatal transition", False),
            ("release-arm", "awsh/launch/operation_linux.go", 'r.phase = startSignal',
             'r.phase = startAcknowledgement', "normal", "unexpected release signal", False),
            ("entry-sentinel", "awsh/submission/frame.go", '__OMEGAFLOW_AWSH_INNER_ENTERED != 1',
             '__OMEGAFLOW_AWSH_INNER_ENTERED != 0', "split-stdout-failure", "redirection failure did not fail-stop", True),
        ]
        for name, path, before, after, case, diagnostic, helper_mutant in variants:
            original = root / "runtime/envoy" / path
            text = original.read_text()
            if text.count(before) != 1:
                raise ValueError(f"nonunique mutation {name}")
            fixture = output / f"mutation-{name}.go"
            fixture.write_text(text.replace(before, after))
            overlay = output / f"mutation-{name}.json"
            overlay.write_text(json.dumps({"Replace": {str(original): str(fixture)}}))
            binary = output / f"mutation-{name}.test"
            subprocess.run(["go", "test", "-c", f"-overlay={overlay}", "-o", str(binary),
                            "./awsh/launch"], cwd=root / "runtime/envoy", env=env, check=True)
            awsh = None
            if helper_mutant:
                awsh = output / f"mutation-{name}-awsh"
                subprocess.run(["go", "build", "-buildvcs=false", f"-overlay={overlay}",
                                "-o", str(awsh), "./cmd/awsh"],
                               cwd=root / "runtime/envoy", env=env, check=True)
            mutants.append((name, binary, awsh, case, diagnostic, fixture))
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
            tag = f"omegaflow-start-proof:{release}-{hashlib.sha256(str(output).encode()).hexdigest()[:12]}"
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
                     "-e", f"OMEGAFLOW_START_ONLY={args.case or ''}",
                     "--entrypoint=/omegaflow-runtime/bin/start.test", iid,
                     "-test.v", "-test.timeout=120s", "-test.gocoverdir=/proof-coverage",
                     "-test.run=^Test(StartRejectsEventsOutsideOperation|RealCandidateStart)$"],
                    stdout=log, stderr=subprocess.STDOUT, timeout=135)
            results.append({"release": release, "source_image": image, "test_image": iid,
                            "executable_sha256": candidate["observed"]["executable_sha256"],
                            "signals": signals, "returncode": run.returncode,
                            "qualified": False})
            if run.returncode:
                raise RuntimeError(f"candidate {release} failed; see {artifacts['launch_log']}")
            if not args.case:
                for name, replacement in {"missing-signal": "while ((1)); do ((1)); done",
                    "release-queue-failure": "builtin kill -s USR1 -- 2147483647 || __OMEGAFLOW_AWSH_FAIL_STOP"}.items():
                    fixture = output / f"shell-{name}.bash"
                    original = (context / "etc/awsh-bashrc").read_text()
                    before = 'builtin kill -s USR1 -- "$PPID" || __OMEGAFLOW_AWSH_FAIL_STOP'
                    if original.count(before) != 1:
                        raise ValueError("nonunique release fixture")
                    fixture.write_text(original.replace(before, replacement))
                    log_path = output / f"{release}-{name}.log"
                    with log_path.open("w") as log:
                        run = subprocess.run(["docker", "run", "--rm", "--network=none",
                            "-v", f"{fixture}:/omegaflow-runtime/etc/awsh-bashrc:ro",
                            "-e", f"OMEGAFLOW_CANDIDATE_DIGEST={candidate['observed']['executable_sha256']}",
                            "-e", f"OMEGAFLOW_CANDIDATE_SIGNALS={json.dumps(signals)}",
                            "-e", "OMEGAFLOW_NONROOT_SUPERVISOR=1", "-e", "OMEGAFLOW_START_UID=1000",
                            "-e", f"OMEGAFLOW_START_ONLY={name}",
                            "--entrypoint=/omegaflow-runtime/bin/start.test", iid,
                            "-test.v", "-test.timeout=15s", "-test.run=^TestRealCandidateStart$"],
                            stdout=log, stderr=subprocess.STDOUT, timeout=25)
                    results[-1].setdefault("release_faults", {})[name] = {
                        "returncode": run.returncode, "log": str(log_path),
                        "rcfile_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest()}
                    if run.returncode:
                        raise RuntimeError(f"release fault {name} failed: {log_path}")
                for case in json.loads(Path(__file__).with_name("cases.json").read_text()):
                    directory = output / f"{release}-{case['id']}"
                    directory.mkdir()
                    command = ["docker", "run", "--rm", "--network=none", "-e",
                               f"OMEGAFLOW_CANDIDATE_DIGEST={candidate['observed']['executable_sha256']}",
                               "-e", "INPUTRC=/omegaflow-runtime/etc/inputrc", "-e", "TERM=xterm-256color",
                               "-e", "TERMINFO=/omegaflow-runtime/share/terminfo", "-e", "LC_ALL=C.UTF-8",
                               "-e", "LANG=C.UTF-8", "-e", "LOCPATH=/omegaflow-runtime/lib/locale",
                               "-v", f"{directory}:/proof"]
                    if not case.get("prepared_wire_fault"):
                        fixture = output / f"{release}-{case['id']}.helper"
                        body = 'builtin kill -s TERM -- "$$"\n' if case.get("signal") else (
                            f"builtin printf '%s' {shlex.quote(case['marker'])}\n"
                            f"builtin exit {case['status']}\n")
                        fixture.write_text('#!/bin/bash\nif [[ $3 == start-prepared ]]; then\n' + body +
                                           'fi\nexec /omegaflow-runtime/bin/awsh-real "$@"\n')
                        fixture.chmod(0o555)
                        command += ["-v", f"{fixture}:/omegaflow-runtime/bin/awsh:ro"]
                    log_path = output / f"{release}-{case['id']}.log"
                    with log_path.open("w") as log:
                        run = subprocess.run(command + ["--entrypoint=/usr/bin/python3", iid,
                            "/omegaflow-runtime/etc/prepared-worker.py", "--worker", "--output=/proof",
                            f"--case={case['id']}"], stdout=log, stderr=subprocess.STDOUT, timeout=25)
                    results[-1].setdefault("ps0_faults", {})[case["id"]] = {
                        "returncode": run.returncode, "log": str(log_path),
                        "log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest()}
                    if run.returncode:
                        raise RuntimeError(f"PS0 fault {case['id']} failed: {log_path}")
            for name, binary, awsh, case, diagnostic, fixture in mutants:
                log_path = output / f"{release}-mutation-{name}.log"
                command = ["docker", "run", "--rm", "--network=none",
                           "-v", f"{binary}:/omegaflow-runtime/bin/start.test:ro",
                           "-e", f"OMEGAFLOW_CANDIDATE_DIGEST={candidate['observed']['executable_sha256']}",
                           "-e", f"OMEGAFLOW_CANDIDATE_SIGNALS={json.dumps(signals)}",
                           "-e", "OMEGAFLOW_NONROOT_SUPERVISOR=1", "-e", "OMEGAFLOW_START_UID=1000",
                           "-e", f"OMEGAFLOW_START_ONLY={case}"]
                if awsh:
                    command += ["-v", f"{awsh}:/omegaflow-runtime/bin/awsh:ro"]
                with log_path.open("w") as log:
                    run = subprocess.run(command + ["--entrypoint=/omegaflow-runtime/bin/start.test", iid,
                        "-test.v", "-test.timeout=15s", "-test.run=^TestRealCandidateStart$"],
                        stdout=log, stderr=subprocess.STDOUT, timeout=25)
                results[-1].setdefault("mutations", {})[name] = {
                    "returncode": run.returncode, "log": str(log_path),
                    "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest()}
                if run.returncode == 0 or diagnostic not in log_path.read_text():
                    raise RuntimeError(f"mutation {name} survived or failed for another cause: {log_path}")
    finally:
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"All {len(results)} pinned candidates passed isolated start: {output}")


if __name__ == "__main__":
    main()
