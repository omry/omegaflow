#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


path = Path(__file__).resolve().parent.parent / "reference-state.json"
value = json.loads(path.read_text(encoding="utf-8"))["state"]
print(value)
raise SystemExit(0 if value == sys.argv[1] else 1)
