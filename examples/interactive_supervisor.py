from __future__ import annotations

import asyncio

from submarine import InteractiveOrchestrator, LLMClient, make_llm_supervisor_brain
from submarine.agents.openai import make_openai_agent


async def main() -> None:
    supervisor_client = LLMClient(
        base_url="http://100.83.56.102:8080/v1",
        api_key="sk-local",
        model="unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_XL",
    )

    orchestrator = InteractiveOrchestrator(
        agents={
            "coder": make_openai_agent(
                role="coder",
                model="gpt-4o-mini",
                system_prompt="You are a fast coding subagent.",
            ),
            "researcher": make_openai_agent(
                role="researcher",
                model="gpt-4.1-mini",
                system_prompt="You are a careful research subagent.",
            ),
        },
        supervisor_brain=make_llm_supervisor_brain(
            supervisor_client,
            model="unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_XL",
        ),
    )

    session = await orchestrator.start_session()
    reply = await session.converse("Build a small JSON API and ask me if you need database preferences.")
    print("supervisor:", reply.text)

    while True:
        event = await session.next_event(timeout=30)
        print(event.type, event.message)
        if event.type == "agent_yielded":
            answer = await session.converse("Use postgres.", target_task_id=event.task_id)
            print("supervisor:", answer.text)
        if event.type == "session_stopped":
            break


if __name__ == "__main__":
    asyncio.run(main())
