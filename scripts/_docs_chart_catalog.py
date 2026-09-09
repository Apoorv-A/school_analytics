"""Generate chart catalog YAML and Markdown from CHART_DEFS."""

from __future__ import annotations

from pathlib import Path

import yaml


def write_chart_catalog(root: Path) -> None:
    from app.analytics.charts import CHART_DEFS

    entries = []
    for definition in CHART_DEFS:
        entries.append(
            {
                "key": definition.key,
                "title": definition.title,
                "kind": definition.kind,
                "roles": sorted(role.value for role in definition.roles),
            }
        )

    analytics_dir = root / "docs" / "analytics"
    analytics_dir.mkdir(parents=True, exist_ok=True)

    yaml_path = analytics_dir / "chart-catalog.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {"charts": entries},
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    md_lines = [
        "# Chart Catalog",
        "",
        "Generated from `app/analytics/charts.py`. Do not edit by hand.",
        "",
        "| Key | Title | Kind | Roles |",
        "| --- | --- | --- | --- |",
    ]
    for entry in entries:
        roles = ", ".join(entry["roles"])
        md_lines.append(
            f"| `{entry['key']}` | {entry['title']} | {entry['kind']} | {roles} |"
        )
    md_lines.append("")
    (analytics_dir / "chart-catalog.md").write_text("\n".join(md_lines), encoding="utf-8")
