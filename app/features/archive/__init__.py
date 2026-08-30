"""Service-continuity export: a portable, secret-free workspace manifest.

Handover between committees needs the *shape* of the deployment — the schema,
the env var contract, the API keys in play — without leaking any secret value or
student PII. `build_export_bundle` assembles exactly that (see service.py); the
`GET /admin/archive/export` route (basic-auth, in the admin router) serves it.
"""
