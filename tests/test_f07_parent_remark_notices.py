"""F07: parent unread published-remark notices."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import GuardianRemarkRead, Remark, RemarkCategory, RemarkVisibility, Student


def _remark_term_id(db, student_id: int) -> int:
    from app.models import Term

    student = db.get(Student, student_id)
    assert student is not None
    return db.scalars(
        select(Term.id)
        .where(Term.tenant_id == student.tenant_id)
        .order_by(Term.sequence)
        .limit(1)
    ).one()


def _shared_student(teacher_context, parent_context, db) -> int:
    from app.models import User

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


def _publish_remark(teacher_context, db, student_id: int, body: str) -> int:
    term_id = _remark_term_id(db, student_id)
    created = teacher_context["client"].post(
        "/api/remarks",
        json={
            "student_id": student_id,
            "term_id": term_id,
            "body": body,
            "category": "academic",
            "publish": True,
        },
    )
    assert created.status_code == 200
    return created.json()["id"]


class TestParentRemarkNotices:
    def test_unread_count_after_publish_and_mark_read(
        self, teacher_context, parent_context, db
    ):
        student_id = _shared_student(teacher_context, parent_context, db)
        client = parent_context["client"]
        before = client.get("/api/parent/remark-notices/count").json()["count"]

        marker = "F07 unread count remark."
        remark_id = _publish_remark(teacher_context, db, student_id, marker)

        after_pub = client.get("/api/parent/remark-notices/count").json()
        assert after_pub["count"] == before + 1

        listed = client.get("/api/parent/remark-notices").json()
        assert any(i["id"] == remark_id and marker in i["body_preview"] for i in listed["items"])

        marked = client.post(
            "/api/parent/remark-notices/mark-read",
            json={"remark_ids": [remark_id]},
        )
        assert marked.status_code == 200
        assert marked.json()["marked"] == 1
        assert marked.json()["count"] == before

        row = db.scalars(
            select(GuardianRemarkRead).where(
                GuardianRemarkRead.remark_id == remark_id,
            )
        ).one()
        assert row.guardian_user_id is not None

    def test_draft_not_in_unread_list(self, teacher_context, parent_context, db):
        student_id = _shared_student(teacher_context, parent_context, db)
        term_id = _remark_term_id(db, student_id)
        marker = "F07 draft must not alert parent."
        teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": student_id,
                "term_id": term_id,
                "body": marker,
                "category": "academic",
            },
        )
        items = parent_context["client"].get("/api/parent/remark-notices").json()["items"]
        assert not any(marker in (i.get("body_preview") or "") for i in items)

    def test_other_child_remark_not_visible_to_parent(
        self, parent_context, db
    ):
        other_id = parent_context["other_student_id"]
        student = db.get(Student, other_id)
        assert student is not None
        marker = "F07 other family remark."
        remark = Remark(
            tenant_id=student.tenant_id,
            student_id=other_id,
            category=RemarkCategory.ACADEMIC,
            body=marker,
            visibility=RemarkVisibility.PUBLISHED,
        )
        db.add(remark)
        db.commit()
        db.refresh(remark)
        remark_id = remark.id

        items = parent_context["client"].get("/api/parent/remark-notices").json()["items"]
        assert not any(i["id"] == remark_id for i in items)

        denied = parent_context["client"].post(
            "/api/parent/remark-notices/mark-read",
            json={"remark_ids": [remark_id]},
        )
        assert denied.status_code == 200
        assert denied.json()["marked"] == 0
        assert (
            db.scalars(
                select(GuardianRemarkRead).where(
                    GuardianRemarkRead.remark_id == remark_id
                )
            ).first()
            is None
        )

    def test_teacher_and_student_cannot_access_notices_api(
        self, teacher_context, student_context, parent_context
    ):
        for client in (teacher_context["client"], student_context["client"]):
            assert client.get("/api/parent/remark-notices/count").status_code == 403
            assert client.get("/api/parent/remark-notices").status_code == 403
            assert (
                client.post(
                    "/api/parent/remark-notices/mark-read",
                    json={"mark_all": True},
                ).status_code
                == 403
            )

        page = parent_context["client"].get(
            f"/parent?student_id={parent_context['child_id']}"
        )
        assert page.status_code == 200
        assert "/static/js/parent_remark_notices.js" in page.text
        assert "parent-remark-notices" in page.text

    def test_mark_read_out_of_scope_id_ignored(
        self, teacher_context, parent_context, db
    ):
        student = db.get(Student, parent_context["other_student_id"])
        assert student is not None
        draft = Remark(
            tenant_id=student.tenant_id,
            student_id=student.id,
            category=RemarkCategory.ACADEMIC,
            body="Outside guardian",
            visibility=RemarkVisibility.PUBLISHED,
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)

        result = parent_context["client"].post(
            "/api/parent/remark-notices/mark-read",
            json={"remark_ids": [draft.id]},
        )
        assert result.json()["marked"] == 0
