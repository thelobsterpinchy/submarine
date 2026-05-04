from __future__ import annotations

import asyncio
from typing import Any

from submarine.agents.base import Agent, AgentRunContext
from submarine.agents.llm import LLMClient
from submarine.core.types import Task, TaskResult


def make_openai_agent(
    role: str,
    model: str = "gpt-4o",
    system_prompt: str | None = None,
    base_url: str = "https://api.openai.com/v1",
    api_key: str | None = None,
    timeout: float = 120,
    max_tokens: int = 8192,
    temperature: float = 0.7,
    **kwargs: Any,
) -> Agent:
    client = LLMClient(base_url=base_url, api_key=api_key, model=model, default_system_prompt=system_prompt, timeout=timeout)

    async def handler(task: Task, context: AgentRunContext) -> TaskResult:
        prompt = task.description
        resolved_model = task.metadata.get("model") or task.context.get("model") or model
        output = await client.complete(
            prompt,
            system_prompt=system_prompt,
            model=resolved_model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return TaskResult(
            task_id=task.id,
            agent_id=f"openai-{role}",
            role=role,
            output=output,
            metadata={"model": resolved_model},
        )

    return Agent(role=role, handler=handler, model=model, **kwargs)