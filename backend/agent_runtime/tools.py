from __future__ import annotations

import ast
import math
import operator
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class Tool:
    name: str
    version: str
    description: str
    schema: dict[str, Any]
    execute: Callable[[dict[str, Any]], dict[str, Any]]
    side_effect: str = "none"
    validate_arguments: Callable[[dict[str, Any]], None] | None = None


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def specs(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "version": t.version, "description": t.description,
                 "schema": t.schema, "sideEffect": t.side_effect} for t in self._tools.values()]

    def resolve(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @staticmethod
    def validate(tool: Tool, arguments: dict[str, Any]) -> None:
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        if tool.validate_arguments is not None:
            tool.validate_arguments(arguments)


_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _bounded_number(value: Any) -> float | int:
    if type(value) not in {int, float}:
        raise ValueError("calculator result is not a real number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("calculator value must be finite")
    if abs(value) > 1e100:
        raise ValueError("result is out of range")
    return value


def _calculate(node: ast.AST, depth: int = 0) -> float | int:
    if depth > 12:
        raise ValueError("expression is too complex")
    if isinstance(node, ast.Expression):
        return _calculate(node.body, depth + 1)
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        return _bounded_number(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        base = _calculate(node.left, depth + 1)
        exponent = _calculate(node.right, depth + 1)
        # Exponentiation must be bounded before Python evaluates it. Checking
        # the result afterwards still allows expressions such as 9**9**9 to
        # consume unbounded CPU and memory.
        if abs(exponent) > 100:
            raise ValueError("calculator exponent is out of range")
        try:
            return _bounded_number(operator.pow(base, exponent))
        except (OverflowError, ZeroDivisionError) as exc:
            raise ValueError("calculator exponent result is out of range") from exc
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        try:
            value = _BINARY[type(node.op)](
                _calculate(node.left, depth + 1), _calculate(node.right, depth + 1)
            )
        except (OverflowError, ZeroDivisionError) as exc:
            raise ValueError("calculator operation failed") from exc
        return _bounded_number(value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _bounded_number(_UNARY[type(node.op)](_calculate(node.operand, depth + 1)))
    raise ValueError("unsupported calculator expression")


def _calculator(arguments: dict[str, Any]) -> dict[str, Any]:
    expression = arguments["expression"]
    return {"expression": expression, "result": _calculate(ast.parse(expression, mode="eval"))}


def _validate_calculator(arguments: dict[str, Any]) -> None:
    if set(arguments) != {"expression"} or not isinstance(arguments.get("expression"), str):
        raise ValueError("calculator arguments must contain only a string expression")
    if not arguments["expression"].strip() or len(arguments["expression"]) > 200:
        raise ValueError("calculator expression must be 1-200 characters")


_EXCLUDED_PARTS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__"})
_SENSITIVE_DIRECTORIES = frozenset({
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", "gcloud",
})
_SENSITIVE_NAMES = frozenset({
    ".envrc", ".npmrc", ".pnpmrc", ".pypirc", ".netrc", ".git-credentials",
    ".yarnrc", ".yarnrc.yml", "credentials", "credentials.json", "secrets.json",
    "token", "token.json", "model-settings.json", "workspace.db",
    "application_default_credentials.json", "service-account.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
})
_SENSITIVE_KEY_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
_SENSITIVE_SUFFIXES = (
    ".pem", ".key", ".p12", ".pfx", ".jks", ".ppk", ".keystore", ".pkcs12",
)
_MAX_FILE_BYTES = 256 * 1024


def _is_sensitive(relative: Path) -> bool:
    """Conservatively exclude common credentials and WeavePath runtime data."""
    parts = [part.casefold() for part in relative.parts]
    if any(part in _SENSITIVE_DIRECTORIES for part in parts):
        return True
    name = relative.name.casefold()
    return (
        name.startswith(".env")
        or name in _SENSITIVE_NAMES
        or name.startswith(_SENSITIVE_KEY_PREFIXES)
        # SQLite may create WAL/SHM files, while backups and operator copies
        # commonly use suffixes such as ``workspace.db.backup``. Treat every
        # file in that database family as runtime data rather than readable
        # workspace content.
        or name.startswith("workspace.db")
        or name.endswith(_SENSITIVE_SUFFIXES)
    )


def _safe_relative(root: Path, raw: str, *, directory: bool = False) -> tuple[Path, str]:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 1_000:
        raise ValueError("path must be a non-empty relative path")
    supplied = Path(raw)
    if supplied.is_absolute() or supplied.drive or ".." in supplied.parts:
        raise ValueError("path must stay inside the configured workspace")
    try:
        target = (root / supplied).resolve(strict=True)
    except OSError as exc:
        raise ValueError("path does not exist or cannot be accessed") from exc
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes the configured workspace") from exc
    lowered = {part.casefold() for part in relative.parts}
    if lowered & _EXCLUDED_PARTS or _is_sensitive(relative):
        raise ValueError("path is excluded from agent access")
    if directory and not target.is_dir():
        raise ValueError("path is not a directory")
    if not directory and not target.is_file():
        raise ValueError("path is not a file")
    return target, relative.as_posix() or "."


def _read_text(path: Path) -> str:
    size = path.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise ValueError("file is too large to read safely")
    data = path.read_bytes()
    if b"\0" in data:
        raise ValueError("binary files are not supported")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("file is not UTF-8 text") from exc


def _workspace_tools(root_value: str | os.PathLike[str]) -> list[Tool]:
    root = Path(root_value).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("WEAVEPATH_WORKSPACE_ROOT must be a directory")

    def validate_read(arguments: dict[str, Any]) -> None:
        if set(arguments) != {"path"} or not isinstance(arguments.get("path"), str):
            raise ValueError("read_file requires only a string path")
        _safe_relative(root, arguments["path"])

    def read_file(arguments: dict[str, Any]) -> dict[str, Any]:
        target, relative = _safe_relative(root, arguments["path"])
        content = _read_text(target)
        return {"path": relative, "content": content, "size": target.stat().st_size}

    def validate_search(arguments: dict[str, Any]) -> None:
        if not set(arguments).issubset({"query", "path", "maxResults"}) or "query" not in arguments:
            raise ValueError("workspace_search requires query and optional path/maxResults")
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("search query must be 1-500 characters")
        if "path" in arguments:
            if not isinstance(arguments["path"], str):
                raise ValueError("search path must be a string")
            _safe_relative(root, arguments["path"], directory=True)
        limit = arguments.get("maxResults", 20)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("maxResults must be an integer from 1 to 50")

    def workspace_search(arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments["query"]
        limit = arguments.get("maxResults", 20)
        if "path" in arguments:
            base, _ = _safe_relative(root, arguments["path"], directory=True)
        else:
            base = root
        matches: list[dict[str, Any]] = []
        scanned_files = 0
        for current, dirs, files in os.walk(base, followlinks=False):
            current_path = Path(current)
            dirs[:] = sorted(name for name in dirs
                              if name.casefold() not in _EXCLUDED_PARTS
                              and not _is_sensitive((current_path / name).relative_to(root))
                              and not (current_path / name).is_symlink())
            for name in sorted(files):
                scanned_files += 1
                if scanned_files > 2_000:
                    return {"query": query, "matches": matches, "truncated": True}
                candidate = current_path / name
                if _is_sensitive(candidate.relative_to(root)) or candidate.is_symlink():
                    continue
                try:
                    resolved, relative = _safe_relative(root, str(candidate.relative_to(root)))
                    text = _read_text(resolved)
                except (OSError, ValueError):
                    continue
                for line_number, line in enumerate(text.splitlines(), 1):
                    if query.casefold() in line.casefold():
                        matches.append({"path": relative, "line": line_number,
                                        "preview": line[:500]})
                        if len(matches) >= limit:
                            return {"query": query, "matches": matches, "truncated": True}
        return {"query": query, "matches": matches, "truncated": False}

    return [
        Tool(name="workspace_search", version="1.0.0",
             description="Search UTF-8 text inside the explicitly configured workspace.",
             schema={"type": "object", "properties": {
                 "query": {"type": "string", "maxLength": 500},
                 "path": {"type": "string", "maxLength": 1000},
                 "maxResults": {"type": "integer", "minimum": 1, "maximum": 50}},
                 "required": ["query"], "additionalProperties": False},
             execute=workspace_search, validate_arguments=validate_search),
        Tool(name="read_file", version="1.0.0",
             description="Read one bounded UTF-8 text file inside the explicitly configured workspace.",
             schema={"type": "object", "properties": {
                 "path": {"type": "string", "maxLength": 1000}},
                 "required": ["path"], "additionalProperties": False},
             execute=read_file, validate_arguments=validate_read),
    ]


def _validate_patch_proposal(arguments: dict[str, Any]) -> None:
    if set(arguments) != {"path", "patch"}:
        raise ValueError("propose_patch requires only path and patch")
    path, patch = arguments.get("path"), arguments.get("patch")
    if (not isinstance(path, str) or not path.strip() or len(path) > 1_000
            or Path(path).is_absolute() or Path(path).drive or ".." in Path(path).parts):
        raise ValueError("patch path must be a safe relative path")
    if not isinstance(patch, str) or not patch.strip() or len(patch) > 200_000:
        raise ValueError("patch must be 1-200000 characters")


def _propose_patch(arguments: dict[str, Any]) -> dict[str, Any]:
    # This tool deliberately does not touch the filesystem. The runtime turns
    # its approved result into a versioned Artifact for later human review.
    return {"path": Path(arguments["path"]).as_posix(), "patch": arguments["patch"],
            "filesystemChanged": False}


def _patch_proposal_tool() -> Tool:
    return Tool(
        name="propose_patch", version="1.0.0",
        description="Propose a patch as a reviewable Artifact; never writes project files.",
        schema={"type": "object", "properties": {
            "path": {"type": "string", "maxLength": 1000},
            "patch": {"type": "string", "maxLength": 200000}},
            "required": ["path", "patch"], "additionalProperties": False},
        execute=_propose_patch, side_effect="artifact", validate_arguments=_validate_patch_proposal,
    )


def calculator_registry() -> ToolRegistry:
    return ToolRegistry([Tool(name="safe_calculator", version="1.0.0",
        description="Evaluate a deterministic arithmetic expression without side effects.",
        schema={"type": "object", "properties": {"expression": {"type": "string", "maxLength": 200}},
                "required": ["expression"], "additionalProperties": False}, execute=_calculator,
        validate_arguments=_validate_calculator)])


def runtime_registry(workspace_root: str | os.PathLike[str] | None = None) -> ToolRegistry:
    """Return preview tools without guessing a broad filesystem authority.

    File tools are intentionally absent unless the host explicitly configures a
    workspace root. This keeps a normal desktop launch from exposing the user's
    home directory merely because it happens to be the process working directory.
    """
    tools = [*calculator_registry()._tools.values(), _patch_proposal_tool()]
    if workspace_root:
        tools.extend(_workspace_tools(workspace_root))
    return ToolRegistry(tools)
