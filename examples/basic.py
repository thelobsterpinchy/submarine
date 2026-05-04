from __future__ import annotations

import asyncio

from submarine import Orchestrator
from submarine.agents.mock import make_mock_agent
from submarine.orchestrator.patterns import simple_coding_planner


async def main() -> None:
    agents = {
        "coder": make_mock_agent("coder"),
        "researcher": make_mock_agent("researcher"),
        "tester": make_mock_agent("tester"),
    }

    orchestrator = Orchestrator(
        agents=agents,
        planner=lambda task, all_agents, shared_memory: simple_coding_planner(task, list(all_agents.keys())),
    )

    result = await orchestrator.run("Build auth code, then research deployment and run tests")
    print(result.summary)
    print(result.artifacts)


if __name__ == "__main__":
    asyncio.run(main())
