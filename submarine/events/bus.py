from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, Optional

from .types import AgentEvent


EventCallback = Callable[[AgentEvent], Optional[Awaitable[None]]]
EventPredicate = Callable[[AgentEvent], bool]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventCallback]] = defaultdict(list)
        self._all_subscribers: list[EventCallback] = []
        self._events: list[AgentEvent] = []
        self._condition = asyncio.Condition()

    def cursor(self) -> int:
        return len(self._events)

    def subscribe(self, role: str, callback: EventCallback) -> None:
        if role == "*":
            self._all_subscribers.append(callback)
            return
        self._subscribers[role].append(callback)

    def unsubscribe(self, role: str, callback: EventCallback) -> None:
        if role == "*":
            self._all_subscribers[:] = [h for h in self._all_subscribers if h is not callback]
            return
        self._subscribers[role][:] = [h for h in self._subscribers[role] if h is callback]

    async def publish(self, event: AgentEvent) -> None:
        async with self._condition:
            self._events.append(event)
            self._condition.notify_all()
        callbacks = [*self._all_subscribers, *self._subscribers.get(event.role, [])]
        for callback in callbacks:
            result = callback(event)
            if asyncio.iscoroutine(result):
                await result

    async def wait_for(self, predicate: EventPredicate, timeout: float | None = None) -> AgentEvent:
        event, _ = await self.wait_for_from(0, predicate, timeout=timeout)
        return event

    async def wait_for_from(
        self,
        cursor: int,
        predicate: EventPredicate,
        timeout: float | None = None,
    ) -> tuple[AgentEvent, int]:
        async def _inner() -> tuple[AgentEvent, int]:
            next_cursor = cursor
            while True:
                async with self._condition:
                    while next_cursor < len(self._events):
                        event = self._events[next_cursor]
                        next_cursor += 1
                        if predicate(event):
                            return event, next_cursor
                    await self._condition.wait()

        if timeout is None:
            return await _inner()
        return await asyncio.wait_for(_inner(), timeout=timeout)
