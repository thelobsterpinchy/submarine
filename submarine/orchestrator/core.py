from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from submarine.agents.base import Agent, AgentRunContext
from submarine.core.types import AggregatedResult, Plan, Task, TaskResult
from submarine.events.bus import EventBus
from submarine.events.types import AgentEvent, AgentEventStatus


Planner = Callable[[str, dict[str, Agent], dict[str, Any]], Plan]
Aggregator = Callable[[str, list[TaskResult], list[AgentEvent]], AggregatedResult]
Router = Callable[[Task, dict[str, Agent]], Agent]
CompletionHook = Callable[[AgentEvent, "Orchestrator"], Optional[list[Task]]]


@dataclass
class RunState:
    root_task: str
    completed_results: list[TaskResult]
    events: list[AgentEvent]
    shared_memory: dict[str, Any]


class Orchestrator:
    def __init__(
        self,
        agents: dict[str, Agent],
        *,
        event_bus: EventBus | None = None,
        planner: Planner | None = None,
        aggregator: Aggregator | None = None,
        router: Router | None = None,
        completion_hook: CompletionHook | None = None,
    ) -> None:
        self.agents = dict(agents)
        self.event_bus = event_bus or EventBus()
        self.planner = planner or self._default_planner
        self.aggregator = aggregator or self._default_aggregator
        self.router = router or self._default_router
        self.completion_hook = completion_hook

    def register(self, role: str, agent: Agent) -> None:
        self.agents[role] = agent

    async def run(self, task: str, *, shared_memory: dict[str, Any] | None = None) -> AggregatedResult:
        state = RunState(root_task=task, completed_results=[], events=[], shared_memory=shared_memory or {})
        plan = self.planner(task, self.agents, state.shared_memory)

        pending: dict[str, asyncio.Task[TaskResult | None]] = {}
        task_index: dict[str, Task] = {}
        context = AgentRunContext(event_bus=self.event_bus, shared_memory=state.shared_memory)

        for subtask in plan.initial_subtasks:
            spawned = self._spawn_subtask(subtask, context)
            pending[subtask.id] = spawned
            task_index[subtask.id] = subtask

        while pending:
            event = await self.event_bus.wait_for(
                lambda e: e.task_id in pending and e.status in {AgentEventStatus.COMPLETED, AgentEventStatus.FAILED}
            )
            state.events.append(event)

            finished = pending.pop(event.task_id)
            result = await asyncio.gather(finished, return_exceptions=True)
            item = result[0]
            if isinstance(item, TaskResult):
                state.completed_results.append(item)

            if self.completion_hook is not None:
                new_tasks = self.completion_hook(event, self) or []
                for new_task in new_tasks:
                    spawned = self._spawn_subtask(new_task, context)
                    pending[new_task.id] = spawned
                    task_index[new_task.id] = new_task

        return self.aggregator(task, state.completed_results, state.events)

    def _spawn_subtask(self, task: Task, context: AgentRunContext) -> asyncio.Task[TaskResult | None]:
        agent = self.router(task, self.agents)
        return agent.spawn(task, context)

    @staticmethod
    def _default_planner(task: str, agents: dict[str, Agent], shared_memory: dict[str, Any]) -> Plan:
        if not agents:
            raise ValueError("No agents registered")
        first_role = next(iter(agents.keys()))
        return Plan(initial_subtasks=[Task(id=str(uuid.uuid4()), role=first_role, description=task)])

    @staticmethod
    def _default_router(task: Task, agents: dict[str, Agent]) -> Agent:
        if task.role in agents:
            return agents[task.role]
        if agents:
            return next(iter(agents.values()))
        raise ValueError(f"No agent available for role {task.role!r}")

    @staticmethod
    def _default_aggregator(task: str, results: list[TaskResult], events: list[AgentEvent]) -> AggregatedResult:
        summary_parts = []
        artifact_bag: dict[str, Any] = {"events": [e.status.value for e in events]}
        for result in results:
            summary_parts.append(f"[{result.role}] {result.output}")
            if result.artifacts:
                artifact_bag.setdefault(result.role, []).append(result.artifacts)
        summary = "\n\n".join(summary_parts) if summary_parts else "No successful subagent results."
        return AggregatedResult(task=task, summary=summary, subresults=results, artifacts=artifact_bag)
