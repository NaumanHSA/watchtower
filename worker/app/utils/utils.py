import sys
import httpx
import asyncio
import logging
from config import config
from ..utils.host import _primary_ip

logger = logging.getLogger()


def check_url(url: str) -> bool:
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(url)
            return 200 <= r.status_code < 300
    except Exception:
        return False

def check_url_reachable(url: str) -> bool:
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.head(url)
            # Fallback to GET if HEAD not allowed
            if r.status_code >= 400:
                r = c.get(url)
            return r.status_code < 500  # 2xx, 3xx, or even 4xx means reachable
    except Exception:
        ...
    return False

def set_stream_error_call(stream_id: str):
    """
    Called from inside Engine (child process) when it decides to stop and set error for its own stream. Uses the worker's HTTP API.
    """
    base = f"http://127.0.0.1:{config.HOST_PORT}"
    # If your worker is behind a reverse proxy, use a stable internal URL instead.
    url = f"{base}/streams/set_error/{stream_id}"
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.post(url)
            if r.status_code == 200:
                print("Set error stream call success for stream_id: ", stream_id)
            return 200 <= r.status_code < 300
    except Exception:
        return False

def stop_stream_call(stream_id: str):
    """
    Called from inside Engine (child process) when it decides to stop and set error for its own stream. Uses the worker's HTTP API.
    """
    base = f"http://127.0.0.1:{config.HOST_PORT}"
    # If your worker is behind a reverse proxy, use a stable internal URL instead.
    url = f"{base}/streams/stop/{stream_id}"
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.post(url)
            return 200 <= r.status_code < 300
    except Exception:
        return False

def delete_stream_call(stream_id: str) -> bool:
    """
    Called from inside Engine (child process) when it decides to stop and remove its own stream. Uses the worker's HTTP API.
    """
    base = f"http://127.0.0.1:{config.HOST_PORT}"
    # If your worker is behind a reverse proxy, use a stable internal URL instead.
    url = f"{base}/streams/delete/{stream_id}"
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.delete(url)
            return 200 <= r.status_code < 300
    except Exception:
        return False

def worker_callback_url_auto() -> str:
    # Try to craft a reachable URL for the controller:
    # primary IP + port (controller talks to container IP on the same network)
    return f"http://{_primary_ip()}:{config.HOST_PORT}"



async def graceful_shutdown(delay: int = 3):
    await asyncio.sleep(delay)
    logger.error("[graceful_shutdown] Shutting down via internal endpoint...")
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            await c.post(
                f"http://127.0.0.1:{config.HOST_PORT}/internal/shutdown/{config.INTERNAL_SHUTDOWN_KEY}",
            )
    except Exception as e:
        logger.error(f"[graceful_shutdown] Failed to call shutdown endpoint: {e}")
        # fallback hard-exit if internal endpoint unreachable
        sys.exit(1)