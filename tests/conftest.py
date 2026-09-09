"""Shared test fixtures.

The environment is configured before any application module is imported, so the test
run uses its own throwaway SQLite file and its own ephemeral signing key rather than
touching a developer's local database.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

TEST_DB_PATH = Path(__file__).resolve().parent / "test_school.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["SECRET_KEY"] = secrets.token_urlsafe(32)
os.environ["DEMO_MODE"] = "false"
os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, Section, Student, Teacher, TeacherAssignment, User  # noqa: E402
from seed.generate import DEMO_PASSWORD, SUNRISE_HOST, generate  # noqa: E402

DEFAULT_HEADERS = {"Host": SUNRISE_HOST}


def _remove_db_files() -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(TEST_DB_PATH) + suffix)
        if candidate.exists():
            candidate.unlink()


@pytest.fixture(scope="session", autouse=True)
def seeded_database():
    _remove_db_files()
    generate(reset=True)
    yield
    _remove_db_files()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def _login(email: str) -> TestClient:
    client = TestClient(app, headers=DEFAULT_HEADERS, follow_redirects=False)
    response = client.post(
        "/login", data={"email": email, "password": DEMO_PASSWORD}
    )
    assert response.status_code == 303, f"login failed for {email}: {response.status_code}"
    return client


def _first_email(db, role: Role) -> str:
    from app.models import Tenant

    tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
    return db.scalars(
        select(User.email)
        .where(User.role == role, User.tenant_id == tenant.id)
        .order_by(User.id)
        .limit(1)
    ).one()


@pytest.fixture
def anon_client() -> TestClient:
    return TestClient(app, headers=DEFAULT_HEADERS, follow_redirects=False)


@pytest.fixture
def admin_client(db) -> TestClient:
    return _login(_first_email(db, Role.ADMIN))


@pytest.fixture
def teacher_context(db) -> dict:
    """A teacher plus one section they teach and one they do not."""
    teacher = db.scalars(
        select(Teacher)
        .join(TeacherAssignment, TeacherAssignment.teacher_id == Teacher.id)
        .order_by(Teacher.id)
        .limit(1)
    ).first()
    assigned = set(
        db.scalars(
            select(TeacherAssignment.section_id).where(
                TeacherAssignment.teacher_id == teacher.id
            )
        )
    )
    assigned |= set(
        db.scalars(select(Section.id).where(Section.class_teacher_id == teacher.id))
    )
    all_sections = set(db.scalars(select(Section.id)))
    outside = sorted(all_sections - assigned)
    user = db.get(User, teacher.user_id)
    own_students = set(
        db.scalars(select(Student.id).where(Student.section_id.in_(assigned)))
    )
    outside_students = sorted(
        db.scalars(select(Student.id).where(Student.section_id.in_(outside or {-1})))
    )
    return {
        "client": _login(user.email),
        "email": user.email,
        "teacher_id": teacher.id,
        "own_section_id": sorted(assigned)[0],
        "section_ids": sorted(assigned),
        "outside_section_id": outside[0] if outside else None,
        "own_student_id": sorted(own_students)[0],
        "outside_student_id": outside_students[0] if outside_students else None,
    }


@pytest.fixture
def parent_context(db) -> dict:
    """A guardian, their child, and a child that belongs to somebody else."""
    child = db.scalars(
        select(Student)
        .where(Student.guardian_user_id.is_not(None))
        .order_by(Student.id)
        .limit(1)
    ).one()
    guardian = db.get(User, child.guardian_user_id)
    own_ids = set(
        db.scalars(select(Student.id).where(Student.guardian_user_id == guardian.id))
    )
    other = db.scalars(
        select(Student.id).where(Student.id.not_in(own_ids)).order_by(Student.id).limit(1)
    ).one()
    return {
        "client": _login(guardian.email),
        "email": guardian.email,
        "child_id": child.id,
        "child_section_id": child.section_id,
        "other_student_id": other,
    }


@pytest.fixture
def student_context(db) -> dict:
    record = db.scalars(
        select(Student).where(Student.user_id.is_not(None)).order_by(Student.id).limit(1)
    ).one()
    user = db.get(User, record.user_id)
    other = db.scalars(
        select(Student.id).where(Student.id != record.id).order_by(Student.id).limit(1)
    ).one()
    return {
        "client": _login(user.email),
        "email": user.email,
        "student_id": record.id,
        "other_student_id": other,
    }
