"""Event bus abstraction.

Two interchangeable implementations:

``memory``
    In-process pub/sub. Zero dependencies, correct for a single backend
    process. This is the local-development default.

``redis``
    Cross-process fan-out, required as soon as more than one backend worker
    serves dashboard WebSockets or SDK control long-polls.

Both are used for two distinct purposes:

1. ``user:{user_id}`` channels push live execution/incident updates to the
   dashboard WebSocket.
2. ``control:{execution_id}`` channels wake up an SDK long-poll the instant a
   control command is issued, instead of waiting for the next poll interval.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

# Bounded so a stalled subscriber cannot grow memory without limit; the
# oldest message is dropped instead.
QUEUE_MAXSIZE = 512


class AbstractEventBus(ABC):
    @abstractmethod
    async def publish(self, channel: str, message: dict[str, Any]) -> None: ...

    @abstractmethod
    def subscribe(self, channel: str) -> "Subscription": ...

    async def close(self) -> None:  # pragma: no cover - trivial default
        return None

    async def wait_for_message(
        self, channel: str, timeout: float
    ) -> dict[str, Any] | None:
        """Block until one message arrives on ``channel`` or ``timeout`` elapses.

        Used by the SDK control long-poll so a command is delivered with
        push-like latency rather than on a fixed interval.
        """
        async with self.subscribe(channel) as sub:
            try:
                return await asyncio.wait_for(sub.get(), timeout=timeout)
            except (asyncio.TimeoutError, TimeoutError):
                return None


class Subscription:
    """Async context manager yielding messages from one channel."""

    def __init__(self, bus: "AbstractEventBus", channel: str) -> None:
        self._bus = bus
        self._channel = channel
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._closed = False

    @property
    def channel(self) -> str:
        return self._channel

    def _deliver(self, message: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(message)

    async def get(self) -> dict[str, Any]:
        return await self._queue.get()

    async def __aenter__(self) -> "Subscription":
        await self._bus._register(self)  # noqa: SLF001 - intentional friend access
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._closed = True
        await self._bus._unregister(self)  # noqa: SLF001

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[dict[str, Any]]:
        while not self._closed:
            yield await self._queue.get()


class InMemoryEventBus(AbstractEventBus):
    def __init__(self) -> None:
        self._subs: dict[str, set[Subscription]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def _register(self, sub: Subscription) -> None:
        async with self._lock:
            self._subs[sub.channel].add(sub)

    async def _unregister(self, sub: Subscription) -> None:
        async with self._lock:
            self._subs.get(sub.channel, set()).discard(sub)
            if not self._subs.get(sub.channel):
                self._subs.pop(sub.channel, None)

    def subscribe(self, channel: str) -> Subscription:
        return Subscription(self, channel)

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        for sub in list(self._subs.get(channel, ())):
            sub._deliver(message)  # noqa: SLF001


class RedisEventBus(AbstractEventBus):
    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis  # imported lazily so redis stays optional

        self._redis = aioredis.from_url(url, decode_responses=True)
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        self._subs: dict[str, set[Subscription]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._reader: asyncio.Task[None] | None = None

    async def _ensure_reader(self) -> None:
        if self._reader is None or self._reader.done():
            self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self._pubsub.listen():
                if raw.get("type") != "message":
                    continue
                channel = raw["channel"]
                try:
                    message = json.loads(raw["data"])
                except json.JSONDecodeError:
                    logger.warning("discarding non-JSON bus message on %s", channel)
                    continue
                for sub in list(self._subs.get(channel, ())):
                    sub._deliver(message)  # noqa: SLF001
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - connection loss path
            logger.exception("redis bus reader stopped")

    async def _register(self, sub: Subscription) -> None:
        async with self._lock:
            first = not self._subs[sub.channel]
            self._subs[sub.channel].add(sub)
            if first:
                await self._pubsub.subscribe(sub.channel)
            await self._ensure_reader()

    async def _unregister(self, sub: Subscription) -> None:
        async with self._lock:
            self._subs.get(sub.channel, set()).discard(sub)
            if not self._subs.get(sub.channel):
                self._subs.pop(sub.channel, None)
                with contextlib.suppress(Exception):
                    await self._pubsub.unsubscribe(sub.channel)

    def subscribe(self, channel: str) -> Subscription:
        return Subscription(self, channel)

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        await self._redis.publish(channel, json.dumps(message, default=str))

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        with contextlib.suppress(Exception):
            await self._pubsub.aclose()
        with contextlib.suppress(Exception):
            await self._redis.aclose()


_bus: AbstractEventBus | None = None


def get_bus() -> AbstractEventBus:
    global _bus
    if _bus is None:
        settings = get_settings()
        if settings.event_bus == "redis":
            logger.info("event bus: redis (%s)", settings.redis_url)
            _bus = RedisEventBus(settings.redis_url)
        else:
            logger.info("event bus: in-process memory")
            _bus = InMemoryEventBus()
    return _bus


async def close_bus() -> None:
    global _bus
    if _bus is not None:
        await _bus.close()
        _bus = None


def user_channel(user_id: str) -> str:
    return f"user:{user_id}"


def control_channel(execution_id: str) -> str:
    return f"control:{execution_id}"
