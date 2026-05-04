from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentEventStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    YIELDED = "yielded"
    ANSWER = "answer"


@dataclass
class AgentEvent:
    agent_id: str
    task_id: str
    role: str
    status: AgentEventStatus
    result: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
