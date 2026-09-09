"""Platform administration within a tenant."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.deps import AccessScope, get_access_scope, require_roles
from app.models import Role
from app.templating import templates
from app.tenant.context import require_tenant_context

router = APIRouter(prefix="/admin/platform", tags=["platform"])


@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_roles(Role.ADMIN))])
def tenant_settings_page(
    request: Request,
    scope: AccessScope = Depends(get_access_scope),
) -> HTMLResponse:
    tenant = require_tenant_context()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "title": "Tenant configuration",
            "subtitle": tenant.display_name,
            "nav": [],
            "filters": [],
            "pinned_filters": {},
            "cards": [
                {
                    "key": "tenant.settings",
                    "title": "Active settings",
                    "span": 12,
                    "holder": "state",
                    "static": {
                        "kind": "kpi",
                        "cards": [
                            {
                                "label": "Pass mark",
                                "value": f"{tenant.settings.pass_percentage:.0f}%",
                            },
                            {
                                "label": "At-risk band",
                                "value": f"{tenant.settings.at_risk_percentage:.0f}%",
                            },
                            {
                                "label": "Exports",
                                "value": "On" if tenant.settings.exports_enabled else "Off",
                            },
                            {
                                "label": "Parent portal",
                                "value": "On" if tenant.settings.parent_portal_enabled else "Off",
                            },
                        ],
                    },
                }
            ],
        },
    )
