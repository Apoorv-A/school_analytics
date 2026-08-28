"""Parent / guardian portal: one child at a time, with a switcher for siblings."""

from __future__ import annotations

from app.models import Role
from app.routers.student_views import build_student_portal

router = build_student_portal("/parent", Role.PARENT, "parent")
