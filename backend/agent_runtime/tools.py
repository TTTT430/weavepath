from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Tool:
    name: str
    version: str
    description: str
    schema: dict[str, Any]
    execute: Callable[[dict[str, Any]], dict[str, Any]]
    side_effect: str = "none"


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
        if set(arguments) != {"expression"} or not isinstance(arguments.get("expression"), str):
            raise ValueError("calculator arguments must contain only a string expression")
        if not arguments["expression"].strip() or len(arguments["expression"]) > 200:
            raise ValueError("calculator expression must be 1-200 characters")


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


def calculator_registry() -> ToolRegistry:
    return ToolRegistry([Tool(name="safe_calculator", version="1.0.0",
        description="Evaluate a deterministic arithmetic expression without side effects.",
        schema={"type": "object", "properties": {"expression": {"type": "string", "maxLength": 200}},
                "required": ["expression"], "additionalProperties": False}, execute=_calculator)])
