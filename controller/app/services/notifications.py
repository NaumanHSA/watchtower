import asyncio
from dataclasses import dataclass
from typing import Dict, Set, Optional, Any, Literal
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from config import config
from ..security import require_worker_api_key, require_watchtower_token

router = APIRouter(prefix="/dashboard/ws", tags=["notifications"])

@dataclass
class Client:
    ws: WebSocket
    user_id: str
    queue: asyncio.Queue  # messages to send

class NotificationManager:
    def __init__(self):
        self._clients: Dict[str, Client] = {}     # key: connection id
        self._lock = asyncio.Lock()
        self._ping_task: Optional[asyncio.Task] = None
        self._redis_tx = None
        self._redis_rx_task: Optional[asyncio.Task] = None

    async def start(self):
        if config.REDIS_URL:
            try:
                import redis.asyncio as aioredis
                self._redis_tx = await aioredis.from_url(config.REDIS_URL, decode_responses=True)
                # subscribe in background
                pubsub = self._redis_tx.pubsub()
                await pubsub.subscribe("controller:events")
                self._redis_rx_task = asyncio.create_task(self._redis_reader(pubsub))
            except Exception as e:
                # If redis fails, continue in single-instance mode
                print(f"[notifications] Redis disabled: {e}")
                self._redis_tx = None
        self._ping_task = asyncio.create_task(self._pinger())

    async def stop(self):
        if self._ping_task:
            self._ping_task.cancel()
        if self._redis_rx_task:
            self._redis_rx_task.cancel()
        # Close all sockets
        async with self._lock:
            for cid, client in list(self._clients.items()):
                try:
                    await client.ws.close(code=1001)
                except Exception:
                    pass
                self._clients.pop(cid, None)

    async def register(self, ws: WebSocket, user_id: str) -> str:
        cid = f"ws-{id(ws)}"
        await ws.accept()
        async with self._lock:
            self._clients[cid] = Client(ws=ws, user_id=user_id, queue=asyncio.Queue(maxsize=config.WS_SEND_QUEUE_SIZE))
        asyncio.create_task(self._sender_loop(cid))
        return cid

    async def unregister(self, cid: str):
        async with self._lock:
            c = self._clients.pop(cid, None)
        if c:
            try:
                await c.ws.close(code=1001)
            except Exception:
                pass

    async def broadcast(self, event: Dict[str, Any], audience: Literal["all"] = "all"):
        """
        Broadcast to local clients; if Redis is configured, also publish for other replicas.
        """
        # publish to Redis channel for multi-replica
        if self._redis_tx:
            try:
                import json
                await self._redis_tx.publish("controller:events", json.dumps(event))
            except Exception as e:
                print(f"[notifications] redis publish error: {e}")

        # local broadcast
        async with self._lock:
            dead: Set[str] = set()
            for cid, client in self._clients.items():
                try:
                    client.queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Drop slow client
                    dead.add(cid)
            for cid in dead:
                asyncio.create_task(self.unregister(cid))

    async def _sender_loop(self, cid: str):
        # Each client gets its own sender loop to avoid backpressure on others
        client = self._clients.get(cid)
        if not client:
            return
        try:
            while True:
                msg = await client.queue.get()
                await client.ws.send_json(msg)
        except Exception:
            pass
        finally:
            await self.unregister(cid)

    async def _pinger(self):
        while True:
            await asyncio.sleep(config.WS_PING_INTERVAL_SECONDS)
            async with self._lock:
                for cid, client in list(self._clients.items()):
                    try:
                        await client.ws.send_json({"event": "ping"})
                    except Exception:
                        asyncio.create_task(self.unregister(cid))

    async def _redis_reader(self, pubsub):
        import json
        try:
            async for m in pubsub.listen():
                if m is None or m.get("type") != "message":
                    continue
                payload = m.get("data")
                try:
                    event = json.loads(payload)
                except Exception:
                    continue
                # fan-out to local clients
                await self.broadcast(event)  # this will re-publish unless we gate
        except asyncio.CancelledError:
            return

manager = NotificationManager()

@router.websocket("/notifications", dependencies=[Depends(require_worker_api_key), Depends(require_watchtower_token)])
async def notifications_ws(websocket: WebSocket):
    # user could be a dict with role; for now we derive a synthetic id
    user_id = "admin"
    cid = await manager.register(websocket, user_id=user_id)
    try:
        while True:
            # Optional: receive pings from client; ignore content
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.unregister(cid)
