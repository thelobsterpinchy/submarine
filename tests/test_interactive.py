from __future__ import annotations

import asyncio

from submarine import InteractiveOrchestrator, SupervisorResponse, make_llm_supervisor_brain
from submarine.agents import openai as openai_agent_module
from submarine.agents.base import Agent, AgentRunContext
from submarine.agents.openai import make_openai_agent
from submarine.core.types import Task, TaskResult


async def _slow_handler(task: Task, context: AgentRunContext) -> TaskResult:
    await asyncio.sleep(0.1)
    return TaskResult(task_id=task.id, agent_id="slow-agent", role=task.role, output=f"done:{task.description}")


async def _asking_handler(task: Task, context: AgentRunContext) -> TaskResult:
    answer = await context.ask("Need clarification", task.id, "asking-agent", task.role)
    await asyncio.sleep(0.01)
    return TaskResult(task_id=task.id, agent_id="asking-agent", role=task.role, output=f"answer:{answer}")


async def _chatty_brain(ctx) -> SupervisorResponse:
    lowered = ctx.current_message.lower()
    if "hello" in lowered:
        return await ctx.reply("Hey, I'm here.")
    delegated = await ctx.delegate(ctx.current_message, role="coder")
    return await ctx.reply(f"I kicked this to {delegated[0].role}.")


class _FakeLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.calls: list[dict] = []

    async def complete(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_interactive_session_starts_and_completes_work() -> None:
    async def _run() -> None:
        orchestrator = InteractiveOrchestrator(agents={"coder": Agent(role="coder", handler=_slow_handler)})
        session = await orchestrator.start_session()

        response = await session.converse("build something")
        assert response.text == "On it. I started 1 task(s): coder."

        first = await session.next_event(timeout=1)
        assert first.type == "supervisor"
        assert "On it." in first.message

        started = await session.next_event(timeout=1)
        assert started.type == "agent_started"
        assert started.role == "coder"

        completed = await session.next_event(timeout=1)
        assert completed.type == "agent_completed"
        assert completed.result == "done:build something"

        await session.stop()

    asyncio.run(_run())


def test_session_stays_responsive_while_subagent_runs() -> None:
    async def _run() -> None:
        orchestrator = InteractiveOrchestrator(
            agents={"coder": Agent(role="coder", handler=_slow_handler)},
            supervisor_brain=_chatty_brain,
        )
        session = await orchestrator.start_session()

        reply = await session.converse("first task")
        assert reply.text == "I kicked this to coder."
        await session.next_event(timeout=1)
        await session.next_event(timeout=1)

        second = await session.converse("hello while you work")
        assert second.text == "Hey, I'm here."

        response_event = await session.next_event(timeout=0.05)
        assert response_event.type == "supervisor"
        assert response_event.message == "Hey, I'm here."

        completed_messages = []
        while len(completed_messages) < 1:
            event = await session.next_event(timeout=1)
            if event.type == "agent_completed":
                completed_messages.append(event.result)

        assert completed_messages == ["done:first task"]
        await session.stop()

    asyncio.run(_run())


def test_session_can_answer_yielded_agent_question() -> None:
    async def _run() -> None:
        orchestrator = InteractiveOrchestrator(agents={"researcher": Agent(role="researcher", handler=_asking_handler)})
        session = await orchestrator.start_session()

        first = await session.converse("research this")
        assert first.text == "On it. I started 1 task(s): researcher."
        await session.next_event(timeout=1)
        await session.next_event(timeout=1)
        yielded = await session.next_event(timeout=1)
        assert yielded.type == "agent_yielded"
        assert yielded.message == "Need clarification"

        followup = await session.next_event(timeout=1)
        assert followup.type == "supervisor"
        assert followup.message == "Need clarification"

        answer = await session.converse("use postgres")
        assert answer.text == "Got it. I passed that to researcher."

        ack = await session.next_event(timeout=1)
        assert ack.type == "supervisor"
        assert "Got it." in ack.message

        completed = await session.next_event(timeout=1)
        assert completed.type == "agent_completed"
        assert completed.result == "answer:use postgres"

        await session.stop()

    asyncio.run(_run())


def test_supervisor_can_answer_directly_without_spawning() -> None:
    async def _run() -> None:
        orchestrator = InteractiveOrchestrator(
            agents={"coder": Agent(role="coder", handler=_slow_handler)},
            supervisor_brain=_chatty_brain,
        )
        session = await orchestrator.start_session()

        response = await session.converse("hello")
        assert response.text == "Hey, I'm here."
        assert session.snapshot()["pending_task_ids"] == []

        event = await session.next_event(timeout=1)
        assert event.type == "supervisor"
        assert event.message == "Hey, I'm here."

        await session.stop()

    asyncio.run(_run())


def test_llm_supervisor_brain_can_reply_directly() -> None:
    async def _run() -> None:
        client = _FakeLLMClient([
            '{"action":"reply","reply":"I can answer that directly."}'
        ])
        orchestrator = InteractiveOrchestrator(
            agents={"coder": Agent(role="coder", handler=_slow_handler)},
            supervisor_brain=make_llm_supervisor_brain(client),
        )
        session = await orchestrator.start_session()

        response = await session.converse("what's up?")
        assert response.text == "I can answer that directly."
        assert session.snapshot()["pending_task_ids"] == []
        assert "current_user_message" in client.prompts[0]

        await session.stop()

    asyncio.run(_run())


def test_llm_supervisor_brain_can_delegate_work() -> None:
    async def _run() -> None:
        client = _FakeLLMClient([
            '{"action":"delegate","reply":"I\'ll have the coder handle that.","delegate_message":"build api","delegate_role":"coder"}'
        ])
        orchestrator = InteractiveOrchestrator(
            agents={"coder": Agent(role="coder", handler=_slow_handler)},
            supervisor_brain=make_llm_supervisor_brain(client),
        )
        session = await orchestrator.start_session()

        response = await session.converse("please build api")
        assert response.text == "I'll have the coder handle that."
        assert len(session.snapshot()["pending_task_ids"]) == 1

        first = await session.next_event(timeout=1)
        assert first.type == "supervisor"
        second = await session.next_event(timeout=1)
        assert second.type == "agent_started"

        await session.stop()

    asyncio.run(_run())


def test_default_brain_sends_proactive_followup_on_completion() -> None:
    async def _run() -> None:
        orchestrator = InteractiveOrchestrator(agents={"coder": Agent(role="coder", handler=_slow_handler)})
        session = await orchestrator.start_session()

        await session.converse("build something")
        await session.next_event(timeout=1)
        await session.next_event(timeout=1)
        completed = await session.next_event(timeout=1)
        assert completed.type == "agent_completed"

        followup = await session.next_event(timeout=1)
        assert followup.type == "supervisor"
        assert followup.message == "coder finished."

        await session.stop()

    asyncio.run(_run())


def test_llm_supervisor_brain_receives_agent_event_trigger() -> None:
    async def _run() -> None:
        client = _FakeLLMClient([
            '{"action":"delegate","reply":"Starting coder.","delegate_message":"build api","delegate_role":"coder"}',
            '{"action":"reply","reply":"Coder wrapped up, checking if anything else is needed."}',
        ])
        orchestrator = InteractiveOrchestrator(
            agents={"coder": Agent(role="coder", handler=_slow_handler)},
            supervisor_brain=make_llm_supervisor_brain(client),
        )
        session = await orchestrator.start_session()

        await session.converse("please build api")
        await session.next_event(timeout=1)
        await session.next_event(timeout=1)
        await session.next_event(timeout=1)
        followup = await session.next_event(timeout=1)
        assert followup.type == "supervisor"
        assert followup.message == "Coder wrapped up, checking if anything else is needed."
        assert 'trigger = "agent_event"' in client.prompts[1]

        await session.stop()

    asyncio.run(_run())


def test_delegate_model_override_flows_to_subagent() -> None:
    async def _run() -> None:
        class FakeOpenAIClient:
            def __init__(self, *args, **kwargs) -> None:
                self.calls: list[dict] = []

            async def complete(self, prompt: str, **kwargs):
                self.calls.append({"prompt": prompt, **kwargs})
                return "built"

        original_client = openai_agent_module.LLMClient
        openai_agent_module.LLMClient = FakeOpenAIClient
        try:
            agent = make_openai_agent("coder", model="default-model")
            fake_client = agent.handler.__closure__[0].cell_contents

            async def brain(ctx) -> SupervisorResponse:
                delegated = await ctx.delegate("build api", role="coder", model="special-subagent-model")
                return await ctx.reply(f"Delegated to {delegated[0].role}.")

            orchestrator = InteractiveOrchestrator(agents={"coder": agent}, supervisor_brain=brain)
            session = await orchestrator.start_session()

            await session.converse("please build api")
            await session.next_event(timeout=1)
            await session.next_event(timeout=1)
            await session.next_event(timeout=1)
            assert fake_client.calls[0]["model"] == "special-subagent-model"
            assert agent.model == "default-model"

            await session.stop()
        finally:
            openai_agent_module.LLMClient = original_client

    asyncio.run(_run())
