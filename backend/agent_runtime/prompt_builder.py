"""Backward-compatible imports for the cache-aware context contract.

New code should import :mod:`agent_runtime.context`.  AgentRuntimeService keeps
this module path during the Runtime v2 rollout so the P0 context change remains
isolated from the concurrent service lifecycle work.
"""

from agent_runtime.context import (PROMPT_LAYOUT_VERSION, SYSTEM_POLICY,
                                   assemble_agent_context, build_agent_messages,
                                   canonical_tool_specs, model_request_sha256,
                                   stable_prefix_sha256, stable_tool_specs)

__all__ = [
    "PROMPT_LAYOUT_VERSION",
    "SYSTEM_POLICY",
    "assemble_agent_context",
    "build_agent_messages",
    "canonical_tool_specs",
    "model_request_sha256",
    "stable_prefix_sha256",
    "stable_tool_specs",
]
