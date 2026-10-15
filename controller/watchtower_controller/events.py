"""Live events for dashboards over WebSocket, fanned out across replicas through Redis."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)

REDIS_CHANNEL = "watchtower:events"


def make_event(event: str, severity: str = "info", /, **params: Any) -> dict[str, Any]:
    return {
        "event": event,
        "category": event.split(".", 1)[0],
        "severity": severity,
        "params": params,
        "ts": datetime.now(UTC).isoformat(),
        "event_id": uuid.uuid4().hex,
        "schema": "1",
    }


@dataclass(eq=False)
class _Client:
    ws: WebSocket
    queue: asyncio.Queue
    sender: asyncio.Task | None = field(default=None)


class Notifier:
    def __init__(self, queue_size: int = 100, ping_interval: float = 20, redis_url: str | None = None):
        self.instance_id = uuid.uuid4().hex
        self._queue_size = queue_size
        self._ping_interval = ping_interval
        self._redis_url = redis_url
        self._redis = None
        self._clients: set[_Client] = set()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        if self._redis_url:
            import redis.asyncio as aioredis  # optional dependency: watchtower-controller[redis]

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(REDIS_CHANNEL)
            self._tasks.append(asyncio.create_task(self._read_redis(pubsub)))
        self._tasks.append(asyncio.create_task(self._ping()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for client in list(self._clients):
            await self._drop(client)
        if self._redis is not None:
            await self._redis.aclose()

    async def publish(self, event_name: str, severity: str = "info", /, **params: Any) -> None:
        event = make_event(event_name, severity, **params)
        self._deliver(event)
        if self._redis is not None:
            try:
                await self._redis.publish(REDIS_CHANNEL, json.dumps({"origin": self.instance_id, "event": event}))
            except Exception as e:
                log.warning("redis publish failed: %s", e)

    # ---- websocket clients ----

    async def attach(self, ws: WebSocket, subprotocol: str | None = None) -> _Client:
        await ws.accept(subprotocol=subprotocol)
        client = _Client(ws=ws, queue=asyncio.Queue(maxsize=self._queue_size))
        client.sender = asyncio.create_task(self._send_loop(client))
        self._clients.add(client)
        return client

    async def detach(self, client: _Client) -> None:
        await self._drop(client)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def _deliver(self, event: dict[str, Any]) -> None:
        for client in list(self._clients):
            try:
                client.queue.put_nowait(event)
            except asyncio.QueueFull:
                log.info("dropping slow websocket client")
                asyncio.create_task(self._drop(client))

    async def _send_loop(self, client: _Client) -> None:
        try:
            while True:
                await client.ws.send_json(await client.queue.get())
        except asyncio.CancelledError:
            raise
        except Exception:
            asyncio.create_task(self._drop(client))

    async def _drop(self, client: _Client) -> None:
        if client not in self._clients:
            return
        self._clients.discard(client)
        if client.sender and client.sender is not asyncio.current_task():
            client.sender.cancel()
        with contextlib.suppress(Exception):
            await client.ws.close(code=1001)

    async def _ping(self) -> None:
        while True:
            await asyncio.sleep(self._ping_interval)
            self._deliver({"event": "ping", "ts": datetime.now(UTC).isoformat()})

    async def _read_redis(self, pubsub) -> None:
        while True:
            try:
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        data = json.loads(message["data"])
                    except (TypeError, ValueError):
                        continue
                    if data.get("origin") != self.instance_id:  # our own events were delivered locally
                        self._deliver(data["event"])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("redis subscription failed, retrying: %s", e)
                await asyncio.sleep(2)
