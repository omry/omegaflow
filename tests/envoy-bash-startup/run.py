#!/usr/bin/env python3
"""Isolated B2.3.2 startup proof on verified Reploy candidates, not admission."""

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


# Faults replace only the rcfile in isolated runs with a read-only bind mount.
# Production never selects scenarios or accepts a test peer through configuration.
_HELPER = "/omegaflow-runtime/bin/awsh bash-helper --socket=/run/omegaflow/session/bash/helper.sock "
_STATE = _HELPER + "prompt-state 0 off emacs\n"
_WAIT = "while :; do builtin read -r -t 1; done\n"
FAULTS = {
    "shell-exit": "builtin exit 42\n",
    "ready-first": _HELPER + "prompt-ready\n",
    "duplicate-state": _STATE + _STATE,
    "missing-state": _WAIT,
    "missing-ready": _STATE + _WAIT,
    "cancel-startup": _WAIT,
    "wrong-peer": _WAIT,
    "unexpected-signal": _WAIT,
    "existing-directory": _WAIT,
    "fail-stop": _HELPER + "prompt-state +1 off emacs || /omegaflow-runtime/bin/awsh bash-fail-stop\n",
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
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
                    str(context / "bin/startup.test"), "./awsh/launch"],
                   cwd=root / "runtime/envoy", env=env, check=True)
    (context / "Dockerfile").write_text(
        "ARG CANDIDATE\nFROM ${CANDIDATE}\n"
        "COPY bin /omegaflow-runtime/bin\nCOPY etc /omegaflow-runtime/etc\n"
        "RUN rm /etc/bash.bashrc && mkdir -p /run/omegaflow/session "
        "/omegaflow-runtime/share/terminfo /omegaflow-runtime/lib/locale "
        "&& cp -a /usr/share/terminfo/. /omegaflow-runtime/share/terminfo/ "
        "&& if test -d /lib/terminfo; then cp -a /lib/terminfo/. /omegaflow-runtime/share/terminfo/; fi "
        "&& cp -a /usr/lib/locale/. /omegaflow-runtime/lib/locale/ "
        "&& chmod -R a-w /omegaflow-runtime\nWORKDIR /\n"
    )
    (output / "inputs.json").write_text(json.dumps({
        "acquisition_sha256": hashlib.sha256(args.acquisition.read_bytes()).hexdigest(),
        "assets": {str(p.relative_to(context)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(context.rglob("*")) if p.is_file()},
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "launch_input_rules_sha256": hashlib.sha256((root / "tests/envoy-bash-launch/run.py").read_bytes()).hexdigest(),
    }, indent=2) + "\n")
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
            tag = f"omegaflow-startup-proof:{release}-{hashlib.sha256(str(output).encode()).hexdigest()[:12]}"
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
                     "--entrypoint=/omegaflow-runtime/bin/startup.test", iid,
                     "-test.v", "-test.timeout=120s", "-test.gocoverdir=/proof-coverage",
                     "-test.run=^Test(QualifiedLaunchFailsClosed|RealCandidateStartup)$"],
                    stdout=log, stderr=subprocess.STDOUT, timeout=135)
            results.append({"release": release, "source_image": image, "test_image": iid,
                            "executable_sha256": candidate["observed"]["executable_sha256"],
                            "signals": signals, "returncode": run.returncode,
                            "qualified": False})
            if run.returncode:
                raise RuntimeError(f"candidate {release} failed; see {artifacts['launch_log']}")
            faults = {}
            for name, script in {**FAULTS, **{name: (context / "etc/awsh-bashrc").read_text()
                                           for name in ("blocked-result", "release-during-ready", "exit-during-ready")}}.items():
                fixture = output / f"fault-{name}.bash"
                fixture.write_text("PS1= PS2= PS0=\n" + script)
                fault_log = output / f"{release}-{name}.log"
                with fault_log.open("w") as log:
                    fault = subprocess.run(
                        ["docker", "run", "--rm", "--network=none", "-v",
                         f"{fixture}:/omegaflow-runtime/etc/awsh-bashrc:ro",
                         "-e", f"OMEGAFLOW_CANDIDATE_DIGEST={candidate['observed']['executable_sha256']}",
                         "-e", f"OMEGAFLOW_CANDIDATE_SIGNALS={json.dumps(signals)}",
                         "-e", f"OMEGAFLOW_STARTUP_SCENARIO={name}",
                         "--entrypoint=/omegaflow-runtime/bin/startup.test", iid,
                         "-test.v", "-test.timeout=20s", "-test.run=^TestRealCandidateStartup$"],
                        stdout=log, stderr=subprocess.STDOUT, timeout=30)
                faults[name] = {"returncode": fault.returncode,
                                "rcfile_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                                "log": str(fault_log)}
                results[-1]["faults"] = faults
                if fault.returncode:
                    raise RuntimeError(f"candidate {release} fault {name} failed; see {fault_log}")
    finally:
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"All {len(results)} pinned candidates passed isolated startup: {output}")


if __name__ == "__main__":
    main()
