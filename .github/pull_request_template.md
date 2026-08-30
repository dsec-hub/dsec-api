<!-- Keep this short. A reviewer should understand the change in under a minute. -->

## What & why

<!-- One or two sentences. Link the issue: Closes #123 -->

## How to test

<!-- The exact steps a reviewer runs to confirm this works. -->

## Checklist

- [ ] `pytest` passes locally
- [ ] No secrets, `.env` files, tokens, real member names, or student IDs in the diff
- [ ] **No schema change** — or, if there is one: it is a hand-reviewed Alembic
      revision (never `--autogenerate` against a real database), it does not
      `DROP` an app-owned table, and it follows the deploy-order rules in
      `CONTRIBUTING.md`
- [ ] Migrations were **not** run against live Neon from a local script; DDL ships
      through the normal deploy
