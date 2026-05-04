# Submarine

Event-driven subagent orchestration for coding and research workflows.

Submarine gives you two layers:
- a **batch orchestrator** for decomposing tasks across subagents
- an **interactive supervisor** for long-lived conversations where agents can yield, ask questions, and resume precisely

It is designed for agent systems that need more than a single linear loop.

## Highlights

- Event-driven orchestration, no polling loop required
- Multiple agent roles with model-specific routing
- Interactive supervisor session with resumable yielded questions
- Structured artifacts and task metadata
- OpenAI-compatible LLM client support
- Subprocess stdio bridge for non-Python hosts

## Install

```bash
pip install submarine
```

For local development:

```bash
git clone https://github.com/thelobsterpinchy/submarine.git
cd submarine
pip install -e .
```

## Quick start

### Batch orchestrator

```python
import asyncio
from submarine import Orchestrator, make_openai_agent

coder = make_openai_agent(
    role="coder",
    model="gpt-4o-mini",
    system_prompt="You write clean, correct code.",
)

researcher = make_openai_agent(
    role="researcher",
    model="gpt-4o-mini",
    system_prompt="You do fast, accurate technical research.",
)

orchestrator = Orchestrator(
    agents={"coder": coder, "researcher": researcher}
)

async def main():
    result = await orchestrator.run(
        task="Build a small REST API, then summarize deployment options."
    )
    print(result.summary)

asyncio.run(main())
```

### Interactive supervisor

```python
import asyncio
from submarine import InteractiveOrchestrator, LLMClient, make_llm_supervisor_brain, make_openai_agent

supervisor_client = LLMClient(
    base_url="https://api.openai.com/v1",
    api_key="YOUR_API_KEY",
    model="gpt-4o-mini",
)

coder = make_openai_agent(
    role="coder",
    model="gpt-4o-mini",
    system_prompt="You are a careful coding agent.",
)

orchestrator = InteractiveOrchestrator(
    agents={"coder": coder},
    supervisor_brain=make_llm_supervisor_brain(supervisor_client),
)

async def main():
    session = await orchestrator.start_session(shared_memory={"project": "demo"})
    response = await session.converse("Build a hello world CLI")
    print(response.text)

asyncio.run(main())
```

## Stdio bridge

Submarine includes a stdio JSON-RPC bridge for embedding from other runtimes:

```bash
python3 -m submarine.serve_stdio
```

Methods:
- `start_session`
- `converse`
- `snapshot`
- `stop`
- `ping`

Events emitted over stdout:
- `supervisor`
- `agent_started`
- `agent_yielded`
- `agent_completed`
- `agent_failed`
- `session_stopped`
- `user_reply`

## Architecture

```text
submarine/
├── agents/        Agent implementations and adapters
│   └── backends/  Pluggable agent backends (Pi first, more later)
├── core/          Task, plan, and result primitives
├── events/        Event bus and event types
├── orchestrator/  Batch and interactive orchestrators
└── serve_stdio.py JSON-RPC stdio bridge
```

## Pluggable backends

Submarine now includes a backend abstraction so the supervisor can stay stable while execution backends vary by role.

Current pieces:
- `AgentBackend` — common backend interface
- `BackendAgent` — adapts a backend into a normal Submarine `Agent`
- `PiBridge` — first backend bridge, aimed at Pi-style stdio agent hosts

Target direction:
- `pi`
- `opencode`
- `custom`

This lets a future config mix backends per role while keeping the same supervisor loop.

## Examples

See `examples/` for:
- `basic.py`
- `interactive_supervisor.py`
- `local_llm_orchestration.py`
- `openai_orchestration.py`
- `subprocess_agents.py`

## Development

```bash
pip install -e .
python3 -m py_compile submarine/serve_stdio.py
python3 tests/test_basic.py
python3 tests/test_interactive.py
```

## Roadmap

- Better distributed event-bus backends
- Richer streaming event translation
- More first-class model/provider adapters
- Stronger test coverage around interactive resume flows

## License

MIT
