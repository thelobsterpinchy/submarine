from __future__ import annotations

import asyncio

from submarine import Orchestrator
from submarine.agents.mock import make_mock_agent
from submarine.orchestrator.patterns import simple_coding_planner


def test_orchestrator_runs_mock_agents() -> None:
    async def _run() -> None:
        orchestrator = Orchestrator(
            agents={
                "coder": make_mock_agent("coder"),
                "tester": make_mock_agent("tester"),
            },
            planner=lambda task, agents, shared: simple_coding_planner(task, list(agents.keys())),
        )
        result = await orchestrator.run("Build code and test it")
        assert "[coder]" in result.summary
        assert "[tester]" in result.summary

    asyncio.run(_run())
