"""F06 MVP: draft vs published teacher remarks."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Remark, RemarkCategory, RemarkVisibility, SecurityAuditEvent, Student


def _remark_term_id(db, student_id: int) -> int:
    from sqlalchemy import select

    from app.models import Student, Term

    student = db.get(Student, student_id)
    assert student is not None
    term_id = db.scalars(
        select(Term.id)
        .where(Term.tenant_id == student.tenant_id)
        .order_by(Term.sequence)
        .limit(1)
    ).one()
    return term_id


def _remark_student_id(teacher_context, parent_context, db) -> int:
    """A student the teacher may write for and the parent may read."""
    from sqlalchemy import select

    from app.models import Student, User

    guardian = db.scalars(
        select(User).where(User.email == parent_context["email"])
    ).one()
    shared_id = db.scalars(
        select(Student.id)
        .where(
            Student.guardian_user_id == guardian.id,
            Student.section_id.in_(teacher_context["section_ids"]),
        )
        .order_by(Student.id)
        .limit(1)
    ).first()
    if shared_id is None:
        pytest.skip("no student shared between teacher section and parent login")
    return shared_id


class TestRemarksPublication:
    def test_draft_hidden_from_parent_chart(self, teacher_context, parent_context, db):
        student_id = _remark_student_id(teacher_context, parent_context, db)
        term_id = _remark_term_id(db, student_id)
        marker = "F06 draft remark must stay staff-only."
        created = teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": student_id,
                "term_id": term_id,
                "body": marker,
                "category": "academic",
            },
        )
        assert created.status_code == 200
        assert created.json()["visibility"] == "draft"
        remark_id = created.json()["id"]

        parent_items = parent_context["client"].get(
            "/api/charts/student.remarks",
            params={"student_id": student_id},
        ).json()["items"]
        assert not any(item["body"] == marker for item in parent_items)
        assert all("visibility" not in item for item in parent_items)

        teacher_items = teacher_context["client"].get(
            "/api/charts/student.remarks",
            params={"student_id": student_id},
        ).json()["items"]
        assert any(i["id"] == remark_id and i["body"] == marker for i in teacher_items)
        match = next(i for i in teacher_items if i["id"] == remark_id)
        assert match["visibility"] == "draft"

    def test_publish_on_create_visible_to_parent_with_audit(
        self, teacher_context, parent_context, db
    ):
        student_id = _remark_student_id(teacher_context, parent_context, db)
        term_id = _remark_term_id(db, student_id)
        marker = "F06 published on create."
        created = teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": student_id,
                "term_id": term_id,
                "body": marker,
                "category": "academic",
                "publish": True,
            },
        )
        assert created.status_code == 200
        body = created.json()
        assert body["visibility"] == "published"
        assert body.get("published_at")

        parent_items = parent_context["client"].get(
            "/api/charts/student.remarks",
            params={"student_id": student_id},
        ).json()["items"]
        assert any(item["body"] == marker for item in parent_items)

        event = db.scalars(
            select(SecurityAuditEvent)
            .where(
                SecurityAuditEvent.action == "remark.published",
                SecurityAuditEvent.target_id == str(body["id"]),
            )
            .order_by(SecurityAuditEvent.id.desc())
        ).first()
        assert event is not None
        assert event.target_type == "remark"
        assert (event.event_metadata or {}).get("student_id") == student_id

    def test_publish_endpoint_promotes_draft(self, teacher_context, parent_context, db):
        student_id = _remark_student_id(teacher_context, parent_context, db)
        term_id = _remark_term_id(db, student_id)
        marker = "F06 publish via endpoint."
        created = teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": student_id,
                "term_id": term_id,
                "body": marker,
                "category": "concern",
            },
        ).json()
        remark_id = created["id"]

        published = teacher_context["client"].post(f"/api/remarks/{remark_id}/publish")
        assert published.status_code == 200
        assert published.json()["visibility"] == "published"

        parent_items = parent_context["client"].get(
            "/api/charts/student.remarks",
            params={"student_id": student_id},
        ).json()["items"]
        assert any(item["body"] == marker for item in parent_items)

    def test_draft_concern_excluded_from_parent_insight_summary(
        self, teacher_context, parent_context, db
    ):
        student_id = _remark_student_id(teacher_context, parent_context, db)
        before = parent_context["client"].get(
            "/api/charts/student.insight_summary",
            params={"student_id": student_id},
        ).json()

        term_id = _remark_term_id(db, student_id)
        teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": student_id,
                "term_id": term_id,
                "body": "Draft concern — not for family risk copy.",
                "category": "concern",
            },
        )
        after = parent_context["client"].get(
            "/api/charts/student.insight_summary",
            params={"student_id": student_id},
        ).json()
        assert after == before

    def test_teacher_cannot_publish_out_of_scope_remark(
        self, teacher_context, db
    ):
        outside = teacher_context.get("outside_student_id")
        if outside is None:
            pytest.skip("no outside student")
        student = db.get(Student, outside)
        assert student is not None
        remark = Remark(
            tenant_id=student.tenant_id,
            student_id=outside,
            category=RemarkCategory.ACADEMIC,
            body="Outside scope",
            visibility=RemarkVisibility.DRAFT,
        )
        db.add(remark)
        db.commit()
        response = teacher_context["client"].post(f"/api/remarks/{remark.id}/publish")
        assert response.status_code == 404
