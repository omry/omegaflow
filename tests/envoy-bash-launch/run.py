#!/usr/bin/env python3
"""B2.3.1 isolated launch proof; accepts verified acquisition evidence only.

Creates test images with a test-only rcfile and observer, never a qualified
table entry or production readiness path. Preserve output under --output.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


RCFILE = '''PS1= PS2= PS0=
IFS= read -r observed < /proc/$$/stat
builtin printf 'BASHSTAT %s\\n' "$observed"
while IFS= read -r observed; do
    case "$observed" in SigBlk:*) builtin printf 'BASHMASK %s\\n' "${observed##*[[:space:]]}" ;; esac
done < /proc/$$/status
/usr/bin/python3 /omegaflow-runtime/etc/observe.py
builtin exit "$?"
'''


_IMAGE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_EXECUTABLE_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_RELEASE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")
_REQUIRED_SIGNALS = frozenset({1, 2, 3, 10, 20, 21, 22})


def _require_mapping(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _require_string(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if any(ord(char) < 0x20 or char.isspace() for char in value):
        raise ValueError(f"{name} must not contain whitespace or control characters")
    return value


def _require_digest(value, name, pattern):
    _require_string(value, name)
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{name} has an invalid digest")
    return value


def _validate_candidate(candidate, index):
    label = f"candidates[{index}]"
    candidate = _require_mapping(candidate, label)
    release = _require_string(candidate.get("release"), f"{label}.release")
    if _RELEASE_RE.fullmatch(release) is None:
        raise ValueError(
            f"{label}.release must be a Docker-tag-safe single path component"
        )

    image = _require_mapping(candidate.get("image"), f"{label}.image")
    reference = _require_string(image.get("reference"), f"{label}.image.reference")
    if reference.startswith("-"):
        raise ValueError(f"{label}.image.reference must not start with '-'")
    _require_digest(
        image.get("image_digest"), f"{label}.image.image_digest", _IMAGE_DIGEST_RE
    )

    platform = _require_mapping(image.get("platform"), f"{label}.image.platform")
    if platform.get("canonical") != "linux/amd64":
        raise ValueError(f"{label}.image.platform.canonical must be linux/amd64")
    if platform.get("os") != "linux" or platform.get("architecture") != "amd64":
        raise ValueError(f"{label}.image.platform must describe linux/amd64")
    if platform.get("variant") != "":
        raise ValueError(f"{label}.image.platform.variant must be empty for linux/amd64")

    observed = _require_mapping(candidate.get("observed"), f"{label}.observed")
    _require_digest(
        observed.get("executable_sha256"),
        f"{label}.observed.executable_sha256",
        _EXECUTABLE_DIGEST_RE,
    )
    return candidate


def _validate_acquisition(acquisition):
    acquisition = _require_mapping(acquisition, "acquisition")
    candidates = acquisition.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("acquisition.candidates must be a non-empty list")
    validated = [_validate_candidate(candidate, index) for index, candidate in enumerate(candidates)]
    releases = [candidate["release"] for candidate in validated]
    if len(set(releases)) != len(releases):
        raise ValueError("acquisition candidates must have unique release values")
    return validated


def _contained_artifact(output, relative_name):
    path = (output / relative_name).resolve()
    try:
        path.relative_to(output)
    except ValueError as error:
        raise ValueError(f"artifact escapes output directory: {relative_name!r}") from error
    return path


def _candidate_artifacts(output, release):
    return {
        "signals": _contained_artifact(output, f"{release}-signals.txt"),
        "build_log": _contained_artifact(output, f"{release}-build.log"),
        "launch_log": _contained_artifact(output, f"{release}-launch.log"),
        "iidfile": _contained_artifact(output, f"{release}-test-image-id.txt"),
        "coverage": _contained_artifact(output, f"{release}-coverage"),
    }


def _verify_image_identity(candidate):
    image = candidate["image"]
    actual = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image["reference"]],
        text=True,
    ).strip()
    expected = image["image_digest"]
    if actual != expected:
        raise ValueError(
            f"candidate {candidate['release']} image identity mismatch: "
            f"expected {expected}, got {actual}"
        )
    return actual


def _validate_signal_inventory(table, release):
    signals = sorted({int(number) for number in re.findall(r"(\d+)\)", table)} - {9, 19})
    missing = _REQUIRED_SIGNALS.difference(signals)
    if missing:
        raise ValueError(
            f"candidate {release} signal inventory is missing {sorted(missing)}"
        )
    return signals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    acquisition = json.loads(args.acquisition.read_text())
    candidates = _validate_acquisition(acquisition)
    output = args.output.resolve()

    artifacts = [
        _candidate_artifacts(output, candidate["release"]) for candidate in candidates
    ]
    results_path = _contained_artifact(output, "results.json")
    coverage_out = _contained_artifact(output, "coverage.out")
    coverage_functions = _contained_artifact(output, "coverage-functions.txt")

    # Confirm every retained image before creating the test context or deriving
    # a test image tag. This keeps identity failures ahead of all later tools and
    # output writes.
    source_images = [_verify_image_identity(candidate) for candidate in candidates]

    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[2]
    context = _contained_artifact(output, "context")
    (context / "bin").mkdir(parents=True)
    (context / "etc").mkdir()
    (context / "etc/awsh-bashrc").write_text(RCFILE)
    (context / "etc/inputrc").write_text("")
    shutil.copyfile(Path(__file__).with_name("observe.py"), context / "etc/observe.py")
    subprocess.run(
        ["go", "test", "-c", "-cover", "-covermode=atomic", "-o", str(context / "bin/launch.test"), "./awsh/launch"],
        cwd=root / "runtime/envoy",
        env={**os.environ, "CGO_ENABLED": "0", "GOOS": "linux", "GOARCH": "amd64"},
        check=True,
    )
    (context / "Dockerfile").write_text(
        "ARG CANDIDATE\nFROM ${CANDIDATE}\n"
        "COPY bin /omegaflow-runtime/bin\nCOPY etc /omegaflow-runtime/etc\n"
        # Declared system rc must be absent in the selected isolated image.
        "RUN rm /etc/bash.bashrc && chmod -R a-w /omegaflow-runtime\n"
        "WORKDIR /\n"
    )
    results = []
    try:
        for candidate, candidate_artifacts, actual in zip(
            candidates, artifacts, source_images
        ):
            release = candidate["release"]
            image = candidate["image"]
            digest = candidate["observed"]["executable_sha256"]
            table = subprocess.check_output(
                ["docker", "run", "--rm", "--network=none", "--entrypoint=/bin/bash",
                 actual, "--noprofile", "--norc", "-c", "builtin trap -l"],
                text=True,
            )
            signals = _validate_signal_inventory(table, release)
            candidate_artifacts["signals"].write_text(table)
            tag = f"omegaflow-launch-proof:{release}-{hashlib.sha256(str(output).encode()).hexdigest()[:12]}"
            with candidate_artifacts["build_log"].open("w") as log:
                subprocess.run(
                    ["docker", "build", "--build-arg", f"CANDIDATE={actual}",
                     "--iidfile", str(candidate_artifacts["iidfile"]), "-t", tag,
                     str(context)],
                    env={**os.environ, "DOCKER_BUILDKIT": "0"},
                    stdout=log, stderr=subprocess.STDOUT, check=True,
                )
            test_image = candidate_artifacts["iidfile"].read_text().strip()
            if _IMAGE_DIGEST_RE.fullmatch(test_image) is None:
                raise ValueError(
                    f"candidate {release} build returned an invalid image ID: {test_image!r}"
                )
            with candidate_artifacts["launch_log"].open("w") as log:
                coverage = candidate_artifacts["coverage"]
                coverage.mkdir()
                run = subprocess.run(
                    ["docker", "run", "--rm", "--network=none",
                     "-v", f"{coverage}:/proof-coverage",
                     "-e", "GOCOVERDIR=/proof-coverage",
                     "-e", f"OMEGAFLOW_CANDIDATE_DIGEST={digest}",
                     "-e", f"OMEGAFLOW_CANDIDATE_SIGNALS={json.dumps(signals)}",
                     "--entrypoint=/omegaflow-runtime/bin/launch.test", test_image,
                     "-test.v", "-test.timeout=120s", "-test.gocoverdir=/proof-coverage",
                     "-test.run=^Test(HandoffGrammar|RealSelectedLaunch)$"],
                    stdout=log, stderr=subprocess.STDOUT,
                )
            results.append({"release": release, "source_image": actual,
                            "test_image": test_image, "test_image_tag": tag,
                            "executable_sha256": digest, "signals": signals,
                            "returncode": run.returncode, "qualified": False})
            if run.returncode:
                raise RuntimeError(f"candidate {release} failed; see retained launch log")
    finally:
        results_path.write_text(json.dumps(results, indent=2) + "\n")
    directories = ",".join(str(artifacts[index]["coverage"]) for index, _ in enumerate(results))
    subprocess.run(["go", "tool", "covdata", "textfmt", f"-i={directories}",
                    f"-o={coverage_out}"], check=True)
    with coverage_functions.open("w") as report:
        subprocess.run(["go", "tool", "cover", f"-func={coverage_out}"],
                       cwd=root / "runtime/envoy", stdout=report, check=True)
    print(f"All {len(results)} pinned candidates passed isolated launch proof: {output}")


if __name__ == "__main__":
    main()
