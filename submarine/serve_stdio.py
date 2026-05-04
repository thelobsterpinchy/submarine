from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from contextlib import suppress
from dataclasses import asdict
from typing import Any

from submarine import InteractiveOrchestrator, LLMClient, Plan, SessionEvent, Task, make_llm_supervisor_brain
from submarine.agents.backends import make_backend_agent


class StdioServer:
    def __init__(self) -> None:
        self.session = None
        self._event_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    async def send(self, payload: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()

    async def send_event(self, event: SessionEvent) -> None:
        await self.send({"type": "event", "event": asdict(event)})

    async def send_result(self, request_id: str | int | None, result: dict[str, Any]) -> None:
        await self.send({"type": "response", "id": request_id, "result": result})

    async def send_error(self, request_id: str | int | None, error: str) -> None:
        await self.send({"type": "response", "id": request_id, "error": error})

    async def pump_events(self, stop_event: asyncio.Event) -> None:
        if self.session is None:
            return
        while True:
            try:
                event = await self.session.next_event()
            except Exception:  # noqa: BLE001
                break
            await self.send_event(event)
            if event.type == "session_stopped" or stop_event.is_set():
                return

    def _build_orchestrator(self, payload: dict[str, Any]) -> InteractiveOrchestrator:
        supervisor = payload.get("supervisor") or {}
        agents_payload = payload.get("agents") or []
        run_kind_routes = payload.get("run_kind_routes") or {}

        supervisor_client = LLMClient(
            base_url=supervisor.get("base_url") or os.environ.get("SUBMARINE_SUPERVISOR_BASE_URL", "https://api.openai.com/v1"),
            api_key=supervisor.get("api_key") or os.environ.get("SUBMARINE_SUPERVISOR_API_KEY"),
            model=supervisor.get("model") or os.environ.get("SUBMARINE_SUPERVISOR_MODEL", "gpt-4o-mini"),
            default_system_prompt=supervisor.get("system_prompt"),
            timeout=float(supervisor.get("timeout", 120)),
        )

        agents = {}
        for agent in agents_payload:
            role = agent["role"]
            backend_type = agent.get("backend", {}).get("type") if isinstance(agent.get("backend"), dict) else None
            if backend_type:
                backend_config = {
                    "type": backend_type,
                    "model": agent.get("model") or supervisor.get("model") or os.environ.get("SUBMARINE_AGENT_MODEL"),
                    "base_url": agent.get("base_url") or supervisor.get("base_url") or os.environ.get("SUBMARINE_AGENT_BASE_URL"),
                    "api_key": agent.get("api_key") or supervisor.get("api_key") or os.environ.get("SUBMARINE_AGENT_API_KEY"),
                    "timeout": agent.get("timeout", 120),
                    "system_prompt": agent.get("system_prompt"),
                    "command": agent.get("backend", {}).get("command") if isinstance(agent.get("backend"), dict) else None,
                    "python_path": agent.get("backend", {}).get("python_path") if isinstance(agent.get("backend"), dict) else None,
                    "script_module": agent.get("backend", {}).get("script_module") if isinstance(agent.get("backend"), dict) else None,
                    "workspace": agent.get("workspace"),
                    **agent.get("backend", {}),
                }
                agents[role] = make_backend_agent(
                    role=role,
                    config=backend_config,
                    timeout=float(agent.get("timeout", 120)),
                )
            else:
                from submarine import make_openai_agent  # noqa: F811
                agents[role] = make_openai_agent(
                    role=role,
                    model=agent.get("model", "gpt-4o-mini"),
                    system_prompt=agent.get("system_prompt"),
                    base_url=agent.get("base_url") or supervisor.get("base_url") or os.environ.get("SUBMARINE_AGENT_BASE_URL", "https://api.openai.com/v1"),
                    api_key=agent.get("api_key") or supervisor.get("api_key") or os.environ.get("SUBMARINE_AGENT_API_KEY"),
                    timeout=float(agent.get("timeout", 120)),
                )

        def planner(task: str, all_agents: dict[str, Any], shared_memory: dict[str, Any]) -> Plan:
            run_kind = shared_memory.get("run_kind")
            route = run_kind_routes.get(run_kind, {}) if isinstance(run_kind_routes, dict) else {}
            role = route.get("role") if isinstance(route, dict) else None
            model = route.get("model") if isinstance(route, dict) else None
            if role and role in all_agents:
                return Plan(
                    initial_subtasks=[
                        Task(
                            id=str(uuid.uuid4()),
                            role=role,
                            description=task,
                            metadata={"model": model} if model else {},
                        )
                    ]
                )
            first_role = next(iter(all_agents.keys()))
            return Plan(initial_subtasks=[Task(id=str(uuid.uuid4()), role=first_role, description=task)])

        return InteractiveOrchestrator(
            agents=agents,
            planner=planner,
            supervisor_brain=make_llm_supervisor_brain(
                supervisor_client,
                system_prompt=supervisor.get("system_prompt"),
                model=supervisor.get("model"),
                temperature=float(supervisor.get("temperature", 0.2)),
                max_tokens=int(supervisor.get("max_tokens", 800)),
            ),
        )

    async def _stop_existing_session(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self.session is not None:
            await self.session.stop()
            self.session = None
        if self._event_task is not None:
            with suppress(asyncio.CancelledError):
                await self._event_task
            self._event_task = None
        self._stop_event = None

    async def handle(self, line: str) -> None:
        request = json.loads(line)
        request_id = request.get("id")
        method = request.get("method")
        payload = request.get("params") or {}

        if method == "ping":
            await self.send_result(request_id, {"ok": True})
            return

        if method == "start_session":
            await self._stop_existing_session()
            orchestrator = self._build_orchestrator(payload)
            self.session = await orchestrator.start_session(shared_memory=payload.get("shared_memory") or {})
            self._stop_event = asyncio.Event()
            self._event_task = asyncio.create_task(self.pump_events(self._stop_event))
            await self.send_result(request_id, {"ok": True})
            return

        if self.session is None:
            await self.send_error(request_id, "session not started")
            return

        if method == "converse":
            response = await self.session.converse(
                payload.get("message", ""),
                target_task_id=payload.get("target_task_id"),
            )
            await self.send_result(request_id, {"response": asdict(response)})
            return

        if method == "snapshot":
            await self.send_result(request_id, {"snapshot": self.session.snapshot()})
            return

        if method == "stop":
            await self._stop_existing_session()
            await self.send_result(request_id, {"ok": True})
            return

        await self.send_error(request_id, f"unknown method: {method}")


async def main() -> None:
    server = StdioServer()
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            await server.handle(line)
        except Exception as exc:  # noqa: BLE001
            request_id = None
            with suppress(Exception):
                request_id = json.loads(line).get("id")
            await server.send_error(request_id, str(exc))

    await server._stop_existing_session()


if __name__ == "__main__":
    asyncio.run(main())
