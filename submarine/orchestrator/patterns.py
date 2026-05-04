from __future__ import annotations

import uuid
from collections.abc import Callable

from submarine.core.types import Plan, Task
from submarine.events.types import AgentEvent, AgentEventStatus


def simple_coding_planner(task: str, available_roles: list[str]) -> Plan:
    subtasks: list[Task] = []
    lowered = task.lower()

    if "test" in lowered and "tester" in available_roles:
        subtasks.append(Task(id=str(uuid.uuid4()), role="tester", description=f"Write or run tests for: {task}"))
    if "research" in lowered and "researcher" in available_roles:
        subtasks.append(Task(id=str(uuid.uuid4()), role="researcher", description=f"Research supporting info for: {task}"))
    if "code" in lowered or "build" in lowered or not subtasks:
        role = "coder" if "coder" in available_roles else available_roles[0]
        subtasks.insert(0, Task(id=str(uuid.uuid4()), role=role, description=task))

    return Plan(initial_subtasks=subtasks)


def respawn_on_failure(role: str) -> Callable[[AgentEvent, object], list[Task]]:
    def _hook(event: AgentEvent, orchestrator: object) -> list[Task]:
        if event.status != AgentEventStatus.FAILED:
            return []
        return [
            Task(
                id=str(uuid.uuid4()),
                role=role,
                description=f"Retry failed task {event.task_id}. Original error: {event.error or 'unknown'}",
                parent_id=event.task_id,
            )
        ]

    return _hook
