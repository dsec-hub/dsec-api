# Contributing to dsec-api

The FastAPI backend behind **api.dsec.club**. It owns the database schema for
the whole club, so the rules here are stricter than a normal web app — a mistake
in this repo can take down every front-end at once.

## Ground rules

1. **No secrets in the repo.** No `.env`, connection strings, or tokens in a
   commit — ever.
2. **`dsec-api` owns the shared schema.** The tables every app reads — people,
   events, projects, sponsors, and the rest of the core — are defined by
   `app/models.py` and changed only through hand-written **Alembic** migrations.
   No other repo alters a shared table or a column another app depends on. (Apps
   may still create their *own* app-owned tables through idempotent `scripts/` —
   that's expected; touching the shared core is not.)
3. **Never `alembic revision --autogenerate` against a real database.** Autogen
   does not know about the tables other apps own and will emit `DROP`s for them.
   Write migrations by hand and review the SQL.
4. **Never run migrations or app code against live Neon from your machine.** No
   `python -c` or ad-hoc script that imports `app.db` / `app.models` outside
   `pytest` — those bind the real database, and a stray `drop_all` / `create_all`
   is catastrophic. Schema changes reach production only through the deploy.
5. **No member PII in logs.** Never log submitted names, student IDs, or emails.

## How to contribute

1. Branch from `main`: `git checkout -b feat/<short-name>`.
2. Make the change. Run the tests:
   ```bash
   pytest
   ```
3. Open a PR against `main` and fill in the template.
4. A code owner reviews. `app/models.py`, `alembic/**`, and the auth files are
   **maintainer-only** — see [CODEOWNERS](.github/CODEOWNERS).

Local dev runs on **SQLite**, and ~23 migrations are Postgres-only, so the dev
schema is built from the models and stamped `head` rather than replaying the
chain. Don't "fix" that by replaying migrations on SQLite — it crashes on
`ALTER COLUMN … SET DEFAULT`.

## Changing the schema

This is the highest-risk thing you can do in this repo. Before you do:

- Read `dsec-monorepo/DEPLOYMENT_ORDER.md` (the deploy-order doctrine still holds).
- Write the migration **by hand**; do not autogenerate. Confirm it emits no
  `DROP` against a table owned by `dsec-hub` (its app-owned tables) or another app.
- If the change adds a constraint that existing writers could violate (for
  example a `CHECK` that another repo's write path doesn't yet satisfy), the
  migration and the matching change in that other repo **must ship in the same
  deploy window** — or production 500s the moment the migration lands.
- A Neon backup is taken before any migration deploy.

If you're not sure, open an issue and flag it. The maintainer coordinates schema
deploys; don't merge one on your own.
