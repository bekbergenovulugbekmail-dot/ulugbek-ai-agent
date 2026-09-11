"""Arithmetic evaluation.

The expression is parsed into an AST and walked with an explicit allow-list of
node types and operators. ``eval``/``exec`` are never used, so no name lookup,
attribute access, call or import can occur — the worst a malicious expression
can do is be rejected.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any, Callable, Final

from ulugbek_ai.core.enums import PermissionLevel
from ulugbek_ai.tools.base import Tool, ToolContext, ToolResult

_BINARY_OPS: Final[dict[type[ast.operator], Callable[[Any, Any], Any]]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: Final[dict[type[ast.unaryop], Callable[[Any], Any]]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

#: Named constants the expression may reference.
_CONSTANTS: Final[dict[str, float]] = {"pi": math.pi, "e": math.e, "tau": math.tau}

#: Guard against expressions like ``9**9**9`` exhausting memory.
_MAX_EXPONENT: Final[int] = 1_000
_MAX_EXPRESSION_CHARS: Final[int] = 500


class CalculatorTool(Tool):
    name = "calculate"
    description = (
        "Evaluate an arithmetic expression and return the numeric result. "
        "Supports + - * / // % ** , parentheses and the constants pi, e, tau. "
        "Use it instead of doing arithmetic in your head."
    )
    permission = PermissionLevel.READ
    timeout_seconds = 5.0
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Arithmetic expression, e.g. '(12 + 5) * 3'.",
                "minLength": 1,
                "maxLength": _MAX_EXPRESSION_CHARS,
            }
        },
        "required": ["expression"],
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "expression": {"type": "string"},
            "result": {"type": "number"},
        },
        "required": ["expression", "result"],
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        expression: str = arguments["expression"]
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            return ToolResult.failure(f"Could not parse expression: {exc.msg}")

        try:
            value = _evaluate(tree.body)
        except ZeroDivisionError:
            return ToolResult.failure("Division by zero.")
        except ValueError as exc:
            return ToolResult.failure(str(exc))
        except OverflowError:
            return ToolResult.failure("Result is too large to represent.")

        return ToolResult.success(
            {"expression": expression, "result": value}, evaluated=True
        )


def _evaluate(node: ast.AST) -> float | int:
    """Recursively evaluate an allow-listed arithmetic AST."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"Unsupported literal: {node.value!r}")
        return node.value

    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise ValueError(f"Unknown name: {node.id!r}")

    if isinstance(node, ast.UnaryOp):
        handler = _UNARY_OPS.get(type(node.op))
        if handler is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return handler(_evaluate(node.operand))

    if isinstance(node, ast.BinOp):
        handler = _BINARY_OPS.get(type(node.op))
        if handler is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ValueError(f"Exponent larger than {_MAX_EXPONENT} is not allowed.")
        return handler(left, right)

    raise ValueError(f"Unsupported expression element: {type(node).__name__}")
