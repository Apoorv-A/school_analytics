"""Generate a realistic synthetic school so every dashboard has data to show.

Run with:  python -m seed.generate  [--reset]

Each student is given a latent ability plus a per-subject aptitude and a term-by-term
trajectory (steady, improving, declining, or struggling), so trend lines, at-risk flags
and consistency metrics all reflect patterns a real school would see rather than noise.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine, init_db
from app.models import (
    AcademicYear,
    Assessment,
    AssessmentType,
    Attendance,
    AttendanceStatus,
    Grade,
    Remark,
    RemarkCategory,
    Role,
    School,
    Score,
    Section,
    Student,
    Subject,
    Teacher,
    TeacherAssignment,
    Term,
    User,
)
from app.security import hash_password

RANDOM_SEED = 20240615

# Shared password for the synthetic demo accounts this script creates. It exists only
# so a fresh local database is immediately explorable; no real account uses it.
DEMO_PASSWORD = "Demo@12345"  # noqa: S105

SCHOOL_NAME = "Sunrise Public School"
SCHOOL_CITY = "Pune"
SCHOOL_BOARD = "CBSE"

YEAR_LABEL = "2025-26"
YEAR_START = date(2025, 6, 9)
YEAR_END = date(2026, 3, 27)

TERMS = [
    ("Term 1", 1, date(2025, 6, 9), date(2025, 9, 26)),
    ("Term 2", 2, date(2025, 9, 29), date(2025, 12, 19)),
    ("Term 3", 3, date(2026, 1, 5), date(2026, 3, 27)),
]

SUBJECTS = [
    ("English", "ENG", "Languages"),
    ("Hindi", "HIN", "Languages"),
    ("Mathematics", "MATH", "Mathematics"),
    ("Science", "SCI", "Science"),
    ("Social Studies", "SST", "Humanities"),
    ("Computer Science", "CS", "Science"),
    ("Art & Design", "ART", "Arts"),
    ("Physical Education", "PE", "Sports"),
]

GRADE_LEVELS = [6, 7, 8, 9, 10]
SECTION_NAMES = ["A", "B"]
STUDENTS_PER_SECTION = 15

# Assessments generated per subject per term: (type, max marks, weight in the term).
TERM_ASSESSMENT_PLAN = [
    (AssessmentType.UNIT_TEST, 25.0, 0.15),
    (AssessmentType.ASSIGNMENT, 20.0, 0.10),
    (AssessmentType.UNIT_TEST, 25.0, 0.15),
    (AssessmentType.PROJECT, 30.0, 0.15),
    (AssessmentType.MID_TERM, 80.0, 0.45),
]
FINAL_TERM_PLAN = [
    (AssessmentType.UNIT_TEST, 25.0, 0.15),
    (AssessmentType.ASSIGNMENT, 20.0, 0.10),
    (AssessmentType.PROJECT, 30.0, 0.15),
    (AssessmentType.FINAL, 100.0, 0.60),
]

FIRST_NAMES_M = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Reyansh", "Kabir", "Ishaan",
    "Rudra", "Aryan", "Shaurya", "Advik", "Dhruv", "Kian", "Neel", "Yuvan",
    "Rohan", "Samar", "Veer", "Aayush", "Nikhil", "Parth", "Tanmay", "Om",
]
FIRST_NAMES_F = [
    "Aadhya", "Ananya", "Diya", "Ira", "Myra", "Sara", "Anika", "Navya",
    "Kiara", "Prisha", "Riya", "Saanvi", "Aarohi", "Meera", "Tara", "Ishita",
    "Nitya", "Vanya", "Zara", "Avni", "Trisha", "Siya", "Mahi", "Pari",
]
LAST_NAMES = [
    "Agrawal", "Sharma", "Verma", "Iyer", "Nair", "Reddy", "Patel", "Joshi",
    "Mehta", "Kulkarni", "Deshpande", "Rao", "Banerjee", "Chatterjee", "Kapoor",
    "Malhotra", "Gupta", "Bhat", "Shetty", "Pillai", "Sinha", "Chauhan", "Bose",
    "Ghosh", "Menon", "Saxena", "Trivedi", "Naidu", "Dubey", "Mishra",
]
TEACHER_NAMES = [
    ("Meenakshi Iyer", "Languages"),
    ("Rajesh Kulkarni", "Mathematics"),
    ("Sunita Deshpande", "Science"),
    ("Anil Verma", "Humanities"),
    ("Farah Khan", "Languages"),
    ("Prakash Rao", "Mathematics"),
    ("Deepa Nair", "Science"),
    ("Vikram Singh", "Sports"),
    ("Neha Joshi", "Arts"),
    ("Sameer Bose", "Science"),
    ("Lakshmi Menon", "Humanities"),
    ("Arun Pillai", "Mathematics"),
]

# Trajectory: (label, per-term drift in percentage points, weight in the population)
TRAJECTORIES = [
    ("steady", 0.0, 46),
    ("improving", 6.5, 20),
    ("strong_improving", 11.0, 8),
    ("declining", -6.0, 14),
    ("sharp_decline", -11.5, 6),
    ("struggling", -1.5, 6),
]


@dataclass
class StudentProfile:
    """Latent traits that drive one student's generated marks."""

    ability: float
    consistency: float
    trajectory: str
    drift: float
    subject_aptitude: dict[str, float]
    attendance_rate: float

    def expected_percentage(self, subject_code: str, term_index: int) -> float:
        base = self.ability + self.subject_aptitude.get(subject_code, 0.0)
        return base + self.drift * term_index


def _weighted_trajectory(rng: random.Random) -> tuple[str, float]:
    labels = [t[0] for t in TRAJECTORIES]
    weights = [t[2] for t in TRAJECTORIES]
    chosen = rng.choices(labels, weights=weights, k=1)[0]
    drift = next(t[1] for t in TRAJECTORIES if t[0] == chosen)
    return chosen, drift


def _build_profile(rng: random.Random, subject_codes: list[str]) -> StudentProfile:
    trajectory, drift = _weighted_trajectory(rng)
    if trajectory == "struggling":
        ability = rng.uniform(28.0, 43.0)
    else:
        # Centred a little above the midpoint, as a real cohort tends to be.
        ability = min(96.0, max(30.0, rng.gauss(66.0, 13.0)))
    aptitude = {}
    for code in subject_codes:
        aptitude[code] = rng.gauss(0.0, 7.5)
    # Give each student a clear strength and a clear weakness.
    strong, weak = rng.sample(subject_codes, 2)
    aptitude[strong] += rng.uniform(6.0, 13.0)
    aptitude[weak] -= rng.uniform(6.0, 14.0)

    attendance_rate = rng.uniform(0.62, 0.78) if trajectory in {
        "struggling", "sharp_decline"
    } else rng.uniform(0.86, 0.99)

    return StudentProfile(
        ability=ability,
        consistency=rng.uniform(4.0, 11.0),
        trajectory=trajectory,
        drift=drift,
        subject_aptitude=aptitude,
        attendance_rate=attendance_rate,
    )


def _slugify_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum() or ch == " ").replace(" ", ".")


def _spread_dates(rng: random.Random, start: date, end: date, count: int) -> list[date]:
    """Pick `count` weekday dates spread evenly across the window."""
    span = max((end - start).days, count)
    step = span / (count + 1)
    dates: list[date] = []
    for index in range(1, count + 1):
        offset = int(step * index) + rng.randint(-2, 2)
        offset = max(1, min(span - 1, offset))
        day = start + timedelta(days=offset)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        dates.append(min(day, end))
    return sorted(dates)


def _school_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def reset_database() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _wipe_rows(db: Session) -> None:
    for model in (
        Remark, Attendance, Score, Assessment, TeacherAssignment,
        Student, Section, Grade, Subject, Teacher, Term, AcademicYear, School, User,
    ):
        db.execute(delete(model))
    db.commit()


def generate(reset: bool = False) -> dict[str, object]:
    rng = random.Random(RANDOM_SEED)
    init_db()

    with SessionLocal() as db:
        if reset:
            _wipe_rows(db)
        elif db.scalars(select(School).limit(1)).first() is not None:
            raise SystemExit(
                "Database already contains data. Re-run with --reset to rebuild it."
            )

        password_hash = hash_password(DEMO_PASSWORD)

        school = School(name=SCHOOL_NAME, city=SCHOOL_CITY, board=SCHOOL_BOARD)
        db.add(school)
        db.flush()

        year = AcademicYear(
            school_id=school.id,
            label=YEAR_LABEL,
            start_date=YEAR_START,
            end_date=YEAR_END,
            is_current=True,
        )
        db.add(year)
        db.flush()

        terms = []
        for name, sequence, start, end in TERMS:
            term = Term(
                academic_year_id=year.id,
                name=name,
                sequence=sequence,
                start_date=start,
                end_date=end,
            )
            db.add(term)
            terms.append(term)
        db.flush()

        subjects: dict[str, Subject] = {}
        for name, code, _department in SUBJECTS:
            subject = Subject(school_id=school.id, name=name, code=code)
            db.add(subject)
            subjects[code] = subject
        db.flush()
        subject_codes = list(subjects)

        principal = User(
            email="principal@sunrise.edu",
            password_hash=password_hash,
            full_name="Dr. Kavita Menon",
            role=Role.ADMIN,
            phone="+91-20-4000-1000",
        )
        db.add(principal)

        teachers: list[Teacher] = []
        for index, (name, department) in enumerate(TEACHER_NAMES, start=1):
            user = User(
                email=f"{_slugify_name(name)}@sunrise.edu",
                password_hash=password_hash,
                full_name=name,
                role=Role.TEACHER,
                phone=f"+91-98{rng.randint(10000000, 99999999)}",
            )
            db.add(user)
            db.flush()
            teacher = Teacher(
                user_id=user.id,
                employee_code=f"EMP{index:03d}",
                department=department,
                joined_on=date(2025 - rng.randint(1, 12), rng.randint(1, 12), rng.randint(1, 28)),
            )
            db.add(teacher)
            teachers.append(teacher)
        db.flush()

        grades: list[Grade] = []
        for level in GRADE_LEVELS:
            grade = Grade(school_id=school.id, name=f"Grade {level}", level=level)
            db.add(grade)
            grades.append(grade)
        db.flush()

        sections: list[Section] = []
        homeroom_pool = list(teachers)
        rng.shuffle(homeroom_pool)
        homeroom_index = 0
        for grade in grades:
            for section_name in SECTION_NAMES:
                class_teacher = homeroom_pool[homeroom_index % len(homeroom_pool)]
                homeroom_index += 1
                section = Section(
                    grade_id=grade.id,
                    academic_year_id=year.id,
                    name=section_name,
                    room=f"{grade.level}{section_name}-{rng.randint(101, 320)}",
                    class_teacher_id=class_teacher.id,
                )
                db.add(section)
                sections.append(section)
        db.flush()

        # Spread subject teaching across teachers whose department matches where possible.
        by_department: dict[str, list[Teacher]] = {}
        for teacher in teachers:
            by_department.setdefault(teacher.department or "General", []).append(teacher)

        subject_department = {code: dept for _name, code, dept in SUBJECTS}
        for section in sections:
            for code, subject in subjects.items():
                pool = by_department.get(subject_department[code]) or teachers
                teacher = pool[rng.randrange(len(pool))]
                db.add(
                    TeacherAssignment(
                        teacher_id=teacher.id,
                        subject_id=subject.id,
                        section_id=section.id,
                        academic_year_id=year.id,
                    )
                )
        db.flush()

        students: list[Student] = []
        profiles: dict[int, StudentProfile] = {}
        used_emails: set[str] = set()
        admission_counter = 1

        for section in sections:
            for roll_no in range(1, STUDENTS_PER_SECTION + 1):
                is_female = rng.random() < 0.5
                first = rng.choice(FIRST_NAMES_F if is_female else FIRST_NAMES_M)
                last = rng.choice(LAST_NAMES)
                full_name = f"{first} {last}"

                base_email = f"{first.lower()}.{last.lower()}"
                email = f"{base_email}@student.sunrise.edu"
                suffix = 1
                while email in used_emails:
                    suffix += 1
                    email = f"{base_email}{suffix}@student.sunrise.edu"
                used_emails.add(email)

                student_user = User(
                    email=email,
                    password_hash=password_hash,
                    full_name=full_name,
                    role=Role.STUDENT,
                )
                db.add(student_user)

                guardian_email = email.replace("@student.", "@parent.")
                guardian_user = User(
                    email=guardian_email,
                    password_hash=password_hash,
                    full_name=f"{rng.choice(FIRST_NAMES_M + FIRST_NAMES_F)} {last}",
                    role=Role.PARENT,
                    phone=f"+91-99{rng.randint(10000000, 99999999)}",
                )
                db.add(guardian_user)
                db.flush()

                grade_level = next(g.level for g in grades if g.id == section.grade_id)
                student = Student(
                    user_id=student_user.id,
                    guardian_user_id=guardian_user.id,
                    section_id=section.id,
                    admission_no=f"SPS{2025}{admission_counter:04d}",
                    roll_no=roll_no,
                    full_name=full_name,
                    date_of_birth=date(
                        2025 - grade_level - 5, rng.randint(1, 12), rng.randint(1, 28)
                    ),
                    gender="Female" if is_female else "Male",
                    enrolled_on=YEAR_START,
                )
                db.add(student)
                admission_counter += 1
                students.append(student)
        db.flush()

        for student in students:
            profiles[student.id] = _build_profile(rng, subject_codes)

        students_by_section: dict[int, list[Student]] = {}
        for student in students:
            students_by_section.setdefault(student.section_id, []).append(student)

        assignment_lookup = {
            (row.section_id, row.subject_id): row.teacher_id
            for row in db.scalars(select(TeacherAssignment))
        }

        assessment_count = 0
        score_count = 0

        for section in sections:
            section_students = students_by_section[section.id]
            for code, subject in subjects.items():
                for term_index, term in enumerate(terms):
                    plan = (
                        FINAL_TERM_PLAN
                        if term.sequence == len(TERMS)
                        else TERM_ASSESSMENT_PLAN
                    )
                    dates = _spread_dates(
                        rng, term.start_date, term.end_date, len(plan)
                    )
                    for slot, (kind, max_marks, weight) in enumerate(plan):
                        # Each paper has its own difficulty, which shifts the whole class.
                        difficulty_shift = rng.gauss(0.0, 4.5)
                        assessment = Assessment(
                            name=f"{subject.name} {kind.label} {slot + 1} ({term.name})",
                            assessment_type=kind,
                            subject_id=subject.id,
                            section_id=section.id,
                            term_id=term.id,
                            max_marks=max_marks,
                            weightage=weight,
                            conducted_on=dates[slot],
                            created_by_teacher_id=assignment_lookup.get(
                                (section.id, subject.id)
                            ),
                        )
                        db.add(assessment)
                        db.flush()
                        assessment_count += 1

                        for student in section_students:
                            profile = profiles[student.id]
                            expected = profile.expected_percentage(code, term_index)
                            noise = rng.gauss(0.0, profile.consistency)
                            pct = expected + noise + difficulty_shift
                            # Coursework tends to score a little higher than exams.
                            if kind in (AssessmentType.ASSIGNMENT, AssessmentType.PROJECT):
                                pct += 6.0
                            if kind is AssessmentType.FINAL:
                                pct -= 2.0
                            pct = max(4.0, min(100.0, pct))

                            is_absent = rng.random() > profile.attendance_rate * 0.995
                            marks = None if is_absent else round(max_marks * pct / 100, 1)
                            db.add(
                                Score(
                                    assessment_id=assessment.id,
                                    student_id=student.id,
                                    marks_obtained=marks,
                                    is_absent=is_absent,
                                )
                            )
                            score_count += 1
            db.commit()

        # Attendance: sampled school days keep the dataset meaningful but compact.
        attendance_rows = 0
        for term_index, term in enumerate(terms):
            all_days = _school_days(term.start_date, term.end_date)
            sampled = all_days[::2]
            for student in students:
                profile = profiles[student.id]
                rate = profile.attendance_rate - 0.02 * term_index
                for day in sampled:
                    draw = rng.random()
                    if draw < rate:
                        status = (
                            AttendanceStatus.LATE
                            if rng.random() < 0.06
                            else AttendanceStatus.PRESENT
                        )
                    elif draw < rate + (1 - rate) * 0.25:
                        status = AttendanceStatus.EXCUSED
                    else:
                        status = AttendanceStatus.ABSENT
                    db.add(
                        Attendance(
                            student_id=student.id,
                            term_id=term.id,
                            on_date=day,
                            status=status,
                        )
                    )
                    attendance_rows += 1
            db.commit()

        remark_templates = {
            RemarkCategory.ACADEMIC: [
                "Grasps new concepts quickly and asks thoughtful questions in class.",
                "Written work is accurate but needs to show intermediate steps.",
                "Reading comprehension has improved noticeably this term.",
            ],
            RemarkCategory.BEHAVIOUR: [
                "Polite, cooperative, and a steadying presence in group work.",
                "Occasionally distracted during the last period; responds well to reminders.",
                "Volunteers readily for classroom responsibilities.",
            ],
            RemarkCategory.ACHIEVEMENT: [
                "Placed among the top three in the inter-house quiz.",
                "Presented an excellent term project with original research.",
                "Represented the school at the district level sports meet.",
            ],
            RemarkCategory.CONCERN: [
                "Frequent absences are beginning to affect continuity in class.",
                "Submissions have been late in the past few weeks; needs a study plan.",
                "Scores have slipped since the previous term; extra practice recommended.",
            ],
        }

        remark_count = 0
        for student in students:
            profile = profiles[student.id]
            for term in terms:
                if rng.random() < 0.55:
                    if profile.trajectory in {"struggling", "sharp_decline", "declining"}:
                        category = rng.choices(
                            list(remark_templates),
                            weights=[3, 2, 1, 6],
                            k=1,
                        )[0]
                    else:
                        category = rng.choices(
                            list(remark_templates),
                            weights=[5, 4, 4, 1],
                            k=1,
                        )[0]
                    teacher_id = db.scalar(
                        select(Section.class_teacher_id).where(
                            Section.id == student.section_id
                        )
                    )
                    db.add(
                        Remark(
                            student_id=student.id,
                            teacher_id=teacher_id,
                            term_id=term.id,
                            category=category,
                            body=rng.choice(remark_templates[category]),
                        )
                    )
                    remark_count += 1
        db.commit()

        # Pick a demo family from a grade with a visible upward trend.
        demo_student = None
        for student in students:
            if profiles[student.id].trajectory in {"improving", "strong_improving"}:
                demo_student = student
                break
        demo_student = demo_student or students[0]
        demo_guardian = db.get(User, demo_student.guardian_user_id)
        demo_student_user = db.get(User, demo_student.user_id)
        demo_teacher_section = db.get(Section, demo_student.section_id)
        demo_teacher = db.get(Teacher, demo_teacher_section.class_teacher_id)
        demo_teacher_user = db.get(User, demo_teacher.user_id) if demo_teacher else None

        # A second child for the demo parent, so the child switcher is exercised.
        sibling = next(
            (
                s
                for s in students
                if s.id != demo_student.id
                and s.section_id != demo_student.section_id
            ),
            None,
        )
        if sibling is not None:
            sibling.guardian_user_id = demo_guardian.id
            family_name = demo_student.full_name.split()[-1]
            sibling.full_name = f"{sibling.full_name.split()[0]} {family_name}"
            db.commit()

        summary = {
            "school": SCHOOL_NAME,
            "academic_year": YEAR_LABEL,
            "students": len(students),
            "teachers": len(teachers),
            "sections": len(sections),
            "subjects": len(subjects),
            "assessments": assessment_count,
            "scores": score_count,
            "attendance_rows": attendance_rows,
            "remarks": remark_count,
            "logins": {
                "principal": principal.email,
                "teacher": demo_teacher_user.email if demo_teacher_user else "n/a",
                "parent": demo_guardian.email if demo_guardian else "n/a",
                "student": demo_student_user.email if demo_student_user else "n/a",
            },
            "demo_child": demo_student.full_name,
        }
        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the school analytics database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing rows before seeding.",
    )
    args = parser.parse_args()

    summary = generate(reset=args.reset)

    logins = summary.pop("logins")
    print("\nSeed complete\n" + "-" * 52)
    for key, value in summary.items():
        print(f"{key.replace('_', ' ').title():<20} {value}")

    print("\nDemo logins (password for all accounts: " + DEMO_PASSWORD + ")")
    print("-" * 52)
    for role, email in logins.items():
        print(f"{role.title():<12} {email}")
    print("\nStart the app with:  uvicorn app.main:app --reload\n")


if __name__ == "__main__":
    main()
