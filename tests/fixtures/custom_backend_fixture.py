from __future__ import annotations

import json
import sys


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


while True:
    line = sys.stdin.readline()
    if not line:
        break
    req = json.loads(line)
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if method == "ping":
        send({"type": "response", "id": req_id, "result": {"ok": True}})
        continue

    if method == "start":
        task_id = params.get("task_id", "task-1")
        role = params.get("role", "coder")
        task = params.get("task", "")
        send({"type": "response", "id": req_id, "result": {"ok": True}})
        send({"type": "event", "event": {"type": "agent_started", "task_id": task_id, "role": role, "message": f"{role} started"}})
        if "need-input" in task:
            send({"type": "event", "event": {"type": "agent_yielded", "task_id": task_id, "role": role, "message": "Need clarification"}})
        else:
            send({"type": "event", "event": {"type": "agent_completed", "task_id": task_id, "role": role, "result": f"done:{task}"}})
        continue

    if method == "continue":
        task_id = params.get("task_id", "task-1")
        role = params.get("role", "coder")
        message = params.get("message", "")
        send({"type": "response", "id": req_id, "result": {"ok": True}})
        send({"type": "event", "event": {"type": "agent_completed", "task_id": task_id, "role": role, "result": f"answer:{message}"}})
        continue

    if method == "stop":
        send({"type": "response", "id": req_id, "result": {"ok": True}})
        break

    send({"type": "response", "id": req_id, "error": f"unknown method: {method}"})