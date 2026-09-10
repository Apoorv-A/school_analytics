"""Database helper tests."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

from app.db import _apply_sqlite_pragmas


def test_apply_sqlite_pragmas_falls_back_when_wal_unavailable() -> None:
    cursor = MagicMock()
    cursor.execute.side_effect = [
        None,
        sqlite3.OperationalError("disk I/O error"),
        None,
    ]

    _apply_sqlite_pragmas(cursor)

    assert cursor.execute.call_args_list[0].args == ("PRAGMA foreign_keys=ON",)
    assert cursor.execute.call_args_list[1].args == ("PRAGMA journal_mode=WAL",)
    assert cursor.execute.call_args_list[2].args == ("PRAGMA journal_mode=DELETE",)
