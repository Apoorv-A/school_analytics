"""F07: parent page chrome stays in sync with the selected child."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import Grade, Section, Student, User


def _multi_child_guardian(db) -> tuple[User, list[int]] | None:
    row = db.execute(
        select(Student.guardian_user_id, func.count())
        .where(Student.guardian_user_id.is_not(None))
        .group_by(Student.guardian_user_id)
        .having(func.count() > 1)
        .order_by(Student.guardian_user_id)
        .limit(1)
    ).first()
    if row is None:
        return None
    guardian = db.get(User, row[0])
    assert guardian is not None
    child_ids = list(
        db.scalars(
            select(Student.id)
            .where(Student.guardian_user_id == guardian.id)
            .order_by(Student.id)
        ).all()
    )
    return guardian, child_ids


def _topbar_subtitle(html: str) -> str | None:
    match = re.search(r'class="topbar__subtitle"[^>]*>([^<]+)', html)
    return match.group(1).strip() if match else None


def _grade_section_pill(html: str) -> str | None:
    match = re.search(
        r'class="topbar__actions"[^>]*>[\s\S]*?'
        r'class="pill pill--neutral nowrap"[^>]*>([^<]+)',
        html,
    )
    return match.group(1).strip() if match else None


def _expected_pill(db, student_id: int) -> str:
    row = db.execute(
        select(Grade.name, Section.name)
        .join(Section, Section.grade_id == Grade.id)
        .join(Student, Student.section_id == Section.id)
        .where(Student.id == student_id)
    ).one()
    return f"{row[0]} {row[1]}"


class TestParentTopbarChildSync:
    """Topbar grade pill must match the student resolved from student_id."""

    def test_pill_matches_subtitle_for_every_linked_child(self, db):
        from tests.conftest import DEFAULT_HEADERS, CsrfTestClient, _login
        from app.main import app

        found = _multi_child_guardian(db)
        if found is None:
            pytest.skip("seed has no guardian with multiple children")
        guardian, child_ids = found
        client = CsrfTestClient(
            app, base_url="http://testserver", headers=DEFAULT_HEADERS
        )
        client = _login(guardian.email)

        for sid in child_ids:
            student = db.get(Student, sid)
            assert student is not None
            html = client.get(f"/parent?student_id={sid}").text
            subtitle = _topbar_subtitle(html)
            pill = _grade_section_pill(html)
            expected = _expected_pill(db, sid)
            assert pill == expected, (
                f"pill for {student.full_name} (id={sid}) should be {expected!r}, "
                f"got {pill!r}"
            )
            assert subtitle is not None
            assert expected in subtitle
            assert student.full_name.split()[0] in subtitle


class TestParentRemarkDrawerUx:
    def test_drawer_shell_uses_scrim_not_modal_backdrop(self, parent_context):
        html = parent_context["client"].get("/parent").text
        assert 'data-remark-notices-scrim' in html
        assert "remark-notices-drawer__scrim" in html

        js_path = (
            Path(__file__).resolve().parents[1] / "app/static/js/parent_remark_notices.js"
        )
        source = js_path.read_text(encoding="utf-8")
        assert "dialog.show(" in source
        assert "showModal" not in source
        assert "data-remark-notices-scrim" in source


class TestParentStudentFilterReload:
    """Changing the student filter must reload the page when siblings exist."""

    def test_filters_js_reloads_for_student_change_with_child_switcher(self):
        js_path = Path(__file__).resolve().parents[1] / "app/static/js/filters.js"
        source = js_path.read_text(encoding="utf-8")
        assert "portalChildSwitcherPresent" in source
        assert "navigateWithCurrentFilters" in source
        assert 'data-filter") === "student_id"' in source
