from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from typing import Any

from submarine.agents.backends.base import AgentBackend, BackendConfig, map_session_event_to_status
from submarine.events.types import AgentEvent


class CustomBridge(AgentBackend):
    """Generic stdio JSON backend.

    Expected protocol:
    - request line: {"id": ..., "method": ..., "params": {...}}
    - response line: {"type": "response", "id": ..., "result": {...}} or {"type":"response","id":...,"error":"..."}
    - event line:    {"type": "event", "event": {...}}

    Default methods:
    - ping
    - start
    - continue
    - stop

    Override method names through config.extra:
    - start_method
    - continue_method
    - stop_method
    - ping_method
    """

    def __init__(self, role: str, config: BackendConfig) -> None:
        super().__init__(role)
        self.config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._started = False
        self._agent_id = f"custom-{role}-{uuid.uuid4().hex[:8]}"
        self._active_task_id: str | None = None

    async def start(self) -> None:
        if self._started:
            return
        command = self.config.command
        if not command:
            raise ValueError("CustomBridge requires config.command")
        cmd = command if isinstance(command, list) else [command]
        env = {**os.environ, **(self.config.env or {})}
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.config.workspace,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        ping_method = self.config.extra.get("ping_method", "ping")
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._request(ping_method, {}), timeout=10)
        self._started = True

    async def stop(self) -> None:
        if self._proc is None:
            return
        stop_method = self.config.extra.get("stop_method", "stop")
        with contextlib.suppress(Exception):
            await self._request(stop_method, {})
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
        task_id = (context or {}).get("task_id") or str(uuid.uuid4())
        self._active_task_id = task_id
        start_method = self.config.extra.get("start_method", "start")
        payload = {
            "task": task,
            "task_id": task_id,
            "role": self.role,
            "model": self.config.model,
            "context": context or {},
        }
        await self._request(start_method, payload)

    async def resume(self, task_id: str, answer: str) -> None:
        await self.start()
        self._active_task_id = task_id
        continue_method = self.config.extra.get("continue_method", "continue")
        await self._request(continue_method, {"task_id": task_id, "message": answer, "role": self.role})

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
            status = map_session_event_to_status(f"agent_{event.get('status', '')}") if event.get("status") else None
        if status is None:
            return
        task_id = event.get("task_id") or self._active_task_id or str(uuid.uuid4())
        self._emit(
            AgentEvent(
                agent_id=event.get("agent_id") or self._agent_id,
                task_id=task_id,
                role=event.get("role") or self.role,
                status=status,
                result=event.get("result") or event.get("message"),
                artifacts=event.get("artifacts") or {},
                error=event.get("error"),
                metadata=event.get("metadata") or {},
            )
        )

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("Backend process is not running")
        req_id = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        self._proc.stdin.write((json.dumps({"id": req_id, "method": method, "params": params}) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()
        return await future
