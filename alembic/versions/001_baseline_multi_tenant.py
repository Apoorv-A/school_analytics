"""Baseline multi-tenant schema (immutable explicit DDL).

Greenfield PostgreSQL must run ``alembic upgrade head`` before app traffic.
Revision 001 creates core tenant-scoped tables; 002 applies RLS; 003+ add
incremental deltas. Local SQLite dev may still bootstrap via ``init_db()`` when
``startup_ddl_enabled()`` is true.

This file is generated once and must not import live ORM metadata — regenerate
with ``PYTHONPATH=. python3 scripts/generate_baseline_001.py`` only when
deliberately re-baselining a greenfield install (never after prod deploy).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("settings_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tenants_key", "tenants", ["key"])

    op.create_table(
        "schools",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("city", sa.String(120)),
        sa.Column("board", sa.String(60)),
    )
    op.create_index("ix_schools_tenant_id", "schools", ["tenant_id"])

    op.create_table(
        "tenant_domains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("hostname", name="uq_tenant_domain_hostname"),
    )
    op.create_index("ix_tenant_domains_tenant_id", "tenant_domains", ["tenant_id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("role", sa.String(7), nullable=False),
        sa.Column("phone", sa.String(32)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "email", name="uq_user_email_per_tenant"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    op.create_table(
        "academic_years",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("school_id", "label", name="uq_year_per_school"),
    )
    op.create_index("ix_academic_years_tenant_id", "academic_years", ["tenant_id"])

    op.create_table(
        "grades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.UniqueConstraint("school_id", "level", name="uq_grade_level"),
    )
    op.create_index("ix_grades_tenant_id", "grades", ["tenant_id"])

    op.create_table(
        "subjects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.UniqueConstraint("school_id", "code", name="uq_subject_code"),
    )
    op.create_index("ix_subjects_tenant_id", "subjects", ["tenant_id"])

    op.create_table(
        "teachers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_code", sa.String(32), nullable=False),
        sa.Column("department", sa.String(80)),
        sa.Column("joined_on", sa.Date()),
        sa.UniqueConstraint("tenant_id", "employee_code", name="uq_teacher_code_per_tenant"),
    )
    op.create_index("ix_teachers_tenant_id", "teachers", ["tenant_id"])

    op.create_table(
        "sections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grade_id", sa.Integer(), sa.ForeignKey("grades.id", ondelete="CASCADE"), nullable=False),
        sa.Column("academic_year_id", sa.Integer(), sa.ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(10), nullable=False),
        sa.Column("room", sa.String(40)),
        sa.Column("class_teacher_id", sa.Integer(), sa.ForeignKey("teachers.id", ondelete="SET NULL")),
        sa.UniqueConstraint("grade_id", "name", "academic_year_id", name="uq_section_per_year"),
    )
    op.create_index("ix_sections_tenant_id", "sections", ["tenant_id"])

    op.create_table(
        "terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("academic_year_id", sa.Integer(), sa.ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.UniqueConstraint("academic_year_id", "sequence", name="uq_term_sequence"),
    )
    op.create_index("ix_terms_tenant_id", "terms", ["tenant_id"])

    op.create_table(
        "assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("assessment_type", sa.String(10), nullable=False),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", sa.Integer(), sa.ForeignKey("sections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("max_marks", sa.Float(), nullable=False),
        sa.Column("weightage", sa.Float(), nullable=False),
        sa.Column("conducted_on", sa.Date(), nullable=False),
        sa.Column("created_by_teacher_id", sa.Integer(), sa.ForeignKey("teachers.id", ondelete="SET NULL")),
        sa.CheckConstraint("max_marks > 0", name="ck_assessment_max_marks_positive"),
    )
    op.create_index("ix_assessment_section_subject", "assessments", ["section_id", "subject_id"])
    op.create_index("ix_assessments_conducted_on", "assessments", ["conducted_on"])
    op.create_index("ix_assessments_tenant_id", "assessments", ["tenant_id"])
    op.create_index("ix_assessments_term_id", "assessments", ["term_id"])

    op.create_table(
        "students",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("guardian_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("section_id", sa.Integer(), sa.ForeignKey("sections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("admission_no", sa.String(32), nullable=False),
        sa.Column("roll_no", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("date_of_birth", sa.Date()),
        sa.Column("gender", sa.String(20)),
        sa.Column("enrolled_on", sa.Date()),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "admission_no", name="uq_admission_per_tenant"),
    )
    op.create_index("ix_students_guardian_user_id", "students", ["guardian_user_id"])
    op.create_index("ix_students_section_id", "students", ["section_id"])
    op.create_index("ix_students_tenant_id", "students", ["tenant_id"])

    op.create_table(
        "teacher_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("teacher_id", sa.Integer(), sa.ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", sa.Integer(), sa.ForeignKey("sections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("academic_year_id", sa.Integer(), sa.ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("subject_id", "section_id", "academic_year_id", name="uq_subject_section_year"),
    )
    op.create_index("ix_teacher_assignments_teacher_id", "teacher_assignments", ["teacher_id"])
    op.create_index("ix_teacher_assignments_tenant_id", "teacher_assignments", ["tenant_id"])

    op.create_table(
        "attendance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="SET NULL")),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(7), nullable=False),
        sa.UniqueConstraint("student_id", "on_date", name="uq_attendance_per_day"),
    )
    op.create_index("ix_attendance_on_date", "attendance", ["on_date"])
    op.create_index("ix_attendance_student_id", "attendance", ["student_id"])
    op.create_index("ix_attendance_tenant_id", "attendance", ["tenant_id"])
    op.create_index("ix_attendance_term_id", "attendance", ["term_id"])

    op.create_table(
        "remarks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("teacher_id", sa.Integer(), sa.ForeignKey("teachers.id", ondelete="SET NULL")),
        sa.Column("term_id", sa.Integer(), sa.ForeignKey("terms.id", ondelete="SET NULL")),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("subjects.id", ondelete="SET NULL")),
        sa.Column("category", sa.String(11), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_remarks_student_id", "remarks", ["student_id"])
    op.create_index("ix_remarks_tenant_id", "remarks", ["tenant_id"])

    op.create_table(
        "scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marks_obtained", sa.Float()),
        sa.Column("is_absent", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(255)),
        sa.UniqueConstraint("assessment_id", "student_id", name="uq_score_per_student"),
        sa.CheckConstraint("marks_obtained IS NULL OR marks_obtained >= 0", name="ck_score_non_negative"),
    )
    op.create_index("ix_scores_assessment_id", "scores", ["assessment_id"])
    op.create_index("ix_scores_student_id", "scores", ["student_id"])
    op.create_index("ix_scores_tenant_id", "scores", ["tenant_id"])



def downgrade() -> None:
    op.drop_table("scores")
    op.drop_table("remarks")
    op.drop_table("attendance")
    op.drop_table("teacher_assignments")
    op.drop_table("students")
    op.drop_table("assessments")
    op.drop_table("terms")
    op.drop_table("sections")
    op.drop_table("teachers")
    op.drop_table("subjects")
    op.drop_table("grades")
    op.drop_table("academic_years")
    op.drop_table("users")
    op.drop_table("tenant_domains")
    op.drop_table("schools")
    op.drop_table("tenants")
