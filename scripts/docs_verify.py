#!/usr/bin/env python3
"""Verify generated documentation matches current code (check mode)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "docs_generate.py")],
        check=True,
        cwd=ROOT,
    )
    result = subprocess.run(
        ["git", "diff", "--quiet", "docs/analytics/chart-catalog.yaml", "docs/analytics/chart-catalog.md", "docs/data/column-dictionary.md"],
        cwd=ROOT,
    )
    if result.returncode != 0:
        print("Generated docs drift from committed files. Run: python scripts/docs_generate.py")
        return 1
    print("Documentation is up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
