"""Server-side CSV export.

The browser can already export whatever a card is showing. This endpoint exists for
the full underlying dataset, which is larger than a chart payload, and it reuses the
same chart registry so an export can never return more than the card it came from.

The filename is built from the chart key and a timestamp only. No user-supplied value
ever reaches the filesystem or a shell.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.analytics.charts import CHART_REGISTRY
from app.db import get_db
from app.deps import AccessScope, get_access_scope
from app.schemas import FilterParams, filter_params

router = APIRouter(prefix="/export", tags=["export"])


def _rows_for(payload: dict) -> tuple[list[str], list[list[object]]]:
    """Flatten any chart payload into a header row and data rows."""
    kind = payload.get("kind")

    if kind == "table":
        header = [column["label"] for column in payload["columns"]]
        rows = [
            [row.get(column["key"]) for column in payload["columns"]]
            for row in payload["rows"]
        ]
        return header, rows

    if kind == "kpi":
        return (
            ["Metric", "Value", "Detail"],
            [[card["label"], card["value"], card.get("hint")] for card in payload["cards"]],
        )

    if kind == "heatmap":
        header = [""] + [column["label"] for column in payload["columns"]]
        rows = [
            [row["label"], *payload["values"][index]]
            for index, row in enumerate(payload["rows"])
        ]
        return header, rows

    if kind == "timeline":
        return (
            ["Category", "Term", "Teacher", "Subject", "Remark"],
            [
                [
                    item["category"],
                    item["term"],
                    item["teacher"],
                    item["subject"],
                    item["body"],
                ]
                for item in payload.get("items", [])
            ],
        )

    if kind == "scatter":
        points = payload["series"][0]["data"] if payload.get("series") else []
        return (
            ["Student", "Class", "Attendance %", "Average %", "Status"],
            [
                [p.get("label"), p.get("section"), p.get("x"), p.get("y"), p.get("risk")]
                for p in points
            ],
        )

    header = ["Label"] + [series["label"] for series in payload.get("series", [])]
    rows = [
        [label] + [series["data"][index] for series in payload["series"]]
        for index, label in enumerate(payload.get("labels", []))
    ]
    return header, rows


@router.get("/{chart_key}.csv")
def export_chart_csv(
    chart_key: str = Path(min_length=1, max_length=64, pattern=r"^[a-z]+\.[a-z_]+$"),
    filters: FilterParams = Depends(filter_params),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
) -> StreamingResponse:
    definition = CHART_REGISTRY.get(chart_key)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown export."
        )
    if scope.role not in definition.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role cannot export this data.",
        )

    payload = definition.handler(db, scope.narrow(filters), scope)
    header, rows = _rows_for(payload)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([definition.title])
    writer.writerow([f"Exported {datetime.now(UTC):%Y-%m-%d %H:%M} UTC"])
    writer.writerow([])
    writer.writerow(header)
    writer.writerows(rows)
    buffer.seek(0)

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    filename = f"{chart_key.replace('.', '-')}-{stamp}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
