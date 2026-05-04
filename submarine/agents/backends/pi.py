from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import uuid
from dataclasses import asdict
from typing import Any

from submarine.agents.backends.base import AgentBackend, BackendConfig, map_session_event_to_status
from submarine.events.types import AgentEvent


class PiBridge(AgentBackend):
    """Backend bridge that talks to a stdio agent host using JSON lines.

    This is the first backend implementation for the pluggable backend model.
    Despite the name, it is intentionally generic enough to target a Pi-style
    agent host that exposes:

    - ping
    - start_session
    - converse
    - snapshot
    - stop

    over stdin/stdout as JSON lines.
    """

    def __init__(self, role: str, config: BackendConfig) -> None:
        super().__init__(role)
        self.config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._started = False
        self._agent_id = f"pi-{role}-{uuid.uuid4().hex[:8]}"
        self._active_task_id: str | None = None

    async def start(self) -> None:
        if self._started:
            return

        command = self.config.command
        if command:
            cmd = command if isinstance(command, list) else [command]
        else:
            python_path = self.config.python_path or sys.executable or "python3"
            script_module = self.config.script_module or "submarine.serve_stdio"
            cmd = [python_path, "-m", script_module]

        env = dict(os.environ)
        if self.config.base_url:
            env.setdefault("SUBMARINE_AGENT_BASE_URL", self.config.base_url)
        if self.config.api_key:
            env.setdefault("SUBMARINE_AGENT_API_KEY", self.config.api_key)
        if self.config.model:
            env.setdefault("SUBMARINE_AGENT_MODEL", self.config.model)

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.config.workspace,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._request("ping", {})
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
            raise RuntimeError("PiBridge failed to start backend process")

        task_id = (context or {}).get("task_id") or str(uuid.uuid4())
        self._active_task_id = task_id

        await self._request(
            "start_session",
            {
                "shared_memory": context or {},
                "supervisor": {
                    "model": self.config.model,
                    "base_url": self.config.base_url,
                    "api_key": self.config.api_key,
                    "timeout": self.config.timeout,
                    "system_prompt": self.config.system_prompt,
                },
                "agents": [
                    {
                        "role": self.role,
                        "model": self.config.model,
                        "base_url": self.config.base_url,
                        "api_key": self.config.api_key,
                        "timeout": self.config.timeout,
                        "system_prompt": self.config.system_prompt,
                    }
                ],
                "run_kind_routes": {
                    "delegated": {"role": self.role},
                },
            },
        )
        await self._request(
            "converse",
            {
                "message": task,
                "target_task_id": None,
            },
        )

    async def resume(self, task_id: str, answer: str) -> None:
        await self.start()
        self._active_task_id = task_id
        await self._request(
            "converse",
            {
                "message": answer,
                "target_task_id": task_id,
            },
        )

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
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
