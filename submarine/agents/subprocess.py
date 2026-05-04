from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from submarine.agents.base import Agent, AgentRunContext
from submarine.core.types import Task, TaskResult
from submarine.events.bus import EventBus
from submarine.events.types import AgentEvent, AgentEventStatus


@dataclass
class SubprocessConfig:
    command: str | list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    shell: bool = False


class SubprocessAgent(Agent):
    def __init__(
        self,
        role: str,
        config: SubprocessConfig,
        result_parser: callable | None = None,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        timeout: float = 300,
        max_tokens: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.result_parser = result_parser or (lambda stdout, stderr, returncode: self._default_parse(stdout, stderr, returncode))
        super().__init__(
            role=role,
            handler=self._subprocess_handler,
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            timeout=timeout,
            max_tokens=max_tokens,
            metadata=metadata,
        )

    async def _subprocess_handler(self, task: Task, context: AgentRunContext) -> TaskResult:
        loop = asyncio.get_event_loop()
        cmd = self.config.command
        if isinstance(cmd, str):
            cmd = [cmd]
        cmd = [c.replace("{task}", task.description).replace("{description}", task.description) for c in cmd]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.config.cwd,
            env={**os.environ, **(self.config.env or {})},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=self.config.shell,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        stdout = stdout_bytes.decode("utf-8", "replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", "replace") if stderr_bytes else ""
        output, artifacts = self.result_parser(stdout, stderr, proc.returncode)
        return TaskResult(
            task_id=task.id,
            agent_id=f"subprocess-{self.role}",
            role=self.role,
            output=output,
            artifacts={"returncode": proc.returncode, "stderr": stderr, **artifacts},
        )

    @staticmethod
    def _default_parse(stdout: str, stderr: str, returncode: int) -> tuple[str, dict]:
        if returncode == 0:
            return stdout, {}
        return stdout, {"error": stderr, "returncode": returncode}