"""Shared Jinja2 environment.

Autoescaping is on (Starlette's default for `Jinja2Templates`), so any student name,
remark, or other user-supplied text is HTML-escaped wherever it is rendered.
"""

from __future__ import annotations

import json
from typing import Any

from markupsafe import Markup
from starlette.templating import Jinja2Templates

from app.config import settings
from app.models import AssessmentType, Role

templates = Jinja2Templates(directory=str(settings.templates_dir))


def to_json(value: Any) -> Markup:
    """Embed a value in a <script> block without allowing tag injection.

    The angle brackets and ampersand are escaped to their JSON unicode forms first, so
    the result cannot close the surrounding script tag or open a new one. Only then is
    it marked safe.
    """
    encoded = json.dumps(value, default=str)
    encoded = (
        encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    return Markup(encoded)  # noqa: S704 - escaped immediately above


def percent(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "--"
    return f"{value:.{digits}f}%"


def signed(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "--"
    return f"{value:+.{digits}f}"


templates.env.filters["to_json"] = to_json
templates.env.filters["percent"] = percent
templates.env.filters["signed"] = signed
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["Role"] = Role
templates.env.globals["assessment_types"] = [
    {"value": t.value, "label": t.label} for t in AssessmentType
]
