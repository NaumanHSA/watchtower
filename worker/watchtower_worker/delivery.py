"""Result delivery to the results URL, in one of two modes per stream:

realtime  bounded in-memory queue; the oldest result is dropped when full and anything
          older than max_age is discarded. Never late, may lose results.
reliable  results are written to a SQLite spool first and sent in order, retrying with
          backoff until the receiver accepts them. Never lost (within spool_max_bytes),
          may be late.

When the receiver is healthy both modes send immediately. Payloads are encrypted (if
the controller supplied a key) before they are queued or spooled.
"""
from __future__ import annotations

import collections
import json
import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from watchtower_contracts import DeliveryMode, DeliveryStats, ResultKey
from watchtower_contracts.crypto import ENVELOPE_HEADER, encrypt_json

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Route:
    url: str | None
    mode: DeliveryMode


class Spool:
    """Append-only on-disk FIFO. One instance shared by the intake and sender threads."""

    def __init__(self, path: Path, max_bytes: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS spool (id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, "
                         "url TEXT, headers TEXT, body BLOB)")
        self.size_bytes, self.count = self._db.execute(
            "SELECT COALESCE(SUM(LENGTH(body)), 0), COUNT(*) FROM spool").fetchone()

    def put(self, url: str, headers: dict[str, str], body: bytes) -> int:
        """Store a result; returns how many old results were evicted to stay under max_bytes."""
        evicted = 0
        with self._lock:
            self._db.execute("INSERT INTO spool (created, url, headers, body) VALUES (?, ?, ?, ?)",
                             (time.time(), url, json.dumps(headers), body))
            self.size_bytes += len(body)
            self.count += 1
            while self.size_bytes > self.max_bytes and self.count > 1:
                row = self._db.execute("SELECT id, LENGTH(body) FROM spool ORDER BY id LIMIT 1").fetchone()
                self._db.execute("DELETE FROM spool WHERE id = ?", (row[0],))
                self.size_bytes -= row[1]
                self.count -= 1
                evicted += 1
        return evicted

    def peek(self) -> tuple[int, str, dict[str, str], bytes] | None:
        with self._lock:
            row = self._db.execute("SELECT id, url, headers, body FROM spool ORDER BY id LIMIT 1").fetchone()
        return (row[0], row[1], json.loads(row[2]), row[3]) if row else None

    def delete(self, row_id: int) -> None:
        with self._lock:
            row = self._db.execute("SELECT LENGTH(body) FROM spool WHERE id = ?", (row_id,)).fetchone()
            if row:
                self._db.execute("DELETE FROM spool WHERE id = ?", (row_id,))
                self.size_bytes -= row[0]
                self.count -= 1

    def close(self) -> None:
        with self._lock:
            self._db.close()


class DeliveryService:
    def __init__(
        self,
        *,
        results_queue: Any,
        spool_path: Path,
        spool_max_bytes: int = 512 * 1024 * 1024,
        realtime_queue_size: int = 100,
        max_age_seconds: float = 5.0,
        timeout_seconds: float = 5.0,
        require_encryption: bool = False,
        default_route: Route | None = None,
        extra_headers: dict[str, str] | None = None,
        realtime_senders: int = 2,
        transport: httpx.BaseTransport | None = None,
    ):
        self.results_queue = results_queue
        self.spool = Spool(spool_path, spool_max_bytes)
        self.max_age = max_age_seconds
        self.timeout = timeout_seconds
        self.require_encryption = require_encryption
        self.default_route = default_route or Route(None, DeliveryMode.REALTIME)
        self.extra_headers = extra_headers or {}
        self._transport = transport
        self._key: ResultKey | None = None
        self._routes: dict[str, Route] = {}
        self._realtime: collections.deque = collections.deque(maxlen=realtime_queue_size)
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._stats = DeliveryStats()
        self._stats_lock = threading.Lock()
        self._warned_no_url = False
        self._threads = [threading.Thread(target=self._intake, name="delivery-intake", daemon=True),
                         threading.Thread(target=self._send_reliable, name="delivery-reliable", daemon=True)]
        self._threads += [threading.Thread(target=self._send_realtime, name=f"delivery-rt-{i}", daemon=True)
                          for i in range(realtime_senders)]

    # ---- control ----

    def start(self) -> None:
        for thread in self._threads:
            thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        for thread in self._threads:
            thread.join(timeout)
        self.spool.close()

    def set_key(self, key: ResultKey | None) -> None:
        self._key = key

    def set_route(self, stream_id: str, route: Route) -> None:
        self._routes[stream_id] = route  # kept after the stream stops so late results still route

    def stats(self) -> DeliveryStats:
        with self._stats_lock:
            stats = self._stats.model_copy()
        stats.spooled, stats.spool_bytes = self.spool.count, self.spool.size_bytes
        return stats

    def _count(self, field: str, n: int = 1) -> None:
        with self._stats_lock:
            setattr(self._stats, field, getattr(self._stats, field) + n)

    # ---- intake: engine results -> realtime queue or spool ----

    def _intake(self) -> None:
        while not self._stop.is_set():
            try:
                item = self.results_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                return
            try:
                self.accept(item["stream_id"], item["event"])
            except Exception:
                log.exception("could not queue a result")
                self._count("failed")

    def accept(self, stream_id: str, event: dict[str, Any]) -> None:
        route = self._routes.get(stream_id, self.default_route)
        url = route.url or self.default_route.url
        if not url:
            if not self._warned_no_url:
                log.warning("no results URL configured: results are discarded (set RESULTS_URL)")
                self._warned_no_url = True
            self._count("dropped")
            return
        body: Any = {"ok": True, "received": event}
        headers = {"Content-Type": "application/json", "X-Watchtower-Event-Id": event.get("eventId", ""),
                   **self.extra_headers}
        key = self._key
        if key is not None:
            body = encrypt_json(body, key.public_key_pem, key.kid)
            headers[ENVELOPE_HEADER] = "v1"
        elif self.require_encryption:
            self._count("dropped")
            log.warning("dropping a result: REQUIRE_ENCRYPTION is set and no encryption key is available")
            return
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if route.mode == DeliveryMode.RELIABLE:
            evicted = self.spool.put(url, headers, data)
            if evicted:
                self._count("dropped", evicted)
                log.warning("spool full: evicted %d oldest results", evicted)
            with self._cond:
                self._cond.notify_all()
        else:
            with self._cond:
                if len(self._realtime) == self._realtime.maxlen:
                    self._count("dropped")  # deque drops the oldest on append
                self._realtime.append((time.monotonic(), url, headers, data))
                self._cond.notify_all()

    # ---- senders ----

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, transport=self._transport)

    def _send_realtime(self) -> None:
        with self._client() as client:
            while not self._stop.is_set():
                with self._cond:
                    while not self._realtime and not self._stop.is_set():
                        self._cond.wait(0.5)
                    if self._stop.is_set():
                        return
                    queued_at, url, headers, data = self._realtime.popleft()
                if time.monotonic() - queued_at > self.max_age:
                    self._count("dropped")  # stale: realtime never delivers late
                    continue
                try:
                    r = client.post(url, content=data, headers=headers)
                    self._count("sent" if r.is_success else "failed")
                    if not r.is_success:
                        log.warning("results receiver returned HTTP %s", r.status_code)
                except httpx.HTTPError as e:
                    self._count("failed")
                    log.warning("results receiver unreachable: %s", e)

    def _send_reliable(self) -> None:
        failures = 0
        with self._client() as client:
            while not self._stop.is_set():
                row = self.spool.peek()
                if row is None:
                    with self._cond:
                        self._cond.wait(0.5)
                    continue
                row_id, url, headers, data = row
                try:
                    r = client.post(url, content=data, headers=headers)
                    retry = r.status_code in RETRYABLE_STATUS
                    if r.is_success:
                        self.spool.delete(row_id)
                        self._count("sent")
                        failures = 0
                        continue
                    if not retry:  # the receiver will never take this one: don't block the queue
                        log.error("results receiver rejected a result with HTTP %s; dropping it", r.status_code)
                        self.spool.delete(row_id)
                        self._count("failed")
                        continue
                except httpx.HTTPError as e:
                    log.warning("results receiver unreachable (%d spooled): %s", self.spool.count, e)
                failures += 1
                self._stop.wait(min(30.0, 0.5 * 2 ** min(failures, 6)))  # back off, keep order
