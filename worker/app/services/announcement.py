from fastapi import status
from config import config
import httpx
import asyncio
import logging
import sys
import logging
import urllib.parse

logger = logging.getLogger()


async def announce_with_retries(host_info: dict):
    controller_url = config.CONTROLLER_URL.strip("/")
    payload = {
        "worker_id": config.resolved_worker_id,
        "worker_url": config.HOST_WORKER_URL,
        "web_rtc_url": config.HOST_MTX_WEBRTC_URL,
        "controller_url": controller_url,
        "capabilities": {
            "max_allowed_streams": config.MAX_ALLOWED_STREAMS,
        },
        "host_info": host_info,
    }

    logger.info(f"\n[announce] Announcing to controller url: {controller_url[:20]}... with:")
    for k, v in payload.items():
        try: logger.info(f"{k}: {v[:8]}...")
        except: pass
            
    logger.info(f"Watchtower token: {config.WATCHTOWER_TOKEN[:5]}...")
    logger.info(f"Worker API key: {config.WORKER_API_KEY[:5]}...")
    logger.info("======================================\n")
        
    delay = config.ANNOUNCE_RETRY_INTERVAL
    last_err = None
    for attempt in range(1, config.ANNOUNCE_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                url = f"{controller_url}/internal/worker/go_live"
                headers = {
                    "Content-Type": "application/json",
                    "worker-api-key": config.WORKER_API_KEY,
                    "watchtower-token": config.WATCHTOWER_TOKEN,
                }
                r = await c.post(url, json=payload, headers=headers)
                r.raise_for_status()
                if 200 <= r.status_code < 300:
                    # get public key from response
                    json_response = r.json()
                    if not json_response["ok"] and json_response["code"] == status.HTTP_409_CONFLICT:
                        logger.info(f"[announce] worker already registered")
                        config.WORKER_ID = json_response["worker_id"]
                        
                    public_key_pem = json_response.get("public_key_pem")
                    if public_key_pem:
                        with open(config.PUBLIC_KEY_PEM_PATH, "w") as f:
                            f.write(public_key_pem)
                        logger.info(f"[announce] public key saved to {config.PUBLIC_KEY_PEM_PATH}")
                    return True
        except Exception as e:
            last_err = e
        
        logger.info(f"[announce] attempt {attempt} failed due to {last_err}. Retrying in {delay} seconds...")
        await asyncio.sleep(delay)
        delay *= 2
    logger.error(f"[announce] failed after {config.ANNOUNCE_RETRIES} attempts: {last_err}")
    return False

