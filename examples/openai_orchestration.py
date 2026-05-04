from __future__ import annotations

import asyncio
import os

from submarine import Orchestrator
from submarine.agents.openai import make_openai_agent
from submarine.orchestrator.patterns import simple_coding_planner


async def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("Set OPENAI_API_KEY to run this example")
        return

    coder = make_openai_agent(
        role="coder",
        model="gpt-4o",
        system_prompt=(
            "You are a senior software engineer. Write clean, correct code. "
            "Prefer short, focused functions with good error handling."
        ),
    )
    researcher = make_openai_agent(
        role="researcher",
        model="gpt-4o-mini",
        system_prompt="You are a research assistant. Find concise, accurate information.",
    )

    orchestrator = Orchestrator(
        agents={"coder": coder, "researcher": researcher},
        planner=lambda task, all_agents, shared: simple_coding_planner(task, list(all_agents.keys())),
    )

    result = await orchestrator.run(
        "Explain the trade-offs between event-driven and thread-per-connection servers. "
        "Then write a minimal async Python echo server using asyncio."
    )
    print(result.summary)
    print("---Artifacts---")
    for key, val in result.artifacts.items():
        if key != "events":
            print(f"  {key}: {val}")


if __name__ == "__main__":
    asyncio.run(main())