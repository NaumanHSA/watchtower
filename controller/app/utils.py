import time
from fastapi import Header, HTTPException, status
from typing import Optional
from .db import connect

async def enforce_idempotency(idempotency_key: Optional[str] = Header(None)):
    """
    Prevent duplicate POST/DELETE side effects if client retries.
    """
    if not idempotency_key:
        return
    db = await connect()
    try:
        await db.idempotency.insert_one({
            "key": idempotency_key,
            "created_at": int(time.time())
        })
    except Exception:
        # Duplicate key means operation already attempted
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate idempotency key"
        )
