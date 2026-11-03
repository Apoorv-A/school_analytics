"""One-off generator for immutable ``001_baseline`` Alembic revision.

Run from the repo root::

    PYTHONPATH=. python3 scripts/generate_baseline_001.py

The output is written to ``alembic/versions/001_baseline_multi_tenant.py``.
Baseline excludes tables/columns introduced in revisions 003-010.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, UniqueConstraint

from app import models  # noqa: F401
from app.db import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "alembic/versions/001_baseline_multi_tenant.py"

LATER_TABLES = {
    "import_runs",
    "import_sync_state",
    "intervention_cases",
    "intervention_contacts",
    "password_reset_tokens",
    "security_audit_events",
    "import_mapping_queue",
}

LATER_COLUMNS = {
    ("users", "external_id"),
    ("users", "source_system"),
    ("users", "totp_secret_enc"),
    ("users", "totp_pending_secret_enc"),
    ("users", "mfa_enrolled_at"),
    ("users", "session_version"),
    ("users", "must_change_password"),
    ("students", "external_id"),
    ("students", "source_system"),
    ("assessments", "external_id"),
    ("assessments", "source_system"),
    ("scores", "external_id"),
    ("scores", "source_system"),
    ("attendance", "external_id"),
    ("attendance", "source_system"),
    ("remarks", "external_id"),
    ("remarks", "source_system"),
}


def _sa_type(col: sa.Column) -> str:
    t = col.type
    if isinstance(t, sa.Uuid):
        return "sa.Uuid()"
    if isinstance(t, sa.String):
        if t.length is None:
            return "sa.Text()"
        return f"sa.String({t.length})"
    if isinstance(t, sa.Integer):
        return "sa.Integer()"
    if isinstance(t, sa.Boolean):
        return "sa.Boolean()"
    if isinstance(t, sa.Float):
        return "sa.Float()"
    if isinstance(t, sa.Text):
        return "sa.Text()"
    if isinstance(t, sa.Date):
        return "sa.Date()"
    if isinstance(t, sa.DateTime):
        return "sa.DateTime(timezone=True)"
    if isinstance(t, sa.JSON):
        return "sa.JSON()"
    if isinstance(t, sa.Enum):
        max_len = max(len(v) for v in t.enums)
        return f"sa.String({max_len})"
    raise TypeError(f"unsupported column type for {col.name}: {t!r}")


def _topo_sort(table_names: set[str]) -> list[str]:
    deps: dict[str, set[str]] = {name: set() for name in table_names}
    for name in table_names:
        table = Base.metadata.tables[name]
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            if parent in table_names and parent != name:
                deps[name].add(parent)
    ordered: list[str] = []
    remaining = set(table_names)
    while remaining:
        ready = sorted(name for name in remaining if not deps[name] - set(ordered))
        if not ready:
            ordered.extend(sorted(remaining))
            break
        ordered.extend(ready)
        remaining -= set(ready)
    return ordered


def _column_line(table_name: str, col: sa.Column, pk: bool) -> str:
    if (table_name, col.name) in LATER_COLUMNS:
        raise ValueError(f"unexpected later column {table_name}.{col.name}")
    parts = [f'sa.Column("{col.name}", {_sa_type(col)}']
    for fk in col.foreign_keys:
        parts.append(
            f'sa.ForeignKey("{fk.target_fullname}", ondelete="{fk.ondelete}")'
        )
    if pk:
        parts.append("primary_key=True")
    if not col.nullable and not pk:
        parts.append("nullable=False")
    if col.server_default is not None:
        default = col.server_default.arg
        if isinstance(default, bool):
            parts.append(f"server_default=sa.{str(default).lower()}()")
        elif isinstance(default, (int, float)):
            parts.append(f"server_default={default!r}")
        else:
            parts.append(f'server_default="{default}"')
    return "        " + ", ".join(parts) + "),"


def _render_upgrade(ordered: list[str]) -> str:
    lines: list[str] = []
    for name in ordered:
        table = Base.metadata.tables[name]
        pk_cols = {c.name for c in table.primary_key.columns}
        lines.append("    op.create_table(")
        lines.append(f'        "{name}",')
        for col in table.columns:
            if (name, col.name) in LATER_COLUMNS:
                continue
            lines.append(_column_line(name, col, col.name in pk_cols))
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and constraint.name:
                cols = ", ".join(f'"{c.name}"' for c in constraint.columns)
                lines.append(
                    f'        sa.UniqueConstraint({cols}, name="{constraint.name}"),'
                )
            elif isinstance(constraint, CheckConstraint) and constraint.name:
                sqltext = str(constraint.sqltext).replace('"', '\\"')
                lines.append(
                    f'        sa.CheckConstraint("{sqltext}", name="{constraint.name}"),'
                )
        lines.append("    )")
        seen_indexes: set[str] = set()
        for idx in sorted(table.indexes, key=lambda i: i.name or ""):
            if not idx.name or idx.name in seen_indexes:
                continue
            idx_names = [e.name for e in idx.expressions if hasattr(e, "name")]
            if any((name, col_name) in LATER_COLUMNS for col_name in idx_names):
                continue
            seen_indexes.add(idx.name)
            cols = ", ".join(f'"{col_name}"' for col_name in idx_names)
            lines.append(f'    op.create_index("{idx.name}", "{name}", [{cols}])')
        lines.append("")
    return "\n".join(lines)


def _render_downgrade(ordered: list[str]) -> str:
    lines: list[str] = []
    for name in reversed(ordered):
        lines.append(f'    op.drop_table("{name}")')
    return "\n".join(lines)


def main() -> None:
    table_names = {
        name for name in Base.metadata.tables if name not in LATER_TABLES
    }
    ordered = _topo_sort(table_names)
    upgrade_body = _render_upgrade(ordered)
    downgrade_body = _render_downgrade(ordered)

    content = f'''"""Baseline multi-tenant schema (immutable explicit DDL).

Greenfield PostgreSQL must run ``alembic upgrade head`` before app traffic.
Revision 001 creates core tenant-scoped tables; 002 applies RLS; 003+ add
incremental deltas. Local SQLite dev may still bootstrap via ``init_db()`` when
``startup_ddl_enabled()`` is true.

This file is generated once and must not import live ORM metadata — regenerate
with ``PYTHONPATH=. python3 scripts/generate_baseline_001.py`` only when
deliberately re-baselining a greenfield install (never after prod deploy).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
{upgrade_body}


def downgrade() -> None:
{downgrade_body}
'''
    OUTPUT.write_text(content)
    print(f"Wrote {OUTPUT} ({len(ordered)} tables)")
    print("Order:", ", ".join(ordered))


if __name__ == "__main__":
    main()
