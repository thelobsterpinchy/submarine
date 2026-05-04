from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


TaskRole = str
TaskStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class Task:
    id: str
    role: TaskRole
    description: str
    parent_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    task_id: str
    agent_id: str
    role: TaskRole
    output: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    initial_subtasks: list[Task]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregatedResult:
    task: str
    summary: str
    subresults: list[TaskResult] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
