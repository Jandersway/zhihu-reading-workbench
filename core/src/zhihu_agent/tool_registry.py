"""Small strict tool boundary; supports only the JSON Schema subset used here."""
from __future__ import annotations

import json
from typing import Callable


def validate(value, schema: dict, path: str = "arguments") -> None:
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if kind not in types or type(value) is not types[kind]:
        raise ValueError(f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: invalid choice")
    if kind == "object":
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - value.keys():
            raise ValueError(f"{path}: missing required fields")
        if schema.get("additionalProperties") is False and value.keys() - props.keys():
            raise ValueError(f"{path}: unexpected fields")
        for key, item in value.items():
            if key in props:
                validate(item, props[key], f"{path}.{key}")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
            raise ValueError(f"{path}: invalid item count")
        for item in value:
            validate(item, schema["items"], f"{path}[]")
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 4000):
            raise ValueError(f"{path}: invalid text length")
    elif kind == "integer":
        if value < schema.get("minimum", 0) or value > schema.get("maximum", 10000000):
            raise ValueError(f"{path}: integer out of range")


class ToolRegistry:
    def __init__(self):
        self.entries: dict[str, tuple[dict, Callable]] = {}

    def register(self, schema: dict, handler: Callable) -> None:
        name = schema["function"]["name"]
        if name in self.entries:
            raise ValueError("Duplicate tool registration")
        self.entries[name] = (schema, handler)

    def schemas(self, allowed: set[str]) -> list[dict]:
        return [schema for name, (schema, _) in self.entries.items() if name in allowed]

    def execute(self, name: str, raw_args: str, allowed: set[str]) -> dict:
        if name not in allowed or name not in self.entries:
            raise ValueError("Tool is not allowed in this workflow stage")
        if len(raw_args) > 16000:
            raise ValueError("Tool arguments exceed size limit")
        args = json.loads(raw_args)
        schema, handler = self.entries[name]
        validate(args, schema["function"]["parameters"])
        return handler(**args)
