# School intake checklist

Ordered steps to onboard a new school tenant.

**Local dev setup:** If you have an existing SQLite database, run `alembic upgrade head` (or start the app once — it auto-migrates on startup). For a fresh demo dataset, run `python -m seed.generate --reset`.

1. Copy `school-analytics-config/tenants/_template/dev.yaml` to `tenants/<school-key>/dev.yaml` and fill tenant, school, academics, and image tag.
2. Set `features.bulkImport: true` if the principal will upload CSV bundles from the admin portal.
3. Prepare CSV files using headers in `docs/onboarding/csv-templates/` (structure first, then users, scores, attendance, remarks).
4. Run `PYTHONPATH=. python -m tenant_operator validate --config=../school-analytics-config/tenants/<school>/dev.yaml`.
5. Deploy or run `tenant_operator apply` to reconcile the tenant registry and school shell.
6. Set `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` in the operator environment, then re-run apply to create the first principal login.
7. Validate CSVs: `PYTHONPATH=. python -m app.import_.cli validate --dir=./bundle`.
8. Apply data (replace the UUID with your tenant id from the `tenants` table; do not use angle brackets — zsh treats `<>` as globs):

   `PYTHONPATH=. python -m app.import_.cli apply --dir=./bundle --tenant-id=11111111-2222-3333-4444-555555555555`

   The apply output includes `created_users` with one temporary password per new account from `users.csv`. Save these credentials securely; they are not stored in `import_runs` and are not returned by the ERP webhook path.
9. Sign in as bootstrap admin, search for a student, and open the insights report to confirm charts populate.
