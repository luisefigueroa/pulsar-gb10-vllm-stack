#!/usr/bin/env python3
"""Deterministic stand-in for model_library.py in selftests.

Launch paths resolve prepared library views through PULSAR_MODEL_LIBRARY_PY.
Tests point that variable here and set FAKE_HOT_INFO_FILE to a canned
find-hot info JSON (see library_hot_fixture.py). find-hot prints the canned
info, verify-hot succeeds while the info file exists, and validate-hot-stamp
echoes the canned stamp validation. Every other verb fails loudly so a test
cannot silently exercise unshimmed behavior.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys


def main() -> int:
    verb = sys.argv[1] if len(sys.argv) > 1 else ""
    info_file = os.environ.get("FAKE_HOT_INFO_FILE", "")
    ready = bool(info_file) and pathlib.Path(info_file).is_file()
    if verb == "find-hot":
        if not ready:
            print(
                "model-library: ERROR: find-hot: no ready instance",
                file=sys.stderr,
            )
            return 1
        sys.stdout.write(pathlib.Path(info_file).read_text(encoding="utf-8"))
        return 0
    if verb == "verify-hot":
        return 0 if ready else 1
    if verb == "validate-hot-stamp":
        if not ready:
            return 1
        info = json.loads(pathlib.Path(info_file).read_text(encoding="utf-8"))
        print(json.dumps(info["stamp"]["validation"], sort_keys=True))
        return 0
    if verb == "write-hot-stamp":
        return 0
    print(f"fake-model-library: unsupported verb {verb!r}", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
