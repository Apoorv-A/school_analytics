"""Image tag rules shared with school-analytics-config ``scripts/version_rules.py``.

Staging and production must pin a plain ``MAJOR.MINOR.PATCH`` (optionally with a
``-hotfix.N`` suffix). Dev may use pre-release tags such as ``0.1.0-SNAPSHOT``.
The ``latest`` tag is never allowed.
"""

from __future__ import annotations

import re

RELEASED = re.compile(r"^\d+\.\d+\.\d+(-hotfix\.\d+)?$")
SYNTACTIC = re.compile(r"^\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?$")


def explain(tag: str) -> str:
    if tag == "latest":
        return "must not be 'latest'"
    if not SYNTACTIC.match(tag):
        return f"'{tag}' is not a semver"
    return (
        f"'{tag}' is a pre-release; staging and production must pin a released version"
        " (MAJOR.MINOR.PATCH, or MAJOR.MINOR.PATCH-hotfix.N)"
    )


def is_released(tag: str) -> bool:
    return bool(RELEASED.match(tag))
