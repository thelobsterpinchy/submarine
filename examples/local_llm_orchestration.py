from __future__ import annotations

import asyncio

from submarine import Orchestrator
from submarine.agents.openai import make_openai_agent
from submarine.orchestrator.patterns import simple_coding_planner


async def main() -> None:
    # Route through a local vLLM server instead of OpenAI
    coder = make_openai_agent(
        role="coder",
        model="unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_XL",
        system_prompt="You are a coding agent. Write clean, correct code.",
        base_url="http://100.83.56.102:8080/v1",
        api_key="sk-unsloth-7960e081ae62374e0c334c789fcdbe77",
        timeout=120,
    )
    researcher = make_openai_agent(
        role="researcher",
        model="cyankiwi/MiniMax-M2.7-AWQ-4bit",
        system_prompt="You are a research agent. Find accurate information.",
        base_url="http://100.80.84.96:8000/v1",
        api_key="sk-no-key-required",
        timeout=120,
    )

    orchestrator = Orchestrator(
        agents={"coder": coder, "researcher": researcher},
        planner=lambda task, all_agents, shared: simple_coding_planner(task, list(all_agents.keys())),
    )

    result = await orchestrator.run(
        "Write a Python HTTP server that handles JSON payloads, then research the best Python web server benchmarks for 2026."
    )
    print(result.summary)


if __name__ == "__main__":
    asyncio.run(main())