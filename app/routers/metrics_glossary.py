"""Versioned metric dictionary API (F01-03)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.analytics.metric_registry import (
    REGISTRY_VERSION,
    get_metric,
    metric_visible,
    search_metrics_for_role,
    serialize_metric_for_role,
)
from app.db import get_db
from app.deps import AccessScope, get_access_scope
from app.portals import render_dashboard
from app.schemas import FilterParams, filter_params

router = APIRouter(tags=["metrics-glossary"])

_METRIC_KEY = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")


@router.get("/api/metrics")
def list_metrics(
    q: str | None = Query(default=None, max_length=120),
    scope: AccessScope = Depends(get_access_scope),
) -> dict:
    """List glossary entries visible to the authenticated caller."""
    metrics = search_metrics_for_role(scope.role, q)
    return {
        "registryVersion": REGISTRY_VERSION,
        "count": len(metrics),
        "metrics": metrics,
    }


@router.get("/api/metrics/{metric_id}")
def get_metric_entry(
    metric_id: str = Path(min_length=3, max_length=64, pattern=_METRIC_KEY.pattern),
    scope: AccessScope = Depends(get_access_scope),
) -> dict:
    definition = get_metric(metric_id)
    if definition is None or not metric_visible(definition, scope.role):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    return {
        "registryVersion": REGISTRY_VERSION,
        "metric": serialize_metric_for_role(definition, scope.role),
    }


@router.get("/help/metrics", response_class=HTMLResponse)
def metrics_appendix_page(
    request: Request,
    q: str | None = Query(default=None, max_length=120),
    db: Session = Depends(get_db),
    scope: AccessScope = Depends(get_access_scope),
    filters: FilterParams = Depends(filter_params),
) -> HTMLResponse:
    """Searchable metrics appendix; sidebar nav wired in F01-04."""
    metrics = search_metrics_for_role(scope.role, q)
    return render_dashboard(
        request,
        db,
        scope,
        filters,
        title="Metrics & chart guide",
        subtitle=f"Registry version {REGISTRY_VERSION}",
        active_nav="/help/metrics",
        visible_filters=[],
        cards=[],
        template="help/metrics.html",
        extra={
            "metrics": metrics,
            "query": q or "",
            "registry_version": REGISTRY_VERSION,
        },
    )
