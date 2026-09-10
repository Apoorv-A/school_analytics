# Column Dictionary

Generated from SQLAlchemy models. Do not edit by hand.

## `academic_years`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `school_id` | INTEGER | no | FK → schools.id |
| `label` | VARCHAR(20) | no | — |
| `start_date` | DATE | no | — |
| `end_date` | DATE | no | — |
| `is_current` | BOOLEAN | no | — |

## `assessments`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `name` | VARCHAR(160) | no | — |
| `assessment_type` | VARCHAR(10) | no | — |
| `subject_id` | INTEGER | no | FK → subjects.id |
| `section_id` | INTEGER | no | FK → sections.id |
| `term_id` | INTEGER | no | FK → terms.id |
| `max_marks` | FLOAT | no | — |
| `weightage` | FLOAT | no | — |
| `conducted_on` | DATE | no | — |
| `external_id` | VARCHAR(64) | yes | — |
| `source_system` | VARCHAR(40) | yes | — |
| `created_by_teacher_id` | INTEGER | yes | FK → teachers.id |

## `attendance`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `student_id` | INTEGER | no | FK → students.id |
| `term_id` | INTEGER | yes | FK → terms.id |
| `on_date` | DATE | no | — |
| `status` | VARCHAR(7) | no | — |
| `external_id` | VARCHAR(64) | yes | — |
| `source_system` | VARCHAR(40) | yes | — |

## `grades`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `school_id` | INTEGER | no | FK → schools.id |
| `name` | VARCHAR(60) | no | — |
| `level` | INTEGER | no | — |

## `remarks`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `student_id` | INTEGER | no | FK → students.id |
| `teacher_id` | INTEGER | yes | FK → teachers.id |
| `term_id` | INTEGER | yes | FK → terms.id |
| `subject_id` | INTEGER | yes | FK → subjects.id |
| `category` | VARCHAR(11) | no | — |
| `body` | TEXT | no | — |
| `created_at` | DATETIME | no | — |

## `schools`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `name` | VARCHAR(200) | no | — |
| `city` | VARCHAR(120) | yes | — |
| `board` | VARCHAR(60) | yes | — |

## `scores`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `assessment_id` | INTEGER | no | FK → assessments.id |
| `student_id` | INTEGER | no | FK → students.id |
| `marks_obtained` | FLOAT | yes | — |
| `is_absent` | BOOLEAN | no | — |
| `external_id` | VARCHAR(64) | yes | — |
| `source_system` | VARCHAR(40) | yes | — |
| `note` | VARCHAR(255) | yes | — |

## `sections`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `grade_id` | INTEGER | no | FK → grades.id |
| `academic_year_id` | INTEGER | no | FK → academic_years.id |
| `name` | VARCHAR(10) | no | — |
| `room` | VARCHAR(40) | yes | — |
| `class_teacher_id` | INTEGER | yes | FK → teachers.id |

## `students`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `user_id` | INTEGER | yes | FK → users.id; unique |
| `guardian_user_id` | INTEGER | yes | FK → users.id |
| `section_id` | INTEGER | no | FK → sections.id |
| `admission_no` | VARCHAR(32) | no | — |
| `external_id` | VARCHAR(64) | yes | — |
| `source_system` | VARCHAR(40) | yes | — |
| `roll_no` | INTEGER | no | — |
| `full_name` | VARCHAR(160) | no | — |
| `date_of_birth` | DATE | yes | — |
| `gender` | VARCHAR(20) | yes | — |
| `enrolled_on` | DATE | yes | — |
| `is_active` | BOOLEAN | no | — |

## `subjects`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `school_id` | INTEGER | no | FK → schools.id |
| `name` | VARCHAR(80) | no | — |
| `code` | VARCHAR(16) | no | — |

## `teacher_assignments`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `teacher_id` | INTEGER | no | FK → teachers.id |
| `subject_id` | INTEGER | no | FK → subjects.id |
| `section_id` | INTEGER | no | FK → sections.id |
| `academic_year_id` | INTEGER | no | FK → academic_years.id |

## `teachers`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `user_id` | INTEGER | no | FK → users.id; unique |
| `employee_code` | VARCHAR(32) | no | — |
| `department` | VARCHAR(80) | yes | — |
| `joined_on` | DATE | yes | — |

## `tenant_domains`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `hostname` | VARCHAR(255) | no | — |
| `is_primary` | BOOLEAN | no | — |

## `tenants`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | CHAR(32) | no | PK |
| `key` | VARCHAR(40) | no | unique |
| `display_name` | VARCHAR(160) | no | — |
| `status` | VARCHAR(20) | no | — |
| `settings_json` | JSON | yes | — |
| `created_at` | DATETIME | no | — |

## `terms`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `academic_year_id` | INTEGER | no | FK → academic_years.id |
| `name` | VARCHAR(60) | no | — |
| `sequence` | INTEGER | no | — |
| `start_date` | DATE | no | — |
| `end_date` | DATE | no | — |

## `users`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `email` | VARCHAR(255) | no | — |
| `password_hash` | VARCHAR(255) | no | — |
| `external_id` | VARCHAR(64) | yes | — |
| `source_system` | VARCHAR(40) | yes | — |
| `full_name` | VARCHAR(160) | no | — |
| `role` | VARCHAR(7) | no | — |
| `phone` | VARCHAR(32) | yes | — |
| `is_active` | BOOLEAN | no | — |
| `created_at` | DATETIME | no | — |
| `last_login_at` | DATETIME | yes | — |
