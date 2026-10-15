"""Who changed what, kept in MongoDB for AUDIT_RETENTION_DAYS."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from .auth import Principal

log = logging.getLogger(__name__)


class Auditor:
    def __init__(self, db: AsyncDatabase, retention_days: int):
        self.db = db
        self.retention_days = retention_days

    async def record(self, principal: Principal | None, action: str, target_id: str | None = None,
                     **details: Any) -> None:
        now = datetime.now(UTC)
        entry = {
            "ts": now,
            "subject": principal.subject if principal else "system",
            "role": principal.role.value if principal else None,
            "action": action,
            "target_id": target_id,
            "details": details,
        }
        if self.retention_days > 0:
            entry["expires_at"] = now + timedelta(days=self.retention_days)
        try:
            await self.db.audit.insert_one(entry)
        except Exception:  # the change itself already happened; don't turn it into an error
            log.exception("could not write audit entry %s on %s", action, target_id)
