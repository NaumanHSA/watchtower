from fastapi import Header, HTTPException, status, Depends, WebSocket, Query
from typing import Optional
from config import config
import httpx


async def require_worker_api_key(worker_api_key: Optional[str] = Header(None)):
    if not worker_api_key or worker_api_key != config.WORKER_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid worker API key")

async def require_watchtower_token(watchtower_token: Optional[str] = Header(None)):
    # TODO: implement watchtower token varification through API Call
    # The API call will return public key pem for encrytion
    bypass_verification = True
    if bypass_verification:
        return watchtower_token, None
    try:
        headers = {
            "Content-Type": "application/json",
            "watchtower-token": watchtower_token,
        }
        response = httpx.get(config.TOKEN_VERIFICATION_URL, headers=headers)
        response.raise_for_status()
        success = response.json().get("isSuccess", False)
        public_key_pem = response.json().get("pub_cov", None)
        if not success:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid watchtower token")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid watchtower token: {e}")
    return watchtower_token, public_key_pem
