#!/usr/bin/env python3
from __future__ import annotations

import argparse
import compileall
import json
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    if not compileall.compile_dir(ROOT / "src", quiet=1):
        return 1
    if not compileall.compile_dir(ROOT / "scripts", quiet=1):
        return 1
    if not compileall.compile_dir(ROOT / "bin", quiet=1):
        return 1

    sys.path.insert(0, str(ROOT / "src"))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1

    tomllib.loads((ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"))
    json.loads((ROOT / "config" / "model-routing.json").read_text(encoding="utf-8"))
    for path in sorted((ROOT / ".codex" / "agents").glob("novel-*.toml")):
        tomllib.loads(path.read_text(encoding="utf-8"))

    diff = subprocess.run(
        ["git", "diff", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        print(diff.stdout + diff.stderr, file=sys.stderr)
        return diff.returncode

    if args.smoke:
        smoke = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "runtime_smoke.py")],
            cwd=ROOT,
            check=False,
        )
        if smoke.returncode != 0:
            return smoke.returncode
        long_smoke = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "long_horizon_smoke.py")],
            cwd=ROOT,
            check=False,
        )
        if long_smoke.returncode != 0:
            return long_smoke.returncode

    print("check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
