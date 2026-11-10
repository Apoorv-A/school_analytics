"""F04: role-aware portal welcome (skip, reduced motion, non-blocking)."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.models import Role, User
from app.welcome import WELCOME_COOKIE_NAME, welcome_dismiss_cookie_value
from seed.generate import DEMO_PASSWORD, SUNRISE_HOST

DEFAULT_HEADERS = {"Host": SUNRISE_HOST}


@pytest.fixture
def teacher_client(teacher_context):
    return teacher_context["client"]


def test_welcome_dialog_on_teacher_dashboard(teacher_client):
    page = teacher_client.get("/teacher", headers=DEFAULT_HEADERS)
    assert page.status_code == 200
    html = page.text
    assert 'id="portal-welcome"' in html
    assert "Your classes at a glance" in html
    assert "/static/js/welcome.js" in html
    assert "welcome-orbit" in html


def test_parent_welcome_copy(parent_context):
    client = parent_context["client"]
    page = client.get(f"/parent?student_id={parent_context['child_id']}", headers=DEFAULT_HEADERS)
    assert page.status_code == 200
    assert "Supporting your child" in page.text


def test_dismiss_hides_welcome_on_next_load(teacher_client):
    dismiss = teacher_client.post("/api/welcome/dismiss", json={})
    assert dismiss.status_code == 200
    assert dismiss.cookies.get(WELCOME_COOKIE_NAME)

    page = teacher_client.get("/teacher", headers=DEFAULT_HEADERS)
    assert page.status_code == 200
    assert 'id="portal-welcome"' not in page.text
    assert "/static/js/welcome.js" not in page.text


def test_welcome_skipped_when_must_change_password(db, anon_client):
    user = db.scalars(
        select(User).where(User.role == Role.TEACHER).order_by(User.id).limit(1)
    ).one()
    user.must_change_password = True
    db.commit()

    anon_client.post("/login", data={"email": user.email, "password": DEMO_PASSWORD})
    page = anon_client.get("/change-password", headers=DEFAULT_HEADERS)
    assert page.status_code == 200
    assert "portal-welcome" not in page.text

    db.refresh(user)
    user.must_change_password = False
    db.commit()


def test_welcome_static_assets(admin_client):
    css = admin_client.get("/static/css/app.css", headers=DEFAULT_HEADERS).text
    assert ".welcome-dialog" in css
    assert "prefers-reduced-motion" in css
    js = admin_client.get("/static/js/welcome.js", headers=DEFAULT_HEADERS).text
    assert "prefers-reduced-motion" in js
    assert "/api/welcome/dismiss" in js


def test_welcome_cookie_binds_user(db):
    user = db.scalars(select(User).where(User.role == Role.ADMIN).limit(1)).one()
    value = welcome_dismiss_cookie_value(user)
    assert isinstance(value, str)
    assert len(value) > 8


def test_welcome_steps_list_markup(teacher_client):
    html = teacher_client.get("/teacher", headers=DEFAULT_HEADERS).text
    assert re.search(r'class="welcome-steps"', html)
    assert "Student lookup" in html or "Search students" in html


def test_help_welcome_redirects_to_portal_home(teacher_client):
    response = teacher_client.get(
        "/help/welcome", headers=DEFAULT_HEADERS, follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/teacher?welcome=1"


def test_welcome_reopens_after_dismiss_via_query(teacher_client):
    teacher_client.post("/api/welcome/dismiss", json={})
    page = teacher_client.get("/teacher", headers=DEFAULT_HEADERS)
    assert 'id="portal-welcome"' not in page.text

    page = teacher_client.get("/teacher?welcome=1", headers=DEFAULT_HEADERS)
    assert page.status_code == 200
    assert 'id="portal-welcome"' in page.text
    assert "/static/js/welcome.js" in page.text


def test_portal_tour_in_help_nav(teacher_client):
    html = teacher_client.get("/teacher", headers=DEFAULT_HEADERS).text
    assert "/help/welcome" in html
    assert "Portal tour" in html


def test_student_welcome_reopen_mobile_layout(student_context):
    client = student_context["client"]
    page = client.get("/student?welcome=1", headers=DEFAULT_HEADERS)
    assert page.status_code == 200
    assert "Your learning dashboard" in page.text
    css = client.get("/static/css/app.css", headers=DEFAULT_HEADERS).text
    assert "@media (max-width: 640px)" in css
    assert ".welcome-dialog__panel" in css
