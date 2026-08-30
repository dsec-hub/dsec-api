"""backfill document.type='Page' for slug-published rows that predate the invariant

NEW-APIROUTERS-01 made the page preview require ``type == 'Page'``, but the public
/website/pages feed serves a document by ``slug + is_public`` alone, so a page
created before this (or via an API/Drizzle write) with ``type`` NULL kept being
served yet minting/consuming its preview link 400/404'd. create/update now keep
``type`` in lock-step with the slug; this backfills the rows already in the table.

Data-only, idempotent (``type`` NULL/'' AND slug present → 'Page'). Runs on the
Postgres deploy via ``alembic upgrade head``; the SQLite dev/test schema is built
from the models + stamped, so this never replays there (the logic is unit-tested
through ``documents.service.backfill_page_document_type`` instead). The backfill
is not reversible — downgrade is a deliberate no-op.

Revision ID: c7d2e9f4a1b6
Revises: b1d4f7a9c3e2
Create Date: 2026-08-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c7d2e9f4a1b6"
down_revision: Union[str, Sequence[str], None] = "b1d4f7a9c3e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BACKFILL_SQL = (
    "UPDATE document SET type = 'Page' "
    "WHERE slug IS NOT NULL AND (type IS NULL OR type = '')"
)


def upgrade() -> None:
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    # Not reversible: we cannot tell which rows we set vs. which were already
    # 'Page'. Leaving them as 'Page' is harmless and correct.
    pass
