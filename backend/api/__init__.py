from __future__ import annotations

from typing import Any

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
    """Load the FastAPI application lazily.

    Runtime adapters import ``api.llm``. Importing the application eagerly from
    the package initializer would make that dependency loop back into
    ``agent_runtime`` while it is still being initialized.
    """
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(name)
