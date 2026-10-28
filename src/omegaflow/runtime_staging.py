"""Stage only the fixed installed, qualified Linux/amd64 package resource."""
from contextlib import contextmanager, ExitStack
from importlib.resources import files
import os
from pathlib import Path
import shutil
import stat
import tempfile

from .runtime_artifact import capture_artifact, DIRECTORY


def _remove_parent_contents(parent_fd):
    """Remove every entry in the owned allocation through its descriptor."""
    for name in os.listdir(parent_fd):
        try:
            mode = os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(mode):
            shutil.rmtree(name, dir_fd=parent_fd)
        else:
            os.unlink(name, dir_fd=parent_fd)


@contextmanager
def stage_runtime():
    """Yield a verified read-only tree under a private parent for this operation."""
    source = Path(files("omegaflow") / "runtime" / "linux-amd64")
    with capture_artifact(source) as artifact, ExitStack() as stack:
        parent = Path(tempfile.mkdtemp(prefix="omegaflow-runtime-"))
        parent_fd = os.open(parent, DIRECTORY)
        stack.callback(os.close, parent_fd)
        owned = []
        try:
            root = parent / "runtime"
            root.mkdir(mode=0o700)
            # Keep directory descriptors for cleanup. Never chmod a pathname
            # that a failed operation could have replaced with a symlink.
            for name in sorted(artifact.directories, key=lambda n: (n.count("/"), n)):
                directory = root / name
                if name:
                    directory.mkdir(mode=0o700)
                fd = os.open(directory, DIRECTORY)
                stack.callback(os.close, fd)
                owned.append(fd)
            for name, data in artifact.payload.items():
                with (root / name).open("xb") as stream:
                    stream.write(data)
                (root / name).chmod(0o555 if name.startswith("bin/") else 0o444)
            artifact.verify()
            # Publish the original manifest only after all verified payload.
            with (root / "manifest.json").open("xb") as stream:
                stream.write(artifact.manifest_bytes)
            (root / "manifest.json").chmod(0o444)
            for fd in reversed(owned):
                os.fchmod(fd, 0o555)
            with capture_artifact(root, readonly=True) as staged:
                if staged.manifest_bytes != artifact.manifest_bytes or staged.payload != artifact.payload:
                    raise ValueError("staged runtime differs from installed source")
                artifact.verify()
                yield root
        finally:
            os.fchmod(parent_fd, 0o700)
            for fd in owned:
                os.fchmod(fd, 0o700)
            _remove_parent_contents(parent_fd)
            try:
                same_parent = os.path.samestat(parent.lstat(), os.fstat(parent_fd))
            except FileNotFoundError:
                same_parent = False
            if not same_parent:
                raise ValueError("runtime stage parent replaced; owned payload removed")
            parent.rmdir()
