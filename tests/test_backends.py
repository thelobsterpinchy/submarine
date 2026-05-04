from __future__ import annotations

import asyncio
from pathlib import Path

from submarine import InteractiveOrchestrator
from submarine.agents.backend_agent import BackendAgent
from submarine.agents.backends import BackendConfig, CustomBridge
from submarine.events.types import AgentEventStatus


FIXTURE = Path(__file__).parent / "fixtures" / "custom_backend_fixture.py"


def test_custom_backend_completes_round_trip() -> None:
    async def _run() -> None:
        bridge = CustomBridge(
            "coder",
            BackendConfig(type="custom", command=["python3", str(FIXTURE)]),
        )
        events = []
        bridge.subscribe(events.append)

        await bridge.run("build api", context={"task_id": "task-1"})
        await asyncio.sleep(0.2)
        await bridge.stop()

        assert [event.status for event in events] == [
            AgentEventStatus.STARTED,
            AgentEventStatus.COMPLETED,
        ]
        assert events[-1].result == "done:build api"

    asyncio.run(_run())


def test_custom_backend_yield_and_resume_round_trip() -> None:
    async def _run() -> None:
        bridge = CustomBridge(
            "coder",
            BackendConfig(type="custom", command=["python3", str(FIXTURE)]),
        )
        events = []
        bridge.subscribe(events.append)

        await bridge.run("need-input", context={"task_id": "task-2"})
        await asyncio.sleep(0.2)
        assert [event.status for event in events] == [
            AgentEventStatus.STARTED,
            AgentEventStatus.YIELDED,
        ]
        assert events[-1].result == "Need clarification"

        await bridge.resume("task-2", "use sqlite")
        await asyncio.sleep(0.2)
        await bridge.stop()

        assert events[-1].status == AgentEventStatus.COMPLETED
        assert events[-1].result == "answer:use sqlite"

    asyncio.run(_run())


def test_backend_agent_resumes_after_answer_event() -> None:
    """BackendAgent resumes its backend when an ANSWER event fires on the event bus.

    This is the exact mechanism OrchestratorSession uses when the user replies to a
    yielded question: it publishes an ANSWER event tagged with the task_id of the
    waiting question.  BackendAgent's answer handler picks it up and calls resume().
    """

    async def _run() -> None:
        bridge = CustomBridge(
            "researcher",
            BackendConfig(type="custom", command=["python3", str(FIXTURE)]),
        )
        agent = BackendAgent(role="researcher", backend=bridge)
        orchestrator = InteractiveOrchestrator(agents={"researcher": agent})
        session = await orchestrator.start_session()

        # Start a task that yields
        await session.converse("need-input")
        yielded_task_id = None
        # Drain events to find the agent_yielded
        for _ in range(8):
            ev = await session.next_event(timeout=2)
            if ev is None:
                break
            if ev.type == "agent_yielded":
                yielded_task_id = ev.task_id
                break

        assert yielded_task_id is not None, "did not see agent_yielded"

        # Simulate OrchestratorSession._answer_waiting_question publishing an ANSWER
        # event (same way a user's reply would be delivered).
        from submarine.events.types import AgentEvent
        await session._context.event_bus.publish(AgentEvent(
            agent_id="researcher",
            task_id=yielded_task_id,
            role="researcher",
            status=AgentEventStatus.ANSWER,
            result="use postgres",
        ))

        # Drain events until we see agent_completed for the resumed task.
        # Note: other events (supervisor, new tasks) may appear due to
        # default_supervisor_brain auto-followup — we just find the right one.
        resumed_completed = None
        for _ in range(10):
            ev = await session.next_event(timeout=2)
            if ev is None:
                break
            if ev.type == "agent_completed" and ev.task_id == yielded_task_id:
                resumed_completed = ev
                break

        assert resumed_completed is not None, (
            f"agent_completed for {yielded_task_id} never arrived. "
            "Check that BackendAgent.answer_handler called backend.resume()."
        )
        assert resumed_completed.result == "answer:use postgres"

        await session.stop()

    asyncio.run(_run())