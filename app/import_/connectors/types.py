"""Shared types for ERP import connectors."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ConnectorKind(StrEnum):
    """Supported connector transports (local is production-ready; others are stubs)."""

    LOCAL = "local"
    SFTP = "sftp"
    REST = "rest"


@dataclass(frozen=True)
class ConnectorSyncResult:
    """Outcome of a connector fetch + apply cycle."""

    kind: ConnectorKind
    source: str
    report: dict[str, Any]
    applied: bool
