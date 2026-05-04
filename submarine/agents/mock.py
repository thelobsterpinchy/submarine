from __future__ import annotations

import asyncio

from submarine.agents.base import Agent, AgentRunContext
from submarine.core.types import Task, TaskResult


async def echo_handler(task: Task, context: AgentRunContext) -> TaskResult:
    await asyncio.sleep(0.01)
    return TaskResult(
        task_id=task.id,
        agent_id=f"mock-{task.role}",
        role=task.role,
        output=f"Handled by {task.role}: {task.description}",
        artifacts={"context_keys": sorted(task.context.keys())},
    )


def make_mock_agent(role: str) -> Agent:
    return Agent(role=role, handler=echo_handler, model="mock")
