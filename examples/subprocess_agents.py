from __future__ import annotations

import asyncio
import tempfile

from submarine import Orchestrator
from submarine.agents.subprocess import SubprocessAgent, SubprocessConfig
from submarine.orchestrator.patterns import simple_coding_planner


def make_codex_agent(role: str, cwd: str | None = None) -> SubprocessAgent:
    return SubprocessAgent(
        role=role,
        config=SubprocessConfig(
            command=[
                "codex",
                "exec",
                "--full-auto",
                "{task}",
            ],
            cwd=cwd,
        ),
        timeout=300,
    )


async def main() -> None:
    # Use a temp directory so codex has a git repo to work in
    with tempfile.TemporaryDirectory() as tmpdir:
        agents = {
            "coder": make_codex_agent("coder", cwd=tmpdir),
            "tester": make_codex_agent("tester", cwd=tmpdir),
        }

        orchestrator = Orchestrator(
            agents=agents,
            planner=lambda task, all_agents, shared: simple_coding_planner(task, list(all_agents.keys())),
        )

        result = await orchestrator.run(
            "Write a Python function that checks if a string is a palindrome, "
            "then write pytest tests for it."
        )
        print(result.summary)


if __name__ == "__main__":
    asyncio.run(main())