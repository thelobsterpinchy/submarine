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
        instance_id = f"backend-{self.role}-{uuid.uuid4().hex[:8]}"
        task_id = task.id
        # Use a list so both nested handlers can mutate it without nonlocal issues
        _resumed = [False]

        def backend_event_handler(event: AgentEvent) -> None:
            if event.role != self.role or event.task_id != task_id:
                return

            asyncio.create_task(context.event_bus.publish(event))

            if completion.done():
                return

            if event.status == AgentEventStatus.COMPLETED:
                completion.set_result(
                    TaskResult(
                        task_id=task.id,
                        agent_id=event.agent_id or instance_id,
                        role=self.role,
                        output=event.result or "",
                        artifacts=event.artifacts,
                        metadata=event.metadata,
                    )
                )
            elif event.status == AgentEventStatus.FAILED:
                completion.set_exception(
                    RuntimeError(event.error or f"{self.role} backend failed")
                )

        async def answer_event_handler(event: AgentEvent) -> None:
            """Handle ANSWER events from the orchestrator's event bus.

            OrchestratorSession._answer_waiting_question publishes ANSWER events
            tagged with the role of the agent that asked the question.  The task_id
            guard ensures we only resume the specific task that is waiting.
            """
            if event.status != AgentEventStatus.ANSWER:
                return
            if event.task_id != task_id or event.role != self.role:
                return
            if _resumed[0]:
                return
            if completion.done():
                return
            _resumed[0] = True
            try:
                await self.backend.resume(event.task_id, event.result or "")
            except Exception as exc:
                completion.set_exception(RuntimeError(f"resume failed: {exc}"))

        self.backend.subscribe(backend_event_handler)
        # Subscribe to ANSWER events for our role from the orchestrator's event bus.
        # This is how OrchestratorSession delivers user replies to waiting tasks.
        context.event_bus.subscribe(self.role, answer_event_handler)

        try:
            await self.backend.run(
                task.description,
                context={**task.context, "task_id": task_id, **task.metadata},
            )
            return await asyncio.wait_for(completion, timeout=self.timeout)
        finally:
            self.backend.unsubscribe(backend_event_handler)
            context.event_bus.unsubscribe(self.role, answer_event_handler)