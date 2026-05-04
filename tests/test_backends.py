from __future__ import annotations

import asyncio
from pathlib import Path

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
        await asyncio.sleep(0.1)
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
        await asyncio.sleep(0.1)
        assert [event.status for event in events] == [
            AgentEventStatus.STARTED,
            AgentEventStatus.YIELDED,
        ]
        assert events[-1].result == "Need clarification"

        await bridge.resume("task-2", "use sqlite")
        await asyncio.sleep(0.1)
        await bridge.stop()

        assert events[-1].status == AgentEventStatus.COMPLETED
        assert events[-1].result == "answer:use sqlite"

    asyncio.run(_run())
