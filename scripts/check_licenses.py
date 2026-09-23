#!/usr/bin/env python3
# Copyright 2026 Sergey Zinchenko
# SPDX-License-Identifier: Apache-2.0
"""Fail if runtime packages drift from NOTICE or use a non-allowlisted license.

Install requirements.txt, then pip-licenses, then run this script with that
interpreter. pip-licenses itself is not part of the image and is ignored.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICE = (ROOT / "NOTICE").read_text(encoding="utf-8")

# Reported SPDX / pip strings that are OSI-approved for this image.
ALLOW = {
    "MIT",
    "MIT License",
    "BSD-3-Clause",
    "BSD License",
    "Mozilla Public License 2.0 (MPL 2.0)",
    "PSF-2.0",
}

# Tooling installed only to run this check.
IGNORE = {"pip-licenses", "prettytable", "wcwidth", "pip", "setuptools", "wheel"}


def main() -> int:
    tool = Path(sys.executable).parent / ("pip-licenses.exe" if os.name == "nt" else "pip-licenses")
    raw = subprocess.check_output([str(tool), "--format=json"], text=True)
    rows = json.loads(raw)
    missing = []
    bad = []
    for row in rows:
        name = row["Name"]
        if name.lower() in IGNORE or name in IGNORE:
            continue
        if name not in NOTICE:
            missing.append(name)
        lic = row.get("License") or ""
        if lic not in ALLOW:
            bad.append(f"{name}: {lic}")
    if missing or bad:
        if missing:
            print("NOTICE is missing packages:", ", ".join(sorted(missing)), file=sys.stderr)
        if bad:
            print("License not allowlisted:", "; ".join(bad), file=sys.stderr)
        return 1
    print(f"license check ok ({len(rows)} distributions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
