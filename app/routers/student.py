"""Student portal: the same views as the parent portal, scoped to the student alone."""

from __future__ import annotations

from app.models import Role
from app.routers.student_views import build_student_portal

router = build_student_portal("/student", Role.STUDENT, "student")
