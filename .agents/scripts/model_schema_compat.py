"""Compile response schemas for Codex while preserving local hard constraints."""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from typing import Any


MODEL_SCHEMA_KEYWORDS = {
    "$schema",
    "$id",
    "$ref",
    "$defs",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "anyOf",
    "description",
    "title",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
}


def model_compatible_schema(
    schema: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Move supported local-only constraints out of the model schema."""

    local_constraints: list[dict[str, str]] = []

    def visit(node: Any, path: str) -> Any:
        if isinstance(node, list):
            return [visit(item, path) for item in node]
        if not isinstance(node, dict):
            return copy.deepcopy(node)
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key == "uniqueItems":
                if value is True:
                    local_constraints.append(
                        {"keyword": "uniqueItems", "path": path}
                    )
                continue
            if key == "properties" and isinstance(value, dict):
                result[key] = {
                    name: visit(child, f"{path}.{name}")
                    for name, child in value.items()
                }
            elif key in {"items", "additionalProperties"}:
                result[key] = visit(value, f"{path}[*]")
            else:
                result[key] = copy.deepcopy(value)
        return result

    return visit(schema, "$"), local_constraints


def preflight_model_schema(schema: dict[str, Any]) -> None:
    """Reject schema keywords outside the tested Codex response-schema subset."""

    def visit(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        unknown = sorted(set(node) - MODEL_SCHEMA_KEYWORDS)
        if unknown:
            raise ValueError(
                "模型Schema预检失败："
                f"{path}包含未验收关键字{','.join(unknown)}"
            )
        for container in ("properties", "$defs"):
            children = node.get(container)
            if isinstance(children, dict):
                for name, child in children.items():
                    visit(child, f"{path}.{container}.{name}")
        for container in ("items", "additionalProperties"):
            child = node.get(container)
            if isinstance(child, dict):
                visit(child, f"{path}.{container}")
        branches = node.get("anyOf")
        if isinstance(branches, list):
            for index, child in enumerate(branches):
                visit(child, f"{path}.anyOf[{index}]")

    visit(schema, "$")


def validate_local_schema_constraints(
    value: Any,
    schema: dict[str, Any],
) -> None:
    """Validate constraints intentionally omitted from the model schema."""

    def json_identity(item: Any) -> Any:
        if item is None:
            return ("null",)
        if isinstance(item, bool):
            return ("boolean", item)
        if isinstance(item, (int, float)):
            return ("number", Decimal(str(item)).normalize())
        if isinstance(item, str):
            return ("string", item)
        if isinstance(item, list):
            return ("array", tuple(json_identity(value) for value in item))
        if isinstance(item, dict):
            return (
                "object",
                tuple(
                    (key, json_identity(value))
                    for key, value in sorted(item.items())
                ),
            )
        raise TypeError(f"不是JSON值：{type(item).__name__}")

    def visit(current: Any, node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        if node.get("uniqueItems") is True and isinstance(current, list):
            seen: set[Any] = set()
            for item in current:
                identity = json_identity(item)
                if identity in seen:
                    raise ValueError(f"本地Schema校验失败：{path}存在重复成员")
                seen.add(identity)
        properties = node.get("properties")
        if isinstance(current, dict) and isinstance(properties, dict):
            for name, child in properties.items():
                if name in current:
                    visit(current[name], child, f"{path}.{name}")
            known_names = set(properties)
        else:
            known_names = set()
        if isinstance(current, dict):
            pattern_properties = node.get("patternProperties")
            matched_names: set[str] = set()
            if isinstance(pattern_properties, dict):
                for pattern, child in pattern_properties.items():
                    for name, item in current.items():
                        if re.search(pattern, name):
                            visit(item, child, f"{path}.{name}")
                            matched_names.add(name)
            additional = node.get("additionalProperties")
            if isinstance(additional, dict):
                for name, item in current.items():
                    if name not in known_names and name not in matched_names:
                        visit(item, additional, f"{path}.{name}")
        items = node.get("items")
        if isinstance(current, list) and isinstance(items, dict):
            for index, item in enumerate(current):
                visit(item, items, f"{path}[{index}]")

    visit(value, schema, "$")
