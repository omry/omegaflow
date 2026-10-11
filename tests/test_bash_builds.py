"""Synthetic admission/transport tests; no selected Bash is executed here."""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bash_build_tool", ROOT / "tools/bash_builds.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
SYNTHETIC = json.loads((ROOT / "tests/fixtures/bash-builds/synthetic.json").read_text())
ASSETS = ["/omegaflow-runtime/bin/awsh", "/omegaflow-runtime/etc/awsh-bashrc",
          "/omegaflow-runtime/etc/inputrc", "/omegaflow-runtime/share/terminfo/x/xterm-256color",
          "/omegaflow-runtime/lib/locale/C.UTF-8/LC_CTYPE"]


def receipt(build):
    result = {"measurements": copy.deepcopy(SYNTHETIC["measurements"]),
              "cases": dict.fromkeys(tool.case_ids(), True)}
    return {"version": 1, "candidate_sha256": tool.sha(tool.canonical(build)),
            "inputs": {"/synthetic/probe": "a" * 64, build["resolved_path"]: build["executable_sha256"], **dict.fromkeys(ASSETS, "e" * 64)},
            "assets": {path: path for path in ASSETS},
            "adapter_inputs": dict.fromkeys(ASSETS, "e" * 64), "target": build["target"],
            "probe": ["/synthetic/probe"], "repetitions": 2,
            "result": result, "result_sha256": tool.sha(tool.canonical(result))}


def table(qualified=True):
    build = copy.deepcopy(SYNTHETIC["candidate"])
    build["release"] = "synthetic-🙂"
    return {"version": 1, "builds": {build["executable_sha256"]:
            {"candidate": build, "qualification": receipt(build) if qualified else None}}}


def set_path(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


@pytest.mark.parametrize("path,value", [
    (["version"], True), (["version"], 2), (["builds"], []),
    (["builds", "b" * 64, "candidate", "release"], ""),
    (["builds", "b" * 64, "candidate", "target", "arch"], "x86_64"),
    (["builds", "b" * 64, "candidate", "target", "arch"], []),
    (["builds", "b" * 64, "candidate", "target", "os"], "darwin"),
    (["builds", "b" * 64, "candidate", "target", "os_release_id"], ""),
    (["builds", "b" * 64, "candidate", "target", "version_id"], 12),
    (["builds", "b" * 64, "candidate", "artifact_sha256"], "A" * 64),
    (["builds", "b" * 64, "candidate", "definition_sha256"], "unknown"),
    (["builds", "b" * 64, "candidate", "lock_sha256"], ""),
    (["builds", "b" * 64, "candidate", "executable_sha256"], "a" * 64),
    (["builds", "b" * 64, "candidate", "resolved_path"], "/a/../bash"),
    (["builds", "b" * 64, "candidate", "system_rc"], "relative"),
    (["builds", "b" * 64, "qualification", "version"], True),
    (["builds", "b" * 64, "qualification", "candidate_sha256"], "f" * 64),
    (["builds", "b" * 64, "qualification", "inputs"], {}),
    (["builds", "b" * 64, "qualification", "adapter_inputs"], {}),
    (["builds", "b" * 64, "qualification", "assets"], {}),
    (["builds", "b" * 64, "qualification", "target", "arch"], "arm64"),
    (["builds", "b" * 64, "qualification", "target", "version_id"], "2"),
    (["builds", "b" * 64, "qualification", "probe"], []),
    (["builds", "b" * 64, "qualification", "probe"], ["/unbound"]),
    (["builds", "b" * 64, "qualification", "repetitions"], True),
    (["builds", "b" * 64, "qualification", "repetitions"], 1),
    (["builds", "b" * 64, "qualification", "result_sha256"], "f" * 64),
])
def test_reject_table(path, value):
    invalid = table()
    set_path(invalid, path, value)
    with pytest.raises(ValueError):
        tool.validate_table(invalid)


@pytest.mark.parametrize("path,value", [
    (["startup_pty_base64"], "%%%"),
    (["startup_pty_base64"], base64.b64encode(b"x" * 4097).decode()),
    (["startup_export", "unset"], ["A", "A"]),
    (["startup_export", "unset"], [{}]),
    (["startup_export", "set"], {"1BAD": "value"}),
    (["startup_export", "set"], {"A": "\0"}),
    (["catchable_signals"], {"INT": 2}),
    (["catchable_signals", "INT"], True),
    (["catchable_signals", "KILL"], 9),
    (["catchable_signals", "SIGSTOP"], 19),
    (["catchable_signals", "INT+1"], 3),
    (["readline", "readiness"], False),
    (["readline", "no_redisplay"], 1),
    (["readline", "utf8_cursor"], False),
    (["readline", "loader_hex"], "1802"),
    (["readline", "submit_hex"], "1801"),
    (["readline", "maximum_line_bytes"], 786431),
])
def test_reject_measurements(path, value):
    invalid = copy.deepcopy(SYNTHETIC["measurements"])
    set_path(invalid, path, value)
    with pytest.raises(ValueError):
        tool.measurements(invalid)


def test_complete_case_evidence_and_schema():
    value = table()
    proof = value["builds"]["b" * 64]["qualification"]
    for change in ("missing", "extra", "false", "unknown"):
        bad = copy.deepcopy(value)
        result = bad["builds"]["b" * 64]["qualification"]["result"]
        if change == "missing":
            result["cases"].pop(tool.case_ids()[0])
        elif change == "extra":
            result["cases"]["invented"] = True
        elif change == "false":
            result["cases"][tool.case_ids()[0]] = False
        else:
            result["measurements"]["invented"] = True
        with pytest.raises(ValueError):
            tool.validate_table(bad)
    tool.validate_table(value)
    assert len(proof["result"]["cases"]) == 258
    for startup in (b"", b"x" * 4096):
        measured = copy.deepcopy(SYNTHETIC["measurements"])
        measured["startup_pty_base64"] = base64.b64encode(startup).decode()
        measured["catchable_signals"].update({"RTMIN+1": 35, "RTMAX-1": 63})
        tool.measurements(measured)


@pytest.mark.parametrize("data", [b'{"version":1,"version":1,"builds":{}}', b'{"x":NaN}',
                                   b'{}{}', b'\xff', b'x' * (tool.LIMIT + 1)])
def test_strict_json(data):
    with pytest.raises(ValueError):
        tool.decode(data)


def test_candidate_is_not_support_and_generation_is_checked(tmp_path):
    pytest.importorskip("omegaflow.bash_builds")
    assert tool.qualified(table(False)) == {}
    if not shutil.which("gofmt"):
        pytest.skip("Go formatting tool unavailable")
    tool.generate(table(False), tmp_path)
    tool.generate(table(False), tmp_path, check=True)
    host = tmp_path / "src/omegaflow/bash_builds.py"
    assert '"builds":{}' in host.read_text()
    host.write_text(host.read_text() + "# drift\n")
    before = host.read_bytes()
    with pytest.raises(ValueError, match="stale"):
        tool.generate(table(False), tmp_path, check=True)
    assert host.read_bytes() == before
    tool.generate(table(False), tmp_path)
    for filename in tool.generated(table(False)):
        (tmp_path / filename).unlink()
        with pytest.raises(ValueError, match="stale"):
            tool.generate(table(False), tmp_path, check=True)
        tool.generate(table(False), tmp_path)
    if tool.TABLE.exists():
        tool.generate(tool.read(tool.TABLE), ROOT, check=True)
        tool.validate_table(tool.read(tool.TABLE))


def test_generated_consumers_select_exact_build_and_inputs(tmp_path):
    if not shutil.which("go"):
        pytest.skip("Go toolchain unavailable")
    binary = tmp_path / "bash"
    binary.write_bytes(b"synthetic executable bytes, never executed")
    identity = tool.sha(binary.read_bytes())
    build = copy.deepcopy(SYNTHETIC["candidate"])
    build["release"] = "synthetic-🙂"
    build.update(executable_sha256=identity, resolved_path=str(binary))
    proof = receipt(build)
    value = {"version": 1, "builds": {identity: {"candidate": build, "qualification": proof}}}
    tool.generate(value, tmp_path)
    host_path = tmp_path / "src/omegaflow/bash_builds.py"
    namespace = {"__file__": str(host_path), "__name__": "synthetic_host"}
    exec(compile(host_path.read_text(), str(host_path), "exec"), namespace)
    lookup = namespace["lookup"]
    inputs = proof["adapter_inputs"]
    target = copy.deepcopy(build["target"])
    assert lookup(str(binary), identity, target, inputs)["candidate"] == build
    # Exercise strict admission through the actual generated lookup, including
    # a malformed sibling that must not be ignored when selecting this build.
    for fault in ("candidate-digest", "result-digest", "case", "unknown", "sibling", "unqualified"):
        invalid = copy.deepcopy(value)
        entry = invalid["builds"][identity]
        if fault == "candidate-digest":
            entry["qualification"]["candidate_sha256"] = "0" * 64
        elif fault == "result-digest":
            entry["qualification"]["result_sha256"] = "0" * 64
        elif fault == "case":
            entry["qualification"]["result"]["cases"][tool.case_ids()[0]] = False
        elif fault == "unknown":
            entry["candidate"]["unknown"] = True
        elif fault == "sibling":
            invalid["builds"]["f" * 64] = {"candidate": {}, "qualification": None}
        else:
            entry["qualification"] = None
        namespace["_TABLE"] = tool.canonical(invalid).decode()
        with pytest.raises(ValueError):
            lookup(str(binary), identity, target, inputs)
    namespace["_TABLE"] = tool.canonical(value).decode()
    consumer = tmp_path / "bin-bash"
    consumer.symlink_to(binary)
    assert lookup(str(consumer), identity, target, inputs)["candidate"] == build
    for expected, current_target, assets in [("f" * 64, target, inputs),
                                              (identity, {**target, "arch": "arm64"}, inputs),
                                              (identity, {**target, "os_release_id": "other"}, inputs),
                                              (identity, {**target, "version_id": "2"}, inputs),
                                              (identity, target, {})]:
        with pytest.raises(ValueError):
            lookup(str(binary), expected, current_target, assets)
    system_rc = tmp_path / "system.bashrc"
    build["system_rc"] = str(system_rc)
    value["builds"][identity]["qualification"] = receipt(build)
    tool.generate(value, tmp_path)
    namespace = {"__file__": str(host_path), "__name__": "synthetic_host"}
    exec(compile(host_path.read_text(), str(host_path), "exec"), namespace)
    assert namespace["lookup"](str(binary), identity, target, inputs)["candidate"] == build
    go_dir = tmp_path / "runtime/envoy/bashbuild"
    for filename in ("lookup.go", "table.go"):
        shutil.copy(ROOT / "runtime/envoy/bashbuild" / filename, go_dir)
    (go_dir / "go.mod").write_text("module synthetic\n\ngo 1.20\n")
    go_test = '''package bashbuild
import ("os"; "testing")
func TestSynthetic(t *testing.T) {
    inputs := map[string]string{INPUTS}
    data,err:=readTable(TABLE,TABLEHASH);if err!=nil {t.Fatal(err)}
    inputs[tablePath]=TABLEHASH
    lookup:=func(path,digest string,target Target,inputs map[string]string)(Entry,error){return lookupBytes(path,digest,target,inputs,data)}
    target := Target{OS:"linux", Arch:"amd64", OSReleaseID:"synthetic", VersionID:"1"}
    for range []int{0,1} {
        entry,err:=lookup(PATH,DIGEST,target,inputs)
        if err!=nil {t.Fatal(err)}
        if entry.Candidate.Target.OSReleaseID!="synthetic" || entry.Candidate.Target.VersionID!="1" {t.Fatal("lost distribution metadata")}
        if _,err:=lookup(CONSUMER,DIGEST,target,inputs);err!=nil {t.Fatal(err)}
        for _, bad := range []struct{digest string; target Target; inputs map[string]string}{
            {"wrong",target,inputs},
            {DIGEST,Target{OS:"linux",Arch:"arm64",OSReleaseID:"synthetic",VersionID:"1"},inputs},
            {DIGEST,Target{OS:"linux",Arch:"amd64",OSReleaseID:"other",VersionID:"1"},inputs},
            {DIGEST,Target{OS:"linux",Arch:"amd64",OSReleaseID:"synthetic",VersionID:"2"},inputs},
            {DIGEST,target,nil},
        } {
            if _,err:=lookup(PATH,bad.digest,bad.target,bad.inputs);err==nil {t.Fatal("accepted mismatch")}
        }
    }
    if err:=os.WriteFile(RC,[]byte("present"),0600);err!=nil {t.Fatal(err)}
    if _,err:=lookup(PATH,DIGEST,target,inputs);err==nil {t.Fatal("accepted present rc")}
}
'''
    runtime_table = tmp_path / "runtime/envoy/etc/bash-builds.json"
    for key, val in {"TABLEHASH": json.dumps(tool.sha(runtime_table.read_bytes())), "TABLE": json.dumps(str(runtime_table)), "INPUTS": ",".join(json.dumps(k)+":"+json.dumps(v) for k,v in inputs.items()),
                     "PATH": json.dumps(str(binary)), "CONSUMER": json.dumps(str(consumer)), "DIGEST": json.dumps(identity), "RC": json.dumps(str(system_rc))}.items():
        go_test = go_test.replace(key, val)
    (go_dir / "lookup_test.go").write_text(go_test)
    subprocess.run(["go", "test", "-count=1", "-coverprofile=coverage.out", "./..."], cwd=go_dir, check=True, capture_output=True)
    assert (go_dir / "coverage.out").read_text().startswith("mode: set\n")
    namespace = {"__file__": str(host_path), "__name__": "synthetic_host"}
    exec(compile(host_path.read_text(), str(host_path), "exec"), namespace)
    with pytest.raises(ValueError, match="system"):
        namespace["lookup"](str(binary), identity, target, inputs)
    system_rc.unlink()
    binary.write_bytes(b"changed build")
    with pytest.raises(ValueError, match="unknown"):
        namespace["lookup"](str(binary), identity, target, inputs)
    binary.write_bytes(b"synthetic executable bytes, never executed")
    build["resolved_path"] = "/synthetic/wrong/bash"
    build["system_rc"] = "none"
    value["builds"][identity]["qualification"] = receipt(build)
    tool.generate(value, tmp_path)
    namespace = {"__file__": str(host_path), "__name__": "synthetic_host"}
    exec(compile(host_path.read_text(), str(host_path), "exec"), namespace)
    with pytest.raises(ValueError, match="resolved"):
        namespace["lookup"](str(binary), identity, target, inputs)


def test_shipped_python_table_rejects_regular_and_special_candidates(tmp_path):
    from omegaflow import bash_builds
    binary = tmp_path / "bash"
    binary.write_bytes(b"unqualified")
    with pytest.raises(ValueError, match="unknown"):
        bash_builds.lookup(str(binary), tool.sha(binary.read_bytes()), {"os": "linux", "arch": "amd64", "os_release_id": "synthetic", "version_id": "1"}, {})
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="nonregular"):
        bash_builds.lookup(str(fifo), "unknown", {"os": "linux", "arch": "amd64", "os_release_id": "synthetic", "version_id": "1"}, {})


def probe_script(tmp_path, body):
    probe = tmp_path / "probe.py"
    probe.write_text(body)
    return [sys.executable, str(probe)]


def test_probe_capture_and_cleanup(tmp_path):
    assert tool.run_probe(probe_script(tmp_path, 'print("{}")'), 2) == {}
    for body, error in [
        ('import sys; sys.exit(3)', "failed"),
        ('import sys; print("diagnostic",file=sys.stderr); print("{}")', "diagnostic"),
        ('print("x"*2200000)', "bound"),
        ('import time; time.sleep(5)', "timeout"),
        ('print("not JSON")', "Expecting value"),
    ]:
        with pytest.raises(ValueError, match=error):
            tool.run_probe(probe_script(tmp_path, body), 0.15)
    for timeout in (True, float("nan"), 0, 301):
        with pytest.raises(ValueError, match="timeout"):
            tool.run_probe([sys.executable], timeout)


def qualification_plan(tmp_path, monkeypatch):
    fake = tmp_path / "bash"
    fake.write_bytes(b"synthetic Bash identity; never execute")
    monkeypatch.setattr(tool, "BASH", fake)
    monkeypatch.setattr(tool.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tool.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(tool.platform, "freedesktop_os_release", lambda: {"ID": "synthetic", "VERSION_ID": "1"})
    build = copy.deepcopy(SYNTHETIC["candidate"])
    build.update(executable_sha256=tool.sha(fake.read_bytes()), resolved_path=str(fake))
    output = {"measurements": SYNTHETIC["measurements"], "cases": dict.fromkeys(tool.case_ids(), True)}
    argv = probe_script(tmp_path, "print(" + repr(json.dumps(output)) + ")")
    argv[0] = str(Path(argv[0]).resolve())
    inputs = {str(fake): tool.sha(fake.read_bytes())}
    for path in argv:
        inputs[path] = tool.file_hash(path)[1]
    assets = {}
    for i, logical in enumerate(ASSETS):
        actual = tmp_path / f"asset-{i}"
        actual.write_bytes(b"synthetic trusted input")
        inputs[str(actual)] = tool.sha(actual.read_bytes())
        assets[logical] = str(actual)
    return build, {"inputs": inputs, "assets": assets, "probe": argv, "timeout_seconds": 2}


@pytest.mark.parametrize("relative_script", ["probe.py", "probe-link.py"])
def test_qualification_binds_relative_probe_files(tmp_path, monkeypatch, relative_script):
    build, plan = qualification_plan(tmp_path, monkeypatch)
    script = Path(plan["probe"][1])
    if relative_script != script.name:
        (tmp_path / relative_script).symlink_to(script.name)
    monkeypatch.chdir(tmp_path)
    plan["probe"][1] = relative_script
    digest = plan["inputs"].pop(str(script))
    with pytest.raises(ValueError, match="unbound probe file"):
        tool.qualify(build, plan)
    plan["inputs"][str(script)] = digest
    assert tool.qualify(build, plan)["inputs"][str(script)] == digest
    script.write_bytes(script.read_bytes() + b"\n# changed probe\n")
    with pytest.raises(ValueError, match="changed"):
        tool.qualify(build, plan)


def test_generation_is_utf8_under_ascii_locale(tmp_path):
    if not shutil.which("gofmt"):
        pytest.skip("Go formatting tool unavailable")
    value = table()
    source = tmp_path / "table.json"
    source.write_bytes(tool.canonical(value))
    output = tmp_path / "output"
    code = (
        "import importlib.util, locale, pathlib; "
        "assert locale.getencoding() in ('ANSI_X3.4-1968', 'US-ASCII'); "
        f"s=importlib.util.spec_from_file_location('tool', {str(ROOT / 'tools/bash_builds.py')!r}); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        f"v=m.read(pathlib.Path({str(source)!r})); root=pathlib.Path({str(output)!r}); "
        "m.generate(v, root); m.generate(v, root, check=True)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, encoding="utf-8",
        env=dict(os.environ, LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0"),
    )
    assert result.returncode == 0, result.stderr
    for filename, content in tool.generated(value).items():
        assert (output / filename).read_bytes() == content.encode("utf-8")


def test_qualification_runner_is_bound_repeatable_and_does_not_admit(tmp_path, monkeypatch):
    build, plan = qualification_plan(tmp_path, monkeypatch)
    shipped_table = tool.TABLE.read_bytes()
    result = tool.qualify(build, plan)
    assert result["repetitions"] == 2
    assert result["adapter_inputs"] == dict.fromkeys(ASSETS, tool.sha(b"synthetic trusted input"))
    tool.receipt(result, build, tool.case_ids())
    assert tool.TABLE.read_bytes() == shipped_table
    bad = copy.deepcopy(build)
    bad["target"]["arch"] = "arm64"
    with pytest.raises(ValueError, match="genuine"):
        tool.qualify(bad, plan)
    for field in ("os_release_id", "version_id"):
        bad = copy.deepcopy(build)
        bad["target"][field] = "other"
        with pytest.raises(ValueError, match="genuine"):
            tool.qualify(bad, plan)
    bad = copy.deepcopy(build)
    bad["executable_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="mismatch"):
        tool.qualify(bad, plan)
    rc = tmp_path / "rc"
    rc.symlink_to(tmp_path / "missing")
    bad = copy.deepcopy(build)
    bad["system_rc"] = str(rc)
    with pytest.raises(ValueError, match="present"):
        tool.qualify(bad, plan)
    bad_plan = copy.deepcopy(plan)
    bad_plan["assets"].pop(ASSETS[0])
    with pytest.raises(ValueError, match="adapter"):
        tool.qualify(build, bad_plan)
    real_run = tool.run_probe
    calls = []
    def changing(argv, timeout):
        value = real_run(argv, timeout)
        calls.append(True)
        if len(calls) == 2:
            value["measurements"]["startup_pty_base64"] = "eA=="
        return value
    monkeypatch.setattr(tool, "run_probe", changing)
    with pytest.raises(ValueError, match="nonrepeatable"):
        tool.qualify(build, plan)
    def mutation(argv, timeout):
        value = real_run(argv, timeout)
        Path(plan["assets"][ASSETS[0]]).write_bytes(b"changed")
        return value
    monkeypatch.setattr(tool, "run_probe", mutation)
    with pytest.raises(ValueError, match="changed"):
        tool.qualify(build, plan)


def test_file_hash_rejects_special_input_without_blocking(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="nonregular"):
        tool.file_hash(fifo)
    with pytest.raises(ValueError, match="nonregular"):
        tool.file_hash(tmp_path)


def test_cli_validation_and_failed_qualification_do_not_write(tmp_path):
    path = tmp_path / "table.json"
    path.write_text(json.dumps(table(False)))
    command = [sys.executable, str(ROOT / "tools/bash_builds.py")]
    result = subprocess.run(command + ["validate", str(path)], capture_output=True)
    assert result.returncode == 0
    path.write_text('{"version":1,"version":1,"builds":{}}')
    result = subprocess.run(command + ["validate", str(path)], capture_output=True)
    assert result.returncode == 1 and b"duplicate" in result.stderr
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(SYNTHETIC["candidate"]))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"inputs": {}, "assets": {}, "probe": [], "timeout_seconds": 1}))
    output = tmp_path / "receipt.json"
    result = subprocess.run(command + ["qualify", str(candidate), str(plan), str(output)], capture_output=True)
    assert result.returncode == 1 and not output.exists()


def test_system_rc_absence_fails_closed_on_observation_error(tmp_path, monkeypatch):
    assert tool.system_rc_absent("none")
    assert tool.system_rc_absent(str(tmp_path / "missing"))
    def denied(_):
        raise PermissionError("unable to observe")
    monkeypatch.setattr(tool.os, "lstat", denied)
    with pytest.raises(PermissionError):
        tool.system_rc_absent(str(tmp_path / "rc"))


def test_cli_qualification_publishes_receipt_without_overwriting(tmp_path, monkeypatch):
    build, plan = qualification_plan(tmp_path, monkeypatch)
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(build))
    probe_plan = tmp_path / "plan.json"
    probe_plan.write_text(json.dumps(plan))
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", ["bash_builds.py", "qualify", str(candidate), str(probe_plan), str(output)])
    tool.main()
    before = output.read_bytes()
    tool.receipt(tool.decode(before), build, tool.case_ids())
    with pytest.raises(SystemExit) as error:
        tool.main()
    assert error.value.code == 1 and output.read_bytes() == before


def test_cli_generation_and_stale_check(tmp_path, monkeypatch):
    source = tmp_path / "candidate-table.json"
    source.write_text(json.dumps(table(False)))
    required = tool.case_ids()
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    # Preserve the independently reviewed case corpus while testing output paths.
    monkeypatch.setattr(tool, "case_ids", lambda: required)
    monkeypatch.setattr(sys, "argv", ["bash_builds.py", "validate", str(source)])
    tool.main()
    monkeypatch.setattr(sys, "argv", ["bash_builds.py", "generate", "--table", str(source)])
    tool.main()
    sys.argv.append("--check")
    tool.main()
    (tmp_path / "src/omegaflow/bash_builds.py").write_text("stale")
    with pytest.raises(SystemExit) as error:
        tool.main()
    assert error.value.code == 1


def test_runtime_generation_is_independent_of_qualification_data(tmp_path):
    first = table()
    second = copy.deepcopy(first)
    entry = second["builds"]["b" * 64]
    entry["candidate"]["release"] = "different-qualification-<>&\u2028\u2029"
    entry["qualification"] = receipt(entry["candidate"])
    a, b = tool.generated(first), tool.generated(second)
    for name in a:
        if name.endswith(".go"):
            assert a[name] == b[name]
            assert "synthetic" not in a[name]
            assert tool.sha(tool.canonical(first)) not in a[name]
    assert a["runtime/envoy/etc/bash-builds.json"] != b["runtime/envoy/etc/bash-builds.json"]
    tool.generate(first, tmp_path)
    for name in ("runtime/envoy/etc/bash-builds.json", "runtime/envoy/bashbuild/table_generated.go"):
        original = (tmp_path / name).read_bytes()
        (tmp_path / name).write_bytes(original + b"\n")
        with pytest.raises(ValueError, match=name):
            tool.generate(first, tmp_path, check=True)
        (tmp_path / name).write_bytes(original)
    tool.generate(first, tmp_path, check=True)


def test_output_table_cannot_qualify_itself():
    value = table()
    entry = value["builds"]["b" * 64]
    proof = entry["qualification"]
    proof["adapter_inputs"][tool.RUNTIME_TABLE] = "e" * 64
    proof["assets"][tool.RUNTIME_TABLE] = tool.RUNTIME_TABLE
    proof["inputs"][tool.RUNTIME_TABLE] = "e" * 64
    with pytest.raises(ValueError, match="output"):
        tool.validate_table(value)


def test_complete_runtime_schema_matches_generated_host(tmp_path):
    """Shared mutations compare the actual generated host and Go validators."""
    if not shutil.which("go"):
        pytest.skip("Go toolchain unavailable")
    tool.generate(table(), tmp_path)
    host_path = tmp_path / "src/omegaflow/bash_builds.py"
    host = {"__file__": str(host_path), "__name__": "schema_host"}
    exec(compile(host_path.read_text(), str(host_path), "exec"), host)
    variants = [table(), table(False), {"version": 1, "builds": {}}]
    own_output = table()
    proof = own_output["builds"]["b" * 64]["qualification"]
    proof["adapter_inputs"][tool.RUNTIME_TABLE] = "e" * 64
    proof["assets"][tool.RUNTIME_TABLE] = tool.RUNTIME_TABLE
    proof["inputs"][tool.RUNTIME_TABLE] = "e" * 64
    variants.append(own_output)
    sibling = table()
    sibling["builds"]["f" * 64] = {"candidate": {}, "qualification": None}
    variants.append(sibling)
    for path, replacement in test_reject_table.pytestmark[0].args[1]:
        value = table()
        set_path(value, path, replacement)
        variants.append(value)
    for path, replacement in test_reject_measurements.pytestmark[0].args[1]:
        value = table()
        set_path(value["builds"]["b" * 64]["qualification"]["result"]["measurements"], path, replacement)
        variants.append(value)
    # Sweep the complete schema with wrong types, absent and unknown fields;
    # dynamic map keys are included, not just selected candidate fields.
    def paths(value, prefix=()):
        yield prefix, value
        if isinstance(value, dict):
            for key, child in value.items():
                yield from paths(child, (*prefix, key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from paths(child, (*prefix, index))
    original = table()
    for keys, child in paths(original):
        if not keys:
            continue
        for replacement in (None, [], {}, True, False, "invalid", 1.0):
            value = copy.deepcopy(original)
            set_path(value, keys, replacement)
            variants.append(value)
        if isinstance(child, dict):
            value = copy.deepcopy(original)
            set_path(value, keys, {**child, "unknown": None})
            variants.append(value)
            if child:
                value = copy.deepcopy(original)
                set_path(value, keys, {k: v for k, v in child.items() if k != next(iter(child))})
                variants.append(value)
    # Canonical digests must preserve non-ASCII, HTML and line separators.
    for release in ("<>&\u2028\u2029🙂\n\t\b", "a" * 4096):
        value = table()
        entry = value["builds"]["b" * 64]
        entry["candidate"]["release"] = release
        entry["qualification"] = receipt(entry["candidate"])
        variants.append(value)
    double_root = table()
    entry = double_root["builds"]["b" * 64]
    entry["candidate"]["resolved_path"] = "//synthetic/bash"
    entry["qualification"] = receipt(entry["candidate"])
    variants.append(double_root)
    data = [tool.canonical(v) for v in variants]
    data.append(json.dumps(table(), ensure_ascii=True).encode())
    for surrogate in ("\ud800", "\udc00"):
        invalid_unicode = table()
        invalid_unicode["builds"]["b" * 64]["qualification"]["probe"].append(surrogate)
        data.append(json.dumps(invalid_unicode, ensure_ascii=True).encode())
    data.extend([b'{"version":1,"version":1,"builds":{}}', b'{}{}', b'\xff',
                 br'{"version":1,"builds":{},"x":"\ud800"}',
                 br'{"version":1,"builds":{},"x":"\udc00"}', b'{"x":NaN}',
                 b'{"x":"\\', b'{"x":"\\u00', br'{"x":"\uzzzz"}',
                 br'{"x":"\ud800\u0041"}', br'{"x":"\ud800\uzzzz"}',
                 b'[' * 66 + b'0' + b']' * 66, b'{', b'[', b'{"x":'])
    records = []
    for raw in data:
        try:
            value = host["validate_table"](host["decode"](raw))
            valid = all(entry["qualification"] is not None for entry in value["builds"].values())
        except (ValueError, TypeError, KeyError):
            valid = False
        records.append({"data": base64.b64encode(raw).decode(), "valid": valid})
    manifest = tmp_path / "variants.json"
    manifest.write_text(json.dumps(records))
    go_dir = tmp_path / "runtime/envoy/bashbuild"
    for filename in ("lookup.go", "table.go"):
        shutil.copy(ROOT / "runtime/envoy/bashbuild" / filename, go_dir)
    (go_dir / "go.mod").write_text("module schema\n\ngo 1.25\n")
    (go_dir / "schema_test.go").write_text('''package bashbuild
import ("os";"testing";"encoding/json";"encoding/base64")
func TestSharedSchema(t *testing.T){
 data,e:=os.ReadFile(MANIFEST);if e!=nil{t.Fatal(e)}
 var cases []struct{Data string;Valid bool};if e=json.Unmarshal(data,&cases);e!=nil{t.Fatal(e)}
 for i,c:=range cases{raw,e:=base64.StdEncoding.DecodeString(c.Data);if e!=nil{t.Fatal(e)}
 _,e=parseTable(raw);if (e==nil)!=c.Valid{t.Errorf("case %d: host=%v Go error=%v data=%s",i,c.Valid,e,raw)}}
}
'''.replace("MANIFEST", json.dumps(str(manifest))))
    result = subprocess.run(["go", "test", "-count=1", "-coverprofile=coverage.out", "./..."], cwd=go_dir, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
