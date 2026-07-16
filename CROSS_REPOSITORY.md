# Cross-repository contract

`dsec-api` is the backend contract for the DSEC services:

- It owns the core Neon schema, SQLAlchemy models, and Alembic migrations. Apply
  migrations before deploying dependent applications.
- `dsec-hub` uses the same Neon database directly and adds only its app-owned,
  idempotent setup objects.
- `dsec-app`, `dsec-website`, and `dsec-games` consume this service through its
  HTTP API. Games traffic is proxied server-side with `DSEC_API_KEY`.
- Keep `CORS_ALLOW_ORIGINS` configured for `https://dsec.club`,
  `https://app.dsec.club`, `https://hub.dsec.club`, and
  `https://games.dsec.club`.
- `HUB_NOTIFY_URL` and `HUB_NOTIFY_SECRET` must match the notification endpoint
  and secret configured by `dsec-hub`.

Deploy this repository first. See `docs/deployment.md` and
`docs/configuration.md` for the full configuration reference.
