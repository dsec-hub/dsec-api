# Operational runbooks

Working documents for in-flight operational tickets. They are checked in so that
the person holding production credentials can read them on the machine that has
those credentials — not because they are finished documentation.

| File | Ticket | Status |
|---|---|---|
| `OPS-02-vps-questions.md` | OPS-02 | **Open** — the step-0 gate. Commands to run on the VPS before any branch is merged. |
| `OPS-02-step-0-findings.md` | OPS-02 | **Open** — what step 0 established, and three findings that change the merge plan. |

## OPS-02 in one paragraph

`main` is behind all six remote branches and ahead of none. Production is running
`deploy/vps`, which has never had a pull request. The task is to reconcile the
branches into `main` in a fixed order without reverting production. It is blocked
at step 0 until the deployed commit is confirmed from the box itself, because a
hotfix applied directly on the server would exist in no branch and a merge would
silently remove it.

**Nothing may be merged until `OPS-02-vps-questions.md` §A–§E are answered.**

## Note on this repository being public

These files deliberately carry no host identifiers, credentials, or `.env` values.
Keep it that way when updating them. Answers to the §A–§E questions contain the
deployed SHA and a redacted list of environment variable *names* — the names are
safe to share; the values are not, and no step here asks for them.
