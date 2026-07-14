#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


path = Path(__file__).resolve().parent.parent / "reference-state.json"
path.write_text(json.dumps({"state": sys.argv[1]}) + "\n", encoding="utf-8")
