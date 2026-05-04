# AGENTS.md

Guidance for agents and contributors working in this repo.

## What Submarine is

Submarine is a Python library for orchestrating multiple subagents.
It has two main execution models:
- `Orchestrator` for batch task decomposition
- `InteractiveOrchestrator` for conversational, resumable supervision

## Repo structure

- `submarine/agents/` agent implementations and adapters
- `submarine/core/` shared task and result types
- `submarine/events/` event bus and event models
- `submarine/orchestrator/` orchestration logic
- `submarine/serve_stdio.py` bridge for non-Python hosts
- `examples/` runnable demos
- `tests/` lightweight test coverage

## Engineering rules

- Preserve Python 3.9+ compatibility
- Prefer additive changes over breaking API changes
- Keep the interactive path non-blocking
- Do not reintroduce polling where event-driven flows already exist
- Keep event payloads structured and explicit
- If a yielded question must be resumed precisely, preserve `task_id`

## Testing

This repo may not always have `pytest` available.
Prefer tests that can run with plain Python when possible.

Examples:

```bash
python3 -m py_compile submarine/serve_stdio.py
python3 tests/test_basic.py
python3 tests/test_interactive.py
```

## Library design notes

- `SessionEvent` is the public event surface for interactive embedding
- `SupervisorResponse` is the structured return type for supervisor turns
- `make_llm_supervisor_brain(...)` is the preferred structured LLM supervisor entry point
- The stdio bridge should stay thin, stable, and host-friendly

## When changing the stdio bridge

If you add new resume semantics or targeting behavior:
- include explicit identifiers like `task_id`
- avoid encoding state into opaque strings when structured fields work
- keep JSON-RPC method names stable unless there is a clear migration path

## When changing orchestration behavior

- keep `Orchestrator.run()` backward compatible if possible
- prefer new entry points for new modes instead of breaking old ones
- document any new event types in `README.md`

## Publishing

Before publishing:
- update `README.md`
- keep `pyproject.toml` metadata accurate
- ensure examples still reflect the current API
