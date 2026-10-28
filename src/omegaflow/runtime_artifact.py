"""The shared C4 manifest reader and installed qualified-artifact validator."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import os
from pathlib import Path, PurePosixPath
import re
import stat

from . import __version__, bash_builds as rules


PREFIX = "/omegaflow-runtime/"
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Artifact:
    """Hold no-follow descriptors until the consumer finishes copying bytes."""

    def __init__(self, root: Path, stack: ExitStack, readonly: bool):
        rules.require(root.is_absolute() and str(root) == os.path.normpath(root),
                      "noncanonical runtime root")
        self.bindings = []
        self.directories = {}
        self.files = {}
        self.payload = {}
        self.modes = {}

        def opened(parent, name, flags, full=True):
            fd = os.open(name, flags, dir_fd=parent)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            self.bindings.append((parent, name, fd, identity(info), full))
            return fd, info

        parent = os.open("/", DIRECTORY)
        stack.callback(os.close, parent)
        for index, part in enumerate(root.parts[1:]):
            parent, _ = opened(parent, part, DIRECTORY,
                               full=index == len(root.parts) - 2)
        rules.require(root != Path("/"), "runtime root cannot be filesystem root")

        def walk(fd, prefix):
            info = os.fstat(fd)
            mode = stat.S_IMODE(info.st_mode)
            rules.require(not mode & 0o7000 and (not readonly or mode == 0o555),
                          "runtime directory mode")
            names = sorted(os.listdir(fd))
            self.directories[prefix] = (fd, identity(info), names)
            for name in names:
                path = prefix + name
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child, _ = opened(fd, name, DIRECTORY)
                    walk(child, path + "/")
                else:
                    child, info = opened(fd, name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    rules.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                                  "nonregular or hardlinked runtime payload: " + path)
                    rules.require(not stat.S_IMODE(info.st_mode) & 0o7000,
                                  "special runtime mode: " + path)
                    with os.fdopen(os.dup(child), "rb") as stream:
                        self.payload[path] = stream.read(info.st_size + 1)
                    self.files[path] = (child, identity(info))
                    self.modes[path] = stat.S_IMODE(info.st_mode)

        walk(parent, "")
        self.manifest_bytes = self.payload.pop("manifest.json")
        self.manifest = rules.decode(self.manifest_bytes)
        self.verify()

    def verify(self):
        for parent, name, fd, before, full in self.bindings:
            observed = identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
            current = identity(os.fstat(fd))
            # Ancestors may legitimately gain unrelated children. Their binding
            # and type must remain exact; payload directories use the full stat.
            selected = slice(None) if full else slice(0, 3)
            rules.require(observed[selected] == before[selected] == current[selected],
                          "runtime path replaced or mutated: " + name)
        for fd, before, names in self.directories.values():
            rules.require(identity(os.fstat(fd)) == before and sorted(os.listdir(fd)) == names,
                          "runtime directory changed during capture")
        for fd, before in self.files.values():
            rules.require(identity(os.fstat(fd)) == before, "runtime file changed during capture")


def validate(artifact: Artifact, *, version, target, source_revision, go_toolchain,
             qualified, table, readonly):
    manifest, payload = artifact.manifest, artifact.payload
    require = rules.require
    rules.shape(manifest, "schema version source_revision go_toolchain target schemas files")
    require(manifest["schema"] == "omegaflow-runtime-manifest-v1", "runtime manifest schema")
    rules.text(manifest["version"])
    require(version is None or manifest["version"] == version, "runtime package version mismatch")
    require(type(manifest["source_revision"]) is str and
            re.fullmatch(r"[0-9a-f]{40}", manifest["source_revision"]), "runtime source revision")
    require(source_revision is None or manifest["source_revision"] == source_revision,
            "runtime source revision mismatch")
    require(manifest["go_toolchain"] == go_toolchain, "runtime toolchain mismatch")
    rules.shape(manifest["target"], "os arch")
    require(manifest["target"]["os"] == "linux" and manifest["target"]["arch"] in {"amd64", "arm64"} and
            (target is None or manifest["target"] == dict(zip(("os", "arch"), target))), "runtime platform mismatch")
    require(manifest["schemas"] == {"telemetry": "omegaflow-envoy-telemetry-v1",
            "private": "awsh-v1", "helper": "awsh-helper-v1"}, "runtime protocol schemas")
    require(type(manifest["files"]) is list, "runtime file inventory")
    wanted = {}
    fixed = {"bin/envoy", "bin/awsh", "etc/awsh-bashrc", "etc/inputrc", "etc/bash-builds.json"}
    directories = {""}
    for item in manifest["files"]:
        rules.shape(item, "path size executable sha256")
        name = item["path"]
        require(type(name) is str and name and not PurePosixPath(name).is_absolute() and
                str(PurePosixPath(name)) == name and ".." not in PurePosixPath(name).parts and
                "\x00" not in name and "\\" not in name and
                (name in fixed or name.startswith(("share/terminfo/", "lib/locale/"))),
                "runtime payload path")
        require(name not in wanted, "duplicate runtime payload path")
        require(type(item["size"]) is int and item["size"] >= 0 and
                type(item["executable"]) is bool, "runtime payload attributes")
        require(item["executable"] == name.startswith("bin/"), "runtime executable intent")
        rules.digest(item["sha256"])
        wanted[name] = item
        directories.update(str(p) + "/" for p in PurePosixPath(name).parents if str(p) != ".")
    require(set(wanted) == set(payload) and fixed <= set(wanted), "runtime inventory mismatch")
    require(set(artifact.directories) == directories, "runtime directory inventory mismatch")
    require(not payload["etc/inputrc"] and
            any(p.startswith("share/terminfo/") and p.endswith("/xterm-256color") for p in payload) and
            any(p.startswith("lib/locale/") for p in payload), "missing fixed runtime assets")
    for name, data in payload.items():
        item = wanted[name]
        require(len(data) == item["size"] and rules.sha(data) == item["sha256"],
                "runtime payload digest mismatch: " + name)
        mode = artifact.modes[name]
        require(bool(mode & 0o111) == item["executable"] and
                (not readonly or mode == (0o555 if item["executable"] else 0o444)),
                "runtime payload mode mismatch: " + name)
    require(not artifact.modes["manifest.json"] & 0o111 and
            (not readonly or artifact.modes["manifest.json"] == 0o444), "runtime manifest mode")
    if qualified:
        require(payload["etc/bash-builds.json"] == table, "runtime qualified table mismatch")
        packaged = rules.validate_table(rules.decode(table))
        require(bool(packaged["builds"]), "empty qualified runtime table")
        adapter = {PREFIX + name: rules.sha(data) for name, data in payload.items()
                   if name not in {"bin/envoy", "etc/bash-builds.json"}}
        for entry in packaged["builds"].values():
            require(all(entry["candidate"]["target"][key] == value
                        for key, value in manifest["target"].items()), "qualified runtime platform mismatch")
            proof = entry["qualification"]
            require(proof is not None and proof["adapter_inputs"] == adapter,
                    "packaged adapter bytes require qualification")
            require(proof["inputs"].get(PREFIX + "bin/envoy") == rules.sha(payload["bin/envoy"]),
                    "packaged Envoy bytes require qualification")


@contextmanager
def capture_artifact(root: Path, *, version=__version__, target=("linux", "amd64"),
                     source_revision=None, go_toolchain=None, qualified=True,
                     table=None, readonly=False):
    """Validate a bound tree while preserving exceptions from consumer code."""
    with ExitStack() as stack:
        try:
            artifact = Artifact(root, stack, readonly)
            validate(artifact, version=version, target=target, source_revision=source_revision,
                     go_toolchain=go_toolchain or rules.GO_TOOLCHAIN, qualified=qualified,
                     table=table if table is not None else rules._TABLE.encode() + b"\n",
                     readonly=readonly)
        except (OSError, KeyError) as exc:
            raise ValueError("invalid or changed runtime artifact: " + str(exc)) from exc
        yield artifact
        try:
            artifact.verify()
        except (OSError, KeyError) as exc:
            raise ValueError("invalid or changed runtime artifact: " + str(exc)) from exc
