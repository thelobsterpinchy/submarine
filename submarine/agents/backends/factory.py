from __future__ import annotations

from typing import TYPE_CHECKING, Any

from submarine.agents.backends.base import AgentBackend, BackendConfig, BackendFactory
from submarine.agents.backends.custom import CustomBridge
from submarine.agents.backends.opencode import OpenCodeBridge
from submarine.agents.backends.pi import PiBridge


def _register_defaults(factory: BackendFactory) -> BackendFactory:
    factory.register("pi", PiBridge)
    factory.register("opencode", OpenCodeBridge)
    factory.register("custom", CustomBridge)
    return factory


# Global factory instance pre-registered with known backend types
_default_factory = _register_defaults(BackendFactory())


if TYPE_CHECKING:
    from submarine.agents.backend_agent import BackendAgent


def make_backend_agent(
    role: str,
    config: BackendConfig | dict[str, Any],
    *,
    timeout: float = 300,
    model: str | None = None,
    system_prompt: str | None = None,
    tools: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> "BackendAgent":
    """Factory to create a ``BackendAgent`` from a config dict or ``BackendConfig``.

    Usage::

        agent = make_backend_agent(
            "coder",
            {"type": "pi", "model": "gpt-4o-mini", "workspace": "/tmp/workspace"},
        )

        agent = make_backend_agent(
            "coder",
            BackendConfig(type="opencode", model="gpt-4o", command=["opencode", "--agent"]),
        )

    Config fields recognized from a dict:

    - ``type`` — backend type: ``"pi"`` | ``"opencode"`` | ``"custom"``
    - ``model`` — model name
    - ``base_url`` — API base URL (optional, env var fallback)
    - ``api_key`` — API key (optional, env var fallback)
    - ``timeout`` — per-call timeout in seconds
    - ``system_prompt`` — system prompt override
    - ``command`` — for custom backends, the command to spawn
    - ``python_path`` — for pi backend, Python interpreter path
    - ``script_module`` — for pi backend, the module to run
    - ``workspace`` — working directory for the subprocess
    - ``extra`` — arbitrary additional keys forwarded to the backend
    """
    if isinstance(config, dict):
        cfg = BackendConfig(
            type=config.get("type", "openai"),
            model=config.get("model", model),
            base_url=config.get("base_url"),
            api_key=config.get("api_key"),
            timeout=config.get("timeout", int(timeout)),
            system_prompt=config.get("system_prompt", system_prompt),
            command=config.get("command"),
            env=config.get("env"),
            python_path=config.get("python_path", "python3"),
            script_module=config.get("script_module", "submarine.serve_stdio"),
            workspace=config.get("workspace"),
            extra=config.get("extra", {}),
        )
    else:
        cfg = config

    if cfg.model is None and model is not None:
        cfg.model = model

    from submarine.agents.backend_agent import BackendAgent

    backend = _default_factory.create(role, cfg)
    return BackendAgent(
        role=role,
        backend=backend,
        model=cfg.model or model,
        system_prompt=cfg.system_prompt or system_prompt,
        tools=tools,
        timeout=timeout,
        metadata=metadata,
    )