"""The JSON API the dashboards call.

`chart_key` is only ever used as a lookup into an explicit registry, so a client
cannot address arbitrary code, and each entry carries the roles allowed to read it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.analytics import queries
from app.analytics.charts import CHART_REGISTRY
from app.db import get_db
from app.deps import AccessScope, get_access_scope
from app.schemas import FilterOptions, FilterParams, filter_params

router = APIRouter(prefix="/api", tags=["charts"])


@router.get("/filters")
def get_filter_options(
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
) -> FilterOptions:
    return queries.filter_options(db, scope)


@router.get("/charts")
def list_charts(scope: AccessScope = Depends(get_access_scope)) -> dict:
    """Advertise the charts this caller is allowed to request."""
    return {
        "charts": [
            {"key": definition.key, "title": definition.title, "kind": definition.kind}
            for definition in CHART_REGISTRY.values()
            if scope.role in definition.roles
        ]
    }


@router.get("/charts/{chart_key}")
def get_chart(
    chart_key: str = Path(min_length=1, max_length=64, pattern=r"^[a-z]+\.[a-z_]+$"),
    filters: FilterParams = Depends(filter_params),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
) -> dict:
    definition = CHART_REGISTRY.get(chart_key)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown chart."
        )
    if scope.role not in definition.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role cannot view this chart.",
        )
    payload = definition.handler(db, scope.narrow(filters, db), scope)
    payload.setdefault("key", definition.key)
    payload.setdefault("title", definition.title)
    return payload
