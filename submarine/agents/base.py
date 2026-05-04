from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from submarine.core.types import Task, TaskResult
from submarine.events.bus import EventBus
from submarine.events.types import AgentEvent, AgentEventStatus


AgentHandler = Callable[[Task, "AgentRunContext"], Awaitable[TaskResult]]


@dataclass
class AgentRunContext:
    event_bus: EventBus
    shared_memory: dict[str, Any] = field(default_factory=dict)
    orchestrator_metadata: dict[str, Any] = field(default_factory=dict)

    async def ask(
        self,
        question: str,
        task_id: str,
        agent_id: str,
        role: str,
        *,
        artifacts: dict[str, Any] | None = None,
    ) -> str:
        """Emit a 'yielded' event and wait for an 'answer' event to be injected."""
        await self.event_bus.publish(
            AgentEvent(
                agent_id=agent_id,
                task_id=task_id,
                role=role,
                status=AgentEventStatus.YIELDED,
                result=question,
                artifacts=artifacts or {},
            )
        )
        answer_evt = await self.event_bus.wait_for(
            lambda e: e.task_id == task_id and e.agent_id == agent_id and e.status == AgentEventStatus.ANSWER,
            timeout=None,
        )
        return answer_evt.result or ""


class Agent:
    def __init__(
        self,
        role: str,
        handler: AgentHandler,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        timeout: float = 300,
        max_tokens: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.role = role
        self.handler = handler
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.metadata = metadata or {}

    def spawn(self, task: Task, context: AgentRunContext) -> asyncio.Task[TaskResult | None]:
        return asyncio.create_task(self._run(task, context), name=f"submarine:{self.role}:{task.id}")

    async def _run(self, task: Task, context: AgentRunContext) -> TaskResult | None:
        agent_id = str(uuid.uuid4())
        await context.event_bus.publish(
            AgentEvent(agent_id=agent_id, task_id=task.id, role=self.role, status=AgentEventStatus.STARTED)
        )
        try:
            result = await asyncio.wait_for(self.handler(task, context), timeout=self.timeout)
            await context.event_bus.publish(
                AgentEvent(
                    agent_id=agent_id,
                    task_id=task.id,
                    role=self.role,
                    status=AgentEventStatus.COMPLETED,
                    result=result.output,
                    artifacts=result.artifacts,
                    metadata=result.metadata,
                )
            )
            return result
        except asyncio.TimeoutError as exc:
            await context.event_bus.publish(
                AgentEvent(
                    agent_id=agent_id,
                    task_id=task.id,
                    role=self.role,
                    status=AgentEventStatus.FAILED,
                    error=f"Agent timed out after {self.timeout} seconds",
                )
            )
            raise exc
        except Exception as exc:
            await context.event_bus.publish(
                AgentEvent(
                    agent_id=agent_id,
                    task_id=task.id,
                    role=self.role,
                    status=AgentEventStatus.FAILED,
                    error=str(exc),
                )
            )
            raise