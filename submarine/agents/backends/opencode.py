from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from submarine.agents.backends.base import AgentBackend, BackendConfig, map_session_event_to_status
from submarine.events.types import AgentEvent


@dataclass
class OpenCodeConfig:
    """Configuration for an OpenCode agent subprocess."""

    command: str | list[str] = ["opencode", "--agent"]
    workspace: str | None = None
    env: dict[str, str] | None = None


class OpenCodeBridge(AgentBackend):
    """Backend bridge that spawns an OpenCode subprocess and drives it via stdio JSON.

    OpenCode accepts a task-mode invocation where it:
    - Receives a task via stdin as a JSON object
    - Emits events / responses on stdout
    - Exposes a ping/ping response for liveness
    """

    def __init__(self, role: str, config: BackendConfig) -> None:
        super().__init__(role)
        self.config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._started = False
        self._agent_id = f"opencode-{role}-{uuid.uuid4().hex[:8]}"
        self._active_task_id: str | None = None

    async def start(self) -> None:
        if self._started:
            return

        command = self.config.command
        if command:
            cmd = command if isinstance(command, list) else [command]
        else:
            cmd = ["opencode", "--agent"]

        env = dict(os.environ)
        if self.config.base_url:
            env.setdefault("OPENAI_BASE_URL", self.config.base_url)
        if self.config.api_key:
            env.setdefault("OPENAI_API_KEY", self.config.api_key)
        if self.config.model:
            env.setdefault("OPENAI_MODEL", self.config.model)

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.config.workspace,
            env={**env, **(self.config.env or {})},
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            await asyncio.wait_for(self._request("ping", {}), timeout=10)
        except Exception:
            pass
        self._started = True

    async def stop(self) -> None:
        if self._proc is None:
            return
        with contextlib.suppress(Exception):
            await self._request("stop", {})
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self._proc.returncode is None:
            self._proc.terminate()
            with contextlib.suppress(ProcessLookupError):
                await asyncio.wait_for(self._proc.wait(), timeout=5)
        self._proc = None
        self._reader_task = None
        self._pending.clear()
        self._started = False

    async def run(self, task: str, *, context: dict[str, Any] | None = None) -> None:
        await self.start()
        if self._proc is None:
            raise RuntimeError("OpenCodeBridge failed to start backend process")

        task_id = str(uuid.uuid4())
        self._active_task_id = task_id

        await self._request(
            "start",
            {
                "task": task,
                "role": self.role,
                "model": self.config.model,
                "timeout": self.config.timeout,
                "context": context or {},
            },
        )

    async def resume(self, task_id: str, answer: str) -> None:
        self._active_task_id = task_id
        await self._request(
            "continue",
            {
                "task_id": task_id,
                "message": answer,
            },
        )

    async def _read_loop(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                return
            try:
                payload = json.loads(line.decode("utf-8", "replace").strip())
            except Exception:
                continue

            if payload.get("type") == "response":
                req_id = str(payload.get("id"))
                future = self._pending.pop(req_id, None)
                if future is not None and not future.done():
                    if payload.get("error"):
                        future.set_exception(RuntimeError(payload["error"]))
                    else:
                        future.set_result(payload.get("result") or {})
                continue

            if payload.get("type") == "event":
                await self._handle_event(payload.get("event") or {})

    async def _handle_event(self, event: dict[str, Any]) -> None:
        status = map_session_event_to_status(event.get("type", ""))
        if status is None:
            return
        task_id = event.get("task_id") or self._active_task_id or str(uuid.uuid4())
        agent_event = AgentEvent(
            agent_id=event.get("agent_id") or self._agent_id,
            task_id=task_id,
            role=event.get("role") or self.role,
            status=status,
            result=event.get("result") or event.get("message"),
            artifacts=event.get("artifacts") or {},
            error=event.get("error"),
            metadata=event.get("metadata") or {},
        )
        self._emit(agent_event)

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("Backend process is not running")
        req_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        payload = {"id": req_id, "method": method, "params": params}
        self._proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()
        return await future