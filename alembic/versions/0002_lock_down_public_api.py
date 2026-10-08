"""Lock tables away from Supabase's public REST API.

Supabase exposes the public schema through PostgREST to the `anon` and `authenticated`
roles. Only our backend (database owner) may read student data, so on PostgreSQL every
table gets row level security with no policies, and those roles lose all privileges.
No-op on SQLite.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("users", "sessions", "exams", "bundles", "scripts", "question_marks", "audit_log",
          "alembic_version")

_LOCK = """
DO $$
DECLARE r text;
BEGIN
  FOR r IN SELECT unnest(ARRAY['anon', 'authenticated']) LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', r);
      EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', r);
      EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I', r);
      EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', r);
    END IF;
  END LOOP;
END $$;
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(_LOCK)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
