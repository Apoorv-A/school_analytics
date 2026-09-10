"""Generate column dictionary Markdown from SQLAlchemy models."""

from __future__ import annotations

from pathlib import Path


def write_column_dictionary(root: Path) -> None:
    from app import models  # noqa: F401
    from app.db import Base

    data_dir = root / "docs" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Column Dictionary",
        "",
        "Generated from SQLAlchemy models. Do not edit by hand.",
        "",
    ]

    for table_name, table in sorted(Base.metadata.tables.items()):
        lines.append(f"## `{table_name}`")
        lines.append("")
        lines.append("| Column | Type | Nullable | Notes |")
        lines.append("| --- | --- | --- | --- |")
        for column in table.columns:
            nullable = "yes" if column.nullable else "no"
            notes: list[str] = []
            if column.primary_key:
                notes.append("PK")
            if column.foreign_keys:
                fks = ", ".join(str(fk.target_fullname) for fk in column.foreign_keys)
                notes.append(f"FK → {fks}")
            if column.unique:
                notes.append("unique")
            lines.append(
                f"| `{column.name}` | {column.type} | {nullable} | {'; '.join(notes) or '—'} |"
            )
        lines.append("")

    (data_dir / "column-dictionary.md").write_text("\n".join(lines), encoding="utf-8")
