from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from submarine.events.types import AgentEvent, AgentEventStatus


# Re-export AgentEventStatus so backends don't need to import from events directly
__all__ = [
    "AgentBackend",
    "BackendConfig",
    "BackendFactory",
    "AgentEventStatus",
    "map_session_event_to_status",
]


class AgentBackend(ABC):
    """Pluggable backend for running agent work.

    A backend encapsulates: spawning a subprocess or process, speaking its
    wire protocol, and translating its native events into ``AgentEvent``
    objects that the Submarine supervisor can reason about.

    Concrete backends implement:

    - ``start()``   — bring up the backend process / connection
    - ``stop()``    — tear it down cleanly
    - ``run()``     — send a task for execution; events stream via ``subscribe``
    - ``resume()``  — answer a yielded question and continue the task

    """

    def __init__(self, role: str) -> None:
        self.role = role
        self._handlers: list[Callable[[AgentEvent], None]] = []

    @abstractmethod
    async def start(self) -> None:
        """Start the backend. Called once before the first ``run()``."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the backend and release resources."""

    @abstractmethod
    async def run(self, task: str, *, context: dict[str, Any] | None = None) -> None:
        """Send a task to the backend.

        The backend streams events via the ``subscribe`` callback.
        The call returns without blocking — the backend emits events
        asynchronously until the task is done or failed.
        """

    @abstractmethod
    async def resume(self, task_id: str, answer: str) -> None:
        """Submit an answer to a yielded question and continue the task."""

    def subscribe(self, handler: Callable[[AgentEvent], None]) -> None:
        """Register to receive AgentEvents from this backend."""
        self._handlers.append(handler)

    def unsubscribe(self, handler: Callable[[AgentEvent], None]) -> None:
        self._handlers.remove(handler)

    def _emit(self, event: AgentEvent) -> None:
        for h in self._handlers:
            h(event)


def map_session_event_to_status(session_event_type: str) -> AgentEventStatus | None:
    """Translate a SessionEvent.type string to an AgentEventStatus, or None to skip."""
    mapping = {
        "agent_started": AgentEventStatus.STARTED,
        "agent_completed": AgentEventStatus.COMPLETED,
        "agent_failed": AgentEventStatus.FAILED,
        "agent_yielded": AgentEventStatus.YIELDED,
        "supervisor": None,  # supervisor events are not agent events
        "session_stopped": None,
        "user_reply": None,
    }
    return mapping.get(session_event_type)


@dataclass
class BackendConfig:
    """Configuration for a single agent backend."""

    type: str = "openai"  # "pi" | "opencode" | "openai" | "custom"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout: int = 300
    system_prompt: str | None = None
    # For "custom" type
    command: str | list[str] | None = None
    # For "pi" type
    python_path: str = "python3"
    script_module: str = "apps.host.src.main"
    workspace: str | None = None
    env: dict[str, str] | None = None
    # Arbitrary extra config passed through to the backend
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackendFactory:
    """Creates ``AgentBackend`` instances from a ``BackendConfig``."""

    backends: dict[str, type[AgentBackend]] = field(default_factory=dict)

    def register(self, name: str, cls: type[AgentBackend]) -> None:
        self.backends[name] = cls

    def create(self, role: str, config: BackendConfig) -> AgentBackend:
        cls = self.backends.get(config.type)
        if cls is None:
            available = ", ".join(sorted(self.backends.keys()))
            raise ValueError(
                f"Unknown backend type {config.type!r}. Available: {available}"
            )
        return cls(role=role, config=config)