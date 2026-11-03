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

## `action_tasks`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `intervention_case_id` | INTEGER | no | FK → intervention_cases.id |
| `title` | VARCHAR(200) | no | — |
| `description` | TEXT | yes | — |
| `assigned_to_user_id` | INTEGER | yes | FK → users.id |
| `created_by_user_id` | INTEGER | yes | FK → users.id |
| `visibility` | VARCHAR(13) | no | — |
| `status` | VARCHAR(11) | no | — |
| `priority` | VARCHAR(6) | no | — |
| `due_at` | DATETIME | yes | — |
| `completed_at` | DATETIME | yes | — |
| `completion_note` | TEXT | yes | — |
| `created_at` | DATETIME | no | — |
| `updated_at` | DATETIME | no | — |

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

## `import_conflicts`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `entity_type` | VARCHAR(10) | no | — |
| `entity_id` | INTEGER | no | — |
| `external_id` | VARCHAR(64) | yes | — |
| `source` | VARCHAR(40) | no | — |
| `local_values` | JSON | no | — |
| `incoming_values` | JSON | no | — |
| `field_names` | JSON | no | — |
| `status` | VARCHAR(10) | no | — |
| `resolution` | JSON | yes | — |
| `resolved_by_user_id` | INTEGER | yes | FK → users.id |
| `resolved_at` | DATETIME | yes | — |
| `created_at` | DATETIME | no | — |
| `updated_at` | DATETIME | no | — |

## `import_mapping_queue`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `source` | VARCHAR(40) | no | — |
| `external_key` | VARCHAR(64) | no | — |
| `payload_snippet` | JSON | yes | — |
| `status` | VARCHAR(8) | no | — |
| `mapped_assessment_id` | INTEGER | yes | FK → assessments.id |
| `resolved_by_user_id` | INTEGER | yes | FK → users.id |
| `resolved_at` | DATETIME | yes | — |
| `created_at` | DATETIME | no | — |
| `updated_at` | DATETIME | no | — |

## `import_runs`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `status` | VARCHAR(9) | no | — |
| `source` | VARCHAR(40) | no | — |
| `file_summary` | VARCHAR(255) | yes | — |
| `row_counts` | JSON | yes | — |
| `error_count` | INTEGER | no | — |
| `created_at` | DATETIME | no | — |
| `created_by_user_id` | INTEGER | yes | FK → users.id |

## `import_sync_state`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `source` | VARCHAR(40) | no | — |
| `bundle_fingerprint` | VARCHAR(64) | no | — |
| `updated_at` | DATETIME | no | — |

## `intervention_cases`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `student_id` | INTEGER | no | FK → students.id |
| `signal_source` | VARCHAR(80) | no | — |
| `reason_summary` | TEXT | no | — |
| `priority` | VARCHAR(6) | no | — |
| `status` | VARCHAR(11) | no | — |
| `owner_user_id` | INTEGER | yes | FK → users.id |
| `due_date` | DATE | yes | — |
| `notes_summary` | TEXT | yes | — |
| `created_at` | DATETIME | no | — |
| `resolved_at` | DATETIME | yes | — |
| `created_by_user_id` | INTEGER | yes | FK → users.id |

## `intervention_contacts`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `case_id` | INTEGER | no | FK → intervention_cases.id |
| `contact_type` | VARCHAR(9) | no | — |
| `notes` | TEXT | no | — |
| `contacted_at` | DATETIME | no | — |
| `created_by_user_id` | INTEGER | yes | FK → users.id |
| `created_at` | DATETIME | no | — |

## `intervention_events`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `intervention_case_id` | INTEGER | no | FK → intervention_cases.id |
| `event_type` | VARCHAR(20) | no | — |
| `actor_user_id` | INTEGER | yes | FK → users.id |
| `metadata` | JSON | yes | — |
| `created_at` | DATETIME | no | — |

## `parental_consent_records`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `student_id` | INTEGER | no | FK → students.id |
| `guardian_user_id` | INTEGER | yes | FK → users.id |
| `purpose` | VARCHAR(17) | no | — |
| `status` | VARCHAR(9) | no | — |
| `privacy_notice_version` | VARCHAR(32) | no | — |
| `dpa_reference` | VARCHAR(80) | yes | — |
| `notes` | TEXT | yes | — |
| `recorded_by_user_id` | INTEGER | yes | FK → users.id |
| `recorded_at` | DATETIME | no | — |

## `password_reset_tokens`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `user_id` | INTEGER | no | FK → users.id |
| `token_hash` | VARCHAR(64) | no | — |
| `expires_at` | DATETIME | no | — |
| `used_at` | DATETIME | yes | — |
| `created_at` | DATETIME | no | — |
| `created_by_user_id` | INTEGER | yes | FK → users.id |

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
| `external_id` | VARCHAR(64) | yes | — |
| `source_system` | VARCHAR(40) | yes | — |
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

## `security_audit_events`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `actor_user_id` | INTEGER | yes | FK → users.id |
| `action` | VARCHAR(64) | no | — |
| `target_type` | VARCHAR(64) | no | — |
| `target_id` | VARCHAR(64) | no | — |
| `metadata` | JSON | yes | — |
| `correlation_id` | VARCHAR(128) | yes | — |
| `created_at` | DATETIME | no | — |

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

## `user_mfa_recovery_codes`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | INTEGER | no | PK |
| `tenant_id` | CHAR(32) | no | FK → tenants.id |
| `user_id` | INTEGER | no | FK → users.id |
| `code_hash` | VARCHAR(64) | no | — |
| `used_at` | DATETIME | yes | — |
| `created_at` | DATETIME | no | — |

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
| `totp_secret_enc` | VARCHAR(512) | yes | — |
| `totp_pending_secret_enc` | VARCHAR(512) | yes | — |
| `mfa_enrolled_at` | DATETIME | yes | — |
| `session_version` | INTEGER | no | — |
| `must_change_password` | BOOLEAN | no | — |
| `created_at` | DATETIME | no | — |
| `last_login_at` | DATETIME | yes | — |
