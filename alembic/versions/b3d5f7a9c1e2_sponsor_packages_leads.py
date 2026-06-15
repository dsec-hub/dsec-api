"""sponsor_packages_leads: public tier definitions + inbound lead inbox

Adds:
  - sponsor_package: exec-editable tier cards served at /website/sponsor-packages
  - sponsor_lead:    inbound leads from dsec-website forms + Cal.com bookings

HAND-WRITTEN — do not autogenerate against live Neon (see workspace_tables note).

Revision ID: b3d5f7a9c1e2
Revises: f3a9d2b7c4e1
Create Date: 2026-06-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b3d5f7a9c1e2"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "sponsor_package",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("pitch", sa.String(length=512), nullable=True),
        sa.Column("price", sa.String(length=64), nullable=True),
        sa.Column("includes", sa.JSON(), nullable=True),
        sa.Column("featured", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_visible", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sponsor_package", schema=None) as b:
        b.create_index(b.f("ix_sponsor_package_is_visible"), ["is_visible"], unique=False)

    op.create_table(
        "sponsor_lead",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("tier", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=256), nullable=True),
        sa.Column("email", sa.String(length=256), nullable=False),
        sa.Column("company", sa.String(length=256), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("budget", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'new'"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sponsor_lead", schema=None) as b:
        b.create_index(b.f("ix_sponsor_lead_email"), ["email"], unique=False)
        b.create_index(b.f("ix_sponsor_lead_source"), ["source"], unique=False)
        b.create_index(b.f("ix_sponsor_lead_status"), ["status"], unique=False)
        b.create_index(b.f("ix_sponsor_lead_created_at"), ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_table("sponsor_lead")
    op.drop_table("sponsor_package")
