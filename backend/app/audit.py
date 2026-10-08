"""Audit trail: who changed what. Stored in the database (not in logs)."""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import AuditLog


async def record(db: AsyncSession, user_id: int | None, action: str, entity: str,
                 entity_id: int | None, **detail) -> None:
    """Add an audit entry to the current transaction (the caller commits)."""
    db.add(AuditLog(user_id=user_id, action=action, entity=entity, entity_id=entity_id,
                    detail=detail))
