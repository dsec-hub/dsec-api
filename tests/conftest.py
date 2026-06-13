"""Shared test fixtures + an isolated, throwaway test database.

Design goals (per the build spec): tests must run with NO external services —
no Neon, no OpenAI. We achieve that by:

1. Pointing `DATABASE_URL` at a temporary SQLite file BEFORE importing the app,
   so the engine binds to the throwaway DB.
2. Monkeypatching the email pipeline's `classify` / `generate` so the OpenAI
   wrapper is never invoked (see the `patch_llm` fixture).
"""

from __future__ import annotations

import os
import tempfile

# --- Configure the environment BEFORE importing any app module. ---------------
# pydantic Settings reads env vars at import; the DB engine is built at import.
_tmp_db = tempfile.NamedTemporaryFile(prefix="dsec_test_", suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"
os.environ.setdefault("AGENT_SECRET", "test-agent-secret")
os.environ.setdefault("DASHBOARD_USER", "admin")
os.environ.setdefault("DASHBOARD_PASS", "test-dashboard-pass")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-used")
os.environ.setdefault("CALCOM_LINK", "https://cal.com/dsec-test")
os.environ.setdefault("API_KEY_PREFIX", "dsec_live_")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402,F401  (register models on Base.metadata)
from app.core.apikeys import generate_key  # noqa: E402
from app.core.llm import LLMResult  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.features.email import pipeline as email_pipeline  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    """Give every test a clean schema in the temporary SQLite DB."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    """A DB session bound to the test engine (same temp file the app uses)."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    """FastAPI TestClient. Tables are managed by `_fresh_db`, not lifespan."""
    return TestClient(app)


@pytest.fixture
def make_email():
    """Factory for a valid /email/process JSON body (uses the `from` alias)."""

    def _make(**overrides) -> dict:
        data = {
            "threadId": "t1",
            "messageId": "m1",
            "from": "person@example.com",
            "to": "committee@dsec.club",
            "subject": "Hello",
            "body": "Hi there, I have a question about joining the club.",
            "date": "2026-06-13T10:00:00Z",
        }
        data.update(overrides)
        return data

    return _make


@pytest.fixture
def agent_headers():
    return {"X-Agent-Secret": os.environ["AGENT_SECRET"]}


@pytest.fixture
def trigger_key(db):
    """Create a read+trigger scoped API key; return its raw (once-shown) value."""
    gen = generate_key()
    db.add(
        models.APIKey(
            name="test-trigger-key",
            prefix=gen.prefix,
            key_hash=gen.key_hash,
            scopes=["read", "trigger"],
        )
    )
    db.commit()
    return gen.raw_key


class _Recorder:
    """Records calls made to the patched LLM functions."""

    def __init__(self) -> None:
        self.classify_args: list = []
        self.generate_args: list = []


@pytest.fixture
def patch_llm(monkeypatch):
    """Install controllable fakes for the pipeline's `classify` / `generate`.

    Returns a function; call it to install the patch and receive a `_Recorder`
    so a test can assert whether (and how) the LLM layer was invoked.
    """

    def _install(
        *,
        label: str = "simple-reply",
        classify_exc: Exception | None = None,
        draft: str = "Thanks for reaching out.\n\nBest regards,\nThe DSEC Committee",
        draft_exc: Exception | None = None,
    ) -> _Recorder:
        rec = _Recorder()

        def fake_classify(system_prompt, user_content, model=None):
            rec.classify_args.append((system_prompt, user_content, model))
            if classify_exc is not None:
                raise classify_exc
            return LLMResult(text=label, tokens=10, cost=0.0001, model="gpt-4o-mini")

        def fake_generate(system_prompt, user_content, model=None):
            rec.generate_args.append((system_prompt, user_content, model))
            if draft_exc is not None:
                raise draft_exc
            return LLMResult(text=draft, tokens=20, cost=0.0002, model="gpt-4o-mini")

        monkeypatch.setattr(email_pipeline, "classify", fake_classify)
        monkeypatch.setattr(email_pipeline, "generate", fake_generate)
        return rec

    return _install
