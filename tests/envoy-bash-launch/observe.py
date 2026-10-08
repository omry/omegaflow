"""Test-only exec observer; no files remain open while enumerating descriptors."""

import json
import os

targets = {}
for descriptor in os.listdir("/proc/self/fd"):
    try:
        targets[descriptor] = os.readlink(f"/proc/self/fd/{descriptor}")
    except FileNotFoundError:
        # os.listdir's directory descriptor has already closed.
        pass
with open("/proc/self/status") as stream:
    status = stream.read()
print("OBSERVER " + json.dumps({"fds": list(targets), "targets": targets, "status": status}))
