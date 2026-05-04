from __future__ import annotations

import asyncio
import uuid
from typing import Any

from submarine.agents.base import Agent, AgentRunContext
from submarine.agents.backends.base import AgentBackend
from submarine.core.types import Task, TaskResult
from submarine.events.types import AgentEvent, AgentEventStatus


class BackendAgent(Agent):
    """Adapter that lets an ``AgentBackend`` participate as a normal Submarine Agent."""

    def __init__(
        self,
        role: str,
        backend: AgentBackend,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        timeout: float = 300,
        max_tokens: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.backend = backend
        super().__init__(
            role=role,
            handler=self._backend_handler,
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            timeout=timeout,
            max_tokens=max_tokens,
            metadata=metadata,
        )

    async def _backend_handler(self, task: Task, context: AgentRunContext) -> TaskResult:
        completion: asyncio.Future[TaskResult] = asyncio.get_running_loop().create_future()
        current_agent_id = f"backend-{self.role}-{uuid.uuid4().hex[:8]}"

        def handler(event: AgentEvent) -> None:
            if event.role != self.role or event.task_id != task.id:
                return

            asyncio.create_task(context.event_bus.publish(event))

            if completion.done():
                return

            if event.status == AgentEventStatus.COMPLETED:
                completion.set_result(
                    TaskResult(
                        task_id=task.id,
                        agent_id=event.agent_id or current_agent_id,
                        role=self.role,
                        output=event.result or "",
                        artifacts=event.artifacts,
                        metadata=event.metadata,
                    )
                )
            elif event.status == AgentEventStatus.FAILED:
                completion.set_exception(RuntimeError(event.error or f"{self.role} backend failed"))

        self.backend.subscribe(handler)
        try:
            await self.backend.run(task.description, context={**task.context, "task_id": task.id, **task.metadata})
            return await asyncio.wait_for(completion, timeout=self.timeout)
        finally:
            self.backend.unsubscribe(handler)
