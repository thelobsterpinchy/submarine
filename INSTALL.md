# Submarine

**Event-driven subagent orchestration library for coding tasks.**

Submarine wakes an orchestrator whenever a subagent completes, enabling dynamic task decomposition, adaptive spawning, and result aggregation — without polling.

---

## Quick Start

```python
import asyncio
from submarine import Orchestrator
from submarine.agents.openai import make_openai_agent

coder = make_openai_agent(
    role="coder",
    model="gpt-4o",
    system_prompt="You are a senior software engineer. Write clean, correct code.",
)

orchestrator = Orchestrator(agents={"coder": coder})
result = await orchestrator.run("Write a palindrome checker and its tests")
print(result.summary)
```

---

## Core Concepts

### Event Bus
The event bus is the core primitive. All inter-agent communication flows through it.

```python
from submarine import EventBus, AgentEvent

bus = EventBus()

async def on_event(event: AgentEvent):
    print(f"{event.role} finished: {event.status}")

bus.subscribe("coder", on_event)
bus.publish(AgentEvent(...))
```

### Agents

Three built-in agent types:

- **`Agent`** — base class, you supply an async handler
- **`make_openai_agent()`** — OpenAI / OpenAI-compatible API (local vLLM, Ollama, etc.)
- **`SubprocessAgent`** — wraps any CLI tool (codex, claude, git, pytest, etc.)

```python
from submarine import SubprocessAgent, SubprocessConfig

codex = SubprocessAgent(
    role="coder",
    config=SubprocessConfig(
        command=["codex", "exec", "--full-auto", "{task}"],
        cwd="/path/to/project",
    ),
    timeout=300,
)
```

### Orchestrator

Drives the event loop. Wakes on subagent completion events, decides whether to spawn more or aggregate.

```python
from submarine import Orchestrator

orchestrator = Orchestrator(
    agents={"coder": coder, "researcher": researcher},
    planner=my_planner,       # optional
    aggregator=my_aggregator,  # optional
)
result = await orchestrator.run("Build auth and test it")
```

---

## Examples

| File | Description |
|------|-------------|
| `examples/basic.py` | Mock agent flow, no external deps |
| `examples/openai_orchestration.py` | Real OpenAI API agents |
| `examples/subprocess_agents.py` | Codex CLI subprocess agents |
| `examples/local_llm_orchestration.py` | Local vLLM servers (100.80.84.96, 100.83.56.102) |

Run any example:
```bash
PYTHONPATH=. python3 examples/<example>.py
```

For local LLM example, set the appropriate API keys in the script or as env vars.

---

## Architecture

```
submarine/
├── events/
│   ├── bus.py        EventBus (pub/sub, async wait_for)
│   └── types.py      AgentEvent, AgentEventStatus
├── core/
│   └── types.py      Task, TaskResult, Plan, AggregatedResult
├── agents/
│   ├── base.py       Agent, AgentRunContext
│   ├── llm.py        LLMClient (async, streaming, OpenAI-compatible)
│   ├── openai.py     make_openai_agent() factory
│   ├── subprocess.py SubprocessAgent, SubprocessConfig
│   └── mock.py       make_mock_agent() for testing
└── orchestrator/
    ├── core.py       Orchestrator (event loop, routing, aggregation)
    └── patterns.py   simple_coding_planner, respawn_on_failure
```

---

## Routing

Agents are selected by role. Override the router for custom selection logic:

```python
def my_router(task, agents):
    if "refactor" in task.description:
        return agents["senior-coder"]
    return agents["coder"]

orchestrator = Orchestrator(agents=..., router=my_router)
```

---

## Custom Planners

```python
from submarine.core.types import Plan, Task
import uuid

def my_planner(task, agents, shared_memory):
    return Plan(initial_subtasks=[
        Task(id=str(uuid.uuid4()), role="researcher", description=f"Research: {task}"),
        Task(id=str(uuid.uuid4()), role="coder", description=f"Implement: {task}"),
    ])

orchestrator = Orchestrator(agents=..., planner=my_planner)
```

---

## Completion Hooks

Called every time a subagent finishes. Return a list of new Tasks to spawn more agents, or `None`/`[]` to stop.

```python
def on_complete(event, orchestrator):
    if event.status == "failed":
        return [Task(id=str(uuid.uuid4()), role=event.role, description=f"Retry: {event.error}")]
    return []

orchestrator = Orchestrator(agents=..., completion_hook=on_complete)
```

---

## Custom Aggregators

```python
def my_aggregator(task, results, events):
    return AggregatedResult(
        task=task,
        summary="\n".join(r.output for r in results),
        subresults=results,
    )

orchestrator = Orchestrator(agents=..., aggregator=my_aggregator)
```

---

## Dependencies

- Python >= 3.9
- `aiohttp` (for LLM client)

Install: `pip install aiohttp`

---

## MIT License