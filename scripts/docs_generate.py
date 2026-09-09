#!/usr/bin/env python3
"""Generate documentation artifacts from code sources of truth."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts._docs_chart_catalog import write_chart_catalog  # noqa: E402
from scripts._docs_column_dictionary import write_column_dictionary  # noqa: E402


def main() -> int:
    write_chart_catalog(ROOT)
    write_column_dictionary(ROOT)
    print("Documentation generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
