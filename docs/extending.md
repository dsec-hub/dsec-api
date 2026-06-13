# Extending — adding a new feature module

The architecture exists so a new inbound integration is **a folder + a router +
one mount line**. No existing feature folder is edited. Everything shared comes
from the core.

## The contract

1. New folder under `app/features/<name>/` with `__init__.py` and `router.py`.
2. `router.py` exposes `router = APIRouter()`.
3. Reuse the shared core — never reimplement db/auth/llm/logging/apikeys/ratelimit.
4. Add exactly one line to `app/main.py`:
   `app.include_router(<name>_router, prefix="/<name>", tags=["<name>"])`.

## Worked example — a Slack relay

`app/features/slack/__init__.py` (empty), then:

```python
# app/features/slack/router.py
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import verify_webhook_signature   # reuse shared auth
from app.core import logging as event_logging    # reuse shared logging
from app.db import get_db                         # reuse shared DB session

router = APIRouter()


@router.post("/webhook")
async def slack_webhook(request: Request, db: Session = Depends(get_db),
                        _: None = Depends(verify_webhook_signature("slack"))):
    payload = await request.json()
    event_logging.log_event(
        db, source="slack", action="received",
        external_id=payload.get("event_id"), payload=payload,
    )
    return {"ok": True}
```

Then in `app/main.py`:

```python
from app.features.slack.router import router as slack_router
...
app.include_router(slack_router, prefix="/slack", tags=["slack"])
```

If the new feature needs a secret, add it to `app/config.py` `Settings` and to
`.env.example`. If it signs webhooks, add a branch to `_secret_for_mode` /
header selection in `app/auth.py:verify_webhook_signature`.

## Reusing the LLM

Any feature can call the generic wrapper — it is **not** email-specific:

```python
from app.core.llm import classify, generate, LLMError

try:
    result = generate("You are a helpful assistant.", user_text)
    # result.text, result.tokens, result.cost, result.model
except LLMError:
    ...  # degrade gracefully — never crash the request
```

If the route spends LLM money, make it `trigger`-scoped and call the rate limiter
first:

```python
from app.core.apikeys import require_api_key
from app.core.ratelimit import limiter

@router.post("/do-llm-thing")
def thing(request: Request, db=Depends(get_db),
          key=Depends(require_api_key("trigger"))):
    limiter.check_request(db, key_id=key.id, ip=client_ip(request))
    limiter.check_and_count_trigger(db, key_id=key.id)  # 429 if capped, no LLM call
    ...
```

## Logging

Every feature logs to the same `EventLog` via `log_event(..., source="<name>")`,
so the dashboard surfaces it automatically. Use a recognisable `source` and add
it to the dashboard filter list in `app/dashboard/router.py` if you want a
dropdown entry (optional — free-text still works).

## Checklist

- [ ] `app/features/<name>/__init__.py` + `router.py` with `router = APIRouter()`
- [ ] Reuses core (no duplicated db/auth/llm/logging)
- [ ] One `include_router` line in `app/main.py`
- [ ] New secrets added to `config.py` + `.env.example` (if any)
- [ ] LLM-spending routes are `trigger`-scoped and rate-limited
- [ ] Logs to `EventLog` with a clear `source`
- [ ] **No existing feature folder was edited**
