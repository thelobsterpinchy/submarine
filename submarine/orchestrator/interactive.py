from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from submarine.agents.base import AgentRunContext
from submarine.agents.llm import LLMClient
from submarine.core.types import Task, TaskResult
from submarine.events.types import AgentEvent, AgentEventStatus
from submarine.orchestrator.core import Orchestrator, RunState


SessionEventType = Literal[
    "supervisor",
    "agent_started",
    "agent_yielded",
    "agent_completed",
    "agent_failed",
    "session_stopped",
    "user_reply",
]


@dataclass
class SessionEvent:
    type: SessionEventType
    message: str
    task_id: str | None = None
    agent_id: str | None = None
    role: str | None = None
    result: str | None = None
    error: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SupervisorResponse:
    text: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class ConversationTurn(TypedDict):
    role: Literal["user", "supervisor", "agent"]
    text: str
    agent_role: str | None


@dataclass
class PendingQuestion:
    task_id: str
    agent_id: str
    role: str
    question: str
    artifacts: dict[str, Any] = field(default_factory=dict)


SupervisorBrain = Callable[["SupervisorContext"], Awaitable[SupervisorResponse]]


@dataclass
class _ConversationCommand:
    message: str
    future: asyncio.Future[SupervisorResponse] | None = None
    target_task_id: str | None = None


@dataclass
class _SubmitCommand:
    message: str
    target_task_id: str | None = None
    future: asyncio.Future[None] | None = None


class _StopCommand:
    pass


class SupervisorContext:
    def __init__(
        self,
        session: "OrchestratorSession",
        message: str,
        *,
        trigger: str = "user",
        latest_event: SessionEvent | None = None,
    ) -> None:
        self._session = session
        self.current_message = message
        self.shared_memory = session.shared_memory
        self.trigger = trigger
        self.latest_event = latest_event

    @property
    def conversation(self) -> list[ConversationTurn]:
        return list(self._session._conversation)

    @property
    def pending_questions(self) -> list[PendingQuestion]:
        return [
            PendingQuestion(
                task_id=event.task_id,
                agent_id=event.agent_id,
                role=event.role,
                question=event.result or f"{event.role} needs input.",
                artifacts=dict(event.artifacts),
            )
            for event in self._session._waiting_questions.values()
        ]

    @property
    def completed_results(self) -> list[TaskResult]:
        return list(self._session._results)

    async def reply(
        self,
        text: str,
        *,
        artifacts: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SupervisorResponse:
        return SupervisorResponse(text=text, artifacts=artifacts or {}, metadata=metadata or {})

    async def delegate(
        self,
        message: str,
        *,
        role: str | None = None,
        parent_id: str | None = None,
        context: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> list[Task]:
        task_context = dict(context or {})
        task_metadata = dict(metadata or {})
        if model is not None:
            task_metadata["model"] = model

        if role is not None:
            task = Task(
                id=str(uuid.uuid4()),
                role=role,
                description=message,
                parent_id=parent_id,
                context=task_context,
                metadata=task_metadata,
            )
            await self._session._spawn_tasks([task])
            return [task]

        plan = self._session.orchestrator.planner(message, self._session.orchestrator.agents, self._session.shared_memory)
        planned = [
            Task(
                id=subtask.id,
                role=subtask.role,
                description=subtask.description,
                parent_id=subtask.parent_id,
                context={**subtask.context, **task_context},
                metadata={**subtask.metadata, **task_metadata},
            )
            for subtask in plan.initial_subtasks
        ]
        await self._session._spawn_tasks(planned)
        return planned

    async def answer(
        self,
        message: str,
        *,
        target_task_id: str | None = None,
    ) -> PendingQuestion | None:
        return await self._session._answer_waiting_question(message, target_task_id=target_task_id)


class OrchestratorSession:
    def __init__(
        self,
        orchestrator: "InteractiveOrchestrator",
        *,
        shared_memory: dict[str, Any] | None = None,
        supervisor_brain: SupervisorBrain | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.shared_memory = shared_memory or {}
        self.supervisor_brain = supervisor_brain or orchestrator.supervisor_brain or default_supervisor_brain
        self.events: asyncio.Queue[SessionEvent] = asyncio.Queue()
        self._commands: asyncio.Queue[_SubmitCommand | _ConversationCommand | _StopCommand] = asyncio.Queue()
        self._pending: dict[str, asyncio.Task[TaskResult | None]] = {}
        self._results: list[TaskResult] = []
        self._agent_events: list[AgentEvent] = []
        self._waiting_questions: dict[str, AgentEvent] = {}
        self._task_index: dict[str, Task] = {}
        self._conversation: list[ConversationTurn] = []
        self._state = RunState(
            root_task="interactive-session",
            completed_results=self._results,
            events=self._agent_events,
            shared_memory=self.shared_memory,
        )
        self._context = AgentRunContext(event_bus=self.orchestrator.event_bus, shared_memory=self.shared_memory)
        self._runner: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self._event_cursor = self.orchestrator.event_bus.cursor()

    async def start(self) -> "OrchestratorSession":
        if self._runner is None:
            self._runner = asyncio.create_task(self._run(), name="submarine:interactive-session")
        return self

    async def submit(self, message: str, *, target_task_id: str | None = None) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        await self._commands.put(_SubmitCommand(message=message, target_task_id=target_task_id, future=future))
        await future

    async def converse(self, message: str, *, target_task_id: str | None = None) -> SupervisorResponse:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[SupervisorResponse] = loop.create_future()
        await self._commands.put(_ConversationCommand(message=message, future=future, target_task_id=target_task_id))
        return await future

    async def next_event(self, timeout: float | None = None) -> SessionEvent:
        if timeout is None:
            return await self.events.get()
        return await asyncio.wait_for(self.events.get(), timeout=timeout)

    async def stop(self) -> None:
        await self._commands.put(_StopCommand())
        await self.wait_closed()

    async def wait_closed(self) -> None:
        await self._closed.wait()
        if self._runner is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner

    def snapshot(self) -> dict[str, Any]:
        return {
            "pending_task_ids": list(self._pending.keys()),
            "waiting_task_ids": list(self._waiting_questions.keys()),
            "completed_results": len(self._results),
            "conversation_turns": len(self._conversation),
        }

    async def _run(self) -> None:
        event_waiter: asyncio.Task[tuple[AgentEvent, int]] | None = None
        command_waiter: asyncio.Task[_SubmitCommand | _ConversationCommand | _StopCommand] | None = None
        try:
            while True:
                if command_waiter is None:
                    command_waiter = asyncio.create_task(self._commands.get())
                if event_waiter is None and self._tracked_task_ids():
                    event_waiter = asyncio.create_task(
                        self.orchestrator.event_bus.wait_for_from(
                            self._event_cursor,
                            lambda event: event.task_id in self._tracked_task_ids(),
                        )
                    )

                waiters: list[asyncio.Task[Any]] = [command_waiter]
                if event_waiter is not None:
                    waiters.append(event_waiter)

                done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)

                if command_waiter in done:
                    command = command_waiter.result()
                    command_waiter = None
                    if isinstance(command, _StopCommand):
                        break
                    if isinstance(command, _ConversationCommand):
                        await self._handle_conversation(command)
                    else:
                        await self._handle_submit(command)

                if event_waiter is not None and event_waiter in done:
                    event, self._event_cursor = event_waiter.result()
                    event_waiter = None
                    await self._handle_agent_event(event)
        finally:
            if event_waiter is not None:
                event_waiter.cancel()
            if command_waiter is not None:
                command_waiter.cancel()
            for task in self._pending.values():
                task.cancel()
            for task in self._pending.values():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await self.events.put(SessionEvent(type="session_stopped", message="Interactive session stopped."))
            self._closed.set()

    def _tracked_task_ids(self) -> set[str]:
        return set(self._pending) | set(self._waiting_questions)

    async def _handle_conversation(self, command: _ConversationCommand) -> None:
        self._conversation.append({"role": "user", "text": command.message, "agent_role": None})

        if command.target_task_id is not None:
            question = await self._answer_waiting_question(command.message, target_task_id=command.target_task_id)
            if question is not None:
                response = SupervisorResponse(text=f"Got it. I passed that to {question.role}.")
                await self._emit_supervisor_response(response)
                if command.future is not None and not command.future.done():
                    command.future.set_result(response)
                return

        ctx = SupervisorContext(self, command.message, trigger="user")
        response = await self.supervisor_brain(ctx)
        await self._emit_supervisor_response(response)
        if command.future is not None and not command.future.done():
            command.future.set_result(response)

    async def _handle_submit(self, command: _SubmitCommand) -> None:
        handled = await self._answer_waiting_question(command.message, target_task_id=command.target_task_id)
        if handled is None:
            plan = self.orchestrator.planner(command.message, self.orchestrator.agents, self.shared_memory)
            await self._spawn_tasks(plan.initial_subtasks)
            await self.events.put(
                SessionEvent(
                    type="supervisor",
                    message=f"Started {len(plan.initial_subtasks)} task(s) for: {command.message}",
                    metadata={"task_count": len(plan.initial_subtasks), "plan": plan.metadata},
                )
            )
        else:
            await self.events.put(
                SessionEvent(
                    type="user_reply",
                    message=f"Sent answer to {handled.role} for task {handled.task_id}.",
                    task_id=handled.task_id,
                    agent_id=handled.agent_id,
                    role=handled.role,
                )
            )
        if command.future is not None and not command.future.done():
            command.future.set_result(None)

    async def _answer_waiting_question(self, message: str, *, target_task_id: str | None = None) -> PendingQuestion | None:
        if target_task_id is not None:
            question = self._waiting_questions.pop(target_task_id, None)
        elif len(self._waiting_questions) == 1:
            target_task_id = next(iter(self._waiting_questions.keys()))
            question = self._waiting_questions.pop(target_task_id, None)
        else:
            question = None

        if question is None:
            return None

        await self.orchestrator.event_bus.publish(
            AgentEvent(
                agent_id=question.agent_id,
                task_id=question.task_id,
                role=question.role,
                status=AgentEventStatus.ANSWER,
                result=message,
            )
        )
        return PendingQuestion(
            task_id=question.task_id,
            agent_id=question.agent_id,
            role=question.role,
            question=question.result or "",
            artifacts=dict(question.artifacts),
        )

    async def _spawn_tasks(self, tasks: list[Task]) -> None:
        for subtask in tasks:
            task = self.orchestrator._spawn_subtask(subtask, self._context)
            self._pending[subtask.id] = task
            self._task_index[subtask.id] = subtask

    async def _emit_supervisor_response(self, response: SupervisorResponse) -> SessionEvent:
        self._conversation.append({"role": "supervisor", "text": response.text, "agent_role": None})
        event = SessionEvent(
            type="supervisor",
            message=response.text,
            artifacts=response.artifacts,
            metadata=response.metadata,
        )
        await self.events.put(event)
        return event

    async def _handle_agent_event(self, event: AgentEvent) -> None:
        self._agent_events.append(event)
        if event.status == AgentEventStatus.STARTED:
            agent_event = SessionEvent(
                type="agent_started",
                message=f"{event.role} started task {event.task_id}.",
                task_id=event.task_id,
                agent_id=event.agent_id,
                role=event.role,
                metadata=event.metadata,
            )
            await self.events.put(agent_event)
            return

        if event.status == AgentEventStatus.YIELDED:
            self._waiting_questions[event.task_id] = event
            self._conversation.append({"role": "agent", "text": event.result or f"{event.role} needs input.", "agent_role": event.role})
            agent_event = SessionEvent(
                type="agent_yielded",
                message=event.result or f"{event.role} needs input.",
                task_id=event.task_id,
                agent_id=event.agent_id,
                role=event.role,
                artifacts=event.artifacts,
                metadata=event.metadata,
            )
            await self.events.put(agent_event)
            await self._maybe_proactive_followup(agent_event)
            return

        if event.status == AgentEventStatus.COMPLETED:
            pending_task = self._pending.pop(event.task_id, None)
            result = await self._collect_result(pending_task)
            if result is not None:
                self._results.append(result)
                self._conversation.append({"role": "agent", "text": result.output, "agent_role": result.role})
            new_tasks = self.orchestrator.completion_hook(event, self.orchestrator) if self.orchestrator.completion_hook else []
            await self._spawn_tasks(list(new_tasks or []))
            agent_event = SessionEvent(
                type="agent_completed",
                message=f"{event.role} completed task {event.task_id}.",
                task_id=event.task_id,
                agent_id=event.agent_id,
                role=event.role,
                result=event.result,
                artifacts=event.artifacts,
                metadata=event.metadata,
            )
            await self.events.put(agent_event)
            await self._maybe_proactive_followup(agent_event)
            return

        if event.status == AgentEventStatus.FAILED:
            pending_task = self._pending.pop(event.task_id, None)
            await self._collect_result(pending_task)
            new_tasks = self.orchestrator.completion_hook(event, self.orchestrator) if self.orchestrator.completion_hook else []
            await self._spawn_tasks(list(new_tasks or []))
            agent_event = SessionEvent(
                type="agent_failed",
                message=f"{event.role} failed task {event.task_id}.",
                task_id=event.task_id,
                agent_id=event.agent_id,
                role=event.role,
                error=event.error,
                artifacts=event.artifacts,
                metadata=event.metadata,
            )
            await self.events.put(agent_event)
            await self._maybe_proactive_followup(agent_event)

    async def _maybe_proactive_followup(self, latest_event: SessionEvent) -> None:
        if latest_event.type not in {"agent_yielded", "agent_completed", "agent_failed"}:
            return
        if self.supervisor_brain is None:
            return

        message = latest_event.message
        if latest_event.type == "agent_completed" and latest_event.result:
            message = f"{latest_event.message} Result: {latest_event.result}"
        elif latest_event.type == "agent_failed" and latest_event.error:
            message = f"{latest_event.message} Error: {latest_event.error}"

        ctx = SupervisorContext(self, message, trigger="agent_event", latest_event=latest_event)
        response = await self.supervisor_brain(ctx)
        if response.text.strip():
            await self._emit_supervisor_response(response)

    async def _collect_result(self, task: asyncio.Task[TaskResult | None] | None) -> TaskResult | None:
        if task is None:
            return None
        try:
            return await task
        except Exception:
            return None


async def default_supervisor_brain(ctx: SupervisorContext) -> SupervisorResponse:
    if ctx.trigger == "agent_event" and ctx.latest_event is not None:
        pending_roles = sorted({question.role for question in ctx.pending_questions})
        if ctx.latest_event.type == "agent_yielded":
            return await ctx.reply(ctx.latest_event.message)
        if ctx.latest_event.type == "agent_completed":
            if pending_roles:
                return await ctx.reply(
                    f"{ctx.latest_event.role} finished. Still waiting on: {', '.join(pending_roles)}."
                )
            return await ctx.reply(f"{ctx.latest_event.role} finished.")
        if ctx.latest_event.type == "agent_failed":
            return await ctx.reply(
                f"{ctx.latest_event.role} failed{': ' + ctx.latest_event.error if ctx.latest_event.error else '.'}"
            )

    if ctx.pending_questions:
        if len(ctx.pending_questions) == 1:
            answered = await ctx.answer(ctx.current_message)
            if answered is not None:
                return await ctx.reply(f"Got it. I passed that to {answered.role}.")
        names = ", ".join(question.role for question in ctx.pending_questions)
        return await ctx.reply(
            f"I have pending questions from: {names}. Reply again with a target task id if you want to answer a specific one."
        )

    delegated = await ctx.delegate(ctx.current_message)
    if not delegated:
        return await ctx.reply("I couldn't find a suitable subagent for that.")

    roles = ", ".join(task.role for task in delegated)
    return await ctx.reply(f"On it. I started {len(delegated)} task(s): {roles}.")


def _format_supervisor_prompt(ctx: SupervisorContext, available_roles: list[str]) -> str:
    pending_questions = [
        {
            "task_id": question.task_id,
            "agent_id": question.agent_id,
            "role": question.role,
            "question": question.question,
            "artifacts": question.artifacts,
        }
        for question in ctx.pending_questions
    ]
    completed_results = [
        {
            "task_id": result.task_id,
            "agent_id": result.agent_id,
            "role": result.role,
            "output": result.output,
            "artifacts": result.artifacts,
            "metadata": result.metadata,
        }
        for result in ctx.completed_results[-5:]
    ]
    conversation = ctx.conversation[-12:]

    latest_event = None
    if ctx.latest_event is not None:
        latest_event = {
            "type": ctx.latest_event.type,
            "message": ctx.latest_event.message,
            "task_id": ctx.latest_event.task_id,
            "agent_id": ctx.latest_event.agent_id,
            "role": ctx.latest_event.role,
            "result": ctx.latest_event.result,
            "error": ctx.latest_event.error,
            "metadata": ctx.latest_event.metadata,
        }

    return (
        "You are an orchestration supervisor for a team of live subagents. "
        "Your job is to stay conversational, decide whether to answer directly, delegate work, or answer a waiting agent question.\n\n"
        "Return ONLY valid JSON with this schema:\n"
        "{\n"
        '  "action": "reply" | "delegate" | "answer",\n'
        '  "reply": "string for the user",\n'
        '  "delegate_message": "string, required when action=delegate",\n'
        '  "delegate_role": "string or null, optional preferred role",\n'
        '  "delegate_model": "string or null, optional model override for delegated subtask",\n'
        '  "answer_message": "string, required when action=answer",\n'
        '  "target_task_id": "string or null"\n'
        "}\n\n"
        "Rules:\n"
        "- Use action=reply when you can answer directly without spawning work.\n"
        "- Use action=delegate when a subagent should do the work.\n"
        "- Use action=answer only if the user message is clearly answering a waiting subagent question.\n"
        "- Keep reply concise and conversational.\n"
        "- If you delegate, still include a short user-facing reply.\n"
        "- Prefer one role from available_roles when delegate_role is set.\n"
        "- If trigger=agent_event, you may proactively update the user instead of delegating more work.\n\n"
        f"trigger = {json.dumps(ctx.trigger)}\n"
        f"available_roles = {json.dumps(available_roles)}\n"
        f"pending_questions = {json.dumps(pending_questions)}\n"
        f"completed_results = {json.dumps(completed_results)}\n"
        f"recent_conversation = {json.dumps(conversation)}\n"
        f"latest_event = {json.dumps(latest_event)}\n"
        f"current_user_message = {json.dumps(ctx.current_message)}\n"
    )


def make_llm_supervisor_brain(
    client: LLMClient,
    *,
    system_prompt: str | None = None,
    model: str | None = None,
    max_tokens: int = 800,
    temperature: float = 0.2,
) -> SupervisorBrain:
    async def _brain(ctx: SupervisorContext) -> SupervisorResponse:
        prompt = _format_supervisor_prompt(ctx, list(ctx._session.orchestrator.agents.keys()))
        raw = await client.complete(
            prompt,
            system_prompt=system_prompt,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return await ctx.reply(raw.strip())

        action = str(data.get("action", "reply")).strip().lower()
        reply = str(data.get("reply", "")).strip()

        if action == "answer":
            answer_message = str(data.get("answer_message", ctx.current_message)).strip() or ctx.current_message
            target_task_id = data.get("target_task_id")
            answered = await ctx.answer(answer_message, target_task_id=target_task_id if isinstance(target_task_id, str) else None)
            if answered is not None:
                if reply:
                    return await ctx.reply(reply)
                return await ctx.reply(f"Got it. I passed that to {answered.role}.")
            return await ctx.reply(reply or "I couldn't match that answer to a waiting agent question.")

        if action == "delegate":
            delegate_message = str(data.get("delegate_message", ctx.current_message)).strip() or ctx.current_message
            delegate_role = data.get("delegate_role")
            delegate_model = data.get("delegate_model")
            role = delegate_role if isinstance(delegate_role, str) and delegate_role else None
            model_override = delegate_model if isinstance(delegate_model, str) and delegate_model else None
            delegated = await ctx.delegate(delegate_message, role=role, model=model_override)
            if delegated:
                return await ctx.reply(reply or f"On it. I started {len(delegated)} task(s).")
            return await ctx.reply(reply or "I couldn't find a suitable subagent for that.")

        return await ctx.reply(reply or raw.strip())

    return _brain


class InteractiveOrchestrator(Orchestrator):
    def __init__(
        self,
        agents: dict[str, Any],
        *,
        supervisor_brain: SupervisorBrain | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(agents, **kwargs)
        self.supervisor_brain = supervisor_brain

    async def start_session(
        self,
        *,
        shared_memory: dict[str, Any] | None = None,
        supervisor_brain: SupervisorBrain | None = None,
    ) -> OrchestratorSession:
        session = OrchestratorSession(self, shared_memory=shared_memory, supervisor_brain=supervisor_brain)
        return await session.start()
