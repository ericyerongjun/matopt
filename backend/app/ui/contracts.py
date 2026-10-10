"""The server-owned version 1 UI wire contract.

Generated interfaces and JSON Schema come from these Pydantic models. Native
nodes are data, never executable templates; active HTML belongs only in the
separately hosted opaque-origin mini-app runtime.
"""
from __future__ import annotations

import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

UI_VERSION = 1
_UNSAFE_KEYS = {"__proto__", "constructor", "prototype"}
_STATE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")


def validate_json_keys(value):
    if isinstance(value, dict):
        if set(value) & _UNSAFE_KEYS:
            raise ValueError("Unsafe object key in UI data")
        for child in value.values():
            validate_json_keys(child)
    elif isinstance(value, list):
        for child in value:
            validate_json_keys(child)

NODE_TYPES = (
    "markdown", "equation", "comparison", "table", "plot", "mermaid", "tabs",
    "disclosure", "slider", "number", "select", "toggle", "button", "stack", "grid",
)
NodeType = Literal["markdown", "equation", "comparison", "table", "plot", "mermaid", "tabs", "disclosure", "slider", "number", "select", "toggle", "button", "stack", "grid"]
Identifier = Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UINode(WireModel):
    id: Identifier
    type: NodeType
    props: dict[str, JsonValue] = Field(default_factory=dict)
    children: list["UINode"] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def validate_props(self):
        p = self.props
        if any(k.lower().startswith("on") or k in {"dangerouslySetInnerHTML", "innerHTML", "srcDoc"} for k in p):
            raise ValueError("Native UI props cannot contain HTML or event handlers")
        required_text = {"markdown": "text", "equation": "latex", "mermaid": "code"}.get(self.type)
        if required_text and not (isinstance(p.get(required_text), str) or
                (isinstance(p.get(required_text), dict) and set(p[required_text]) == {"$state"} and isinstance(p[required_text]["$state"], str))):
            raise ValueError(f"{self.type} requires a string or state binding for {required_text}")
        if self.type in {"slider", "number", "select", "toggle"}:
            if not isinstance(p.get("stateKey"), str) or not _STATE_KEY.fullmatch(p["stateKey"]) or p["stateKey"] in _UNSAFE_KEYS:
                raise ValueError("Every control requires a stateKey")
            if not isinstance(p.get("label"), str) or not p["label"].strip():
                raise ValueError("Every control requires an accessible label")
        if self.type in {"slider", "number"}:
            for k in ("min", "max", "step", "value"):
                if k in p and (type(p[k]) not in (int, float) or not math.isfinite(p[k])):
                    raise ValueError(f"{k} must be a finite number")
            if "min" in p and "max" in p and p["min"] > p["max"]:
                raise ValueError("Control min must not exceed max")
            if "step" in p and p["step"] <= 0:
                raise ValueError("Control step must be positive")
        if self.type == "select" and (not isinstance(p.get("options"), list) or not p["options"]):
            raise ValueError("Select requires options")
        if self.type == "plot" and not isinstance(p.get("data"), list):
            raise ValueError("Plot requires a data array")
        if self.type == "table" and (not isinstance(p.get("columns"), list) or not isinstance(p.get("rows"), list)):
            raise ValueError("Table requires columns and rows arrays")
        if self.type == "button" and not isinstance(p.get("actionId"), str):
            raise ValueError("Action button requires actionId")
        return self


class UIAction(WireModel):
    id: Identifier
    label: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    input_schema: dict[str, JsonValue] = Field(default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False})
    handler_version: int = Field(default=1, ge=1)


class UIApp(WireModel):
    html: str = Field(min_length=1, max_length=524288)


class UISurface(WireModel):
    version: Literal[1] = 1
    id: Identifier
    revision: int = Field(default=1, ge=1)
    kind: Literal["native", "mini_app"] = "native"
    title: str = Field(min_length=1, max_length=200)
    fallback: str = Field(min_length=1, max_length=20000)
    status: Literal["preview", "committed", "error"] = "committed"
    nodes: list[UINode] = Field(default_factory=list, max_length=128)
    app: UIApp | None = None
    state: dict[str, JsonValue] = Field(default_factory=dict)
    computed_state: dict[str, JsonValue] | None = None
    actions: list[UIAction] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_tree(self):
        if self.kind == "native" and self.app is not None:
            raise ValueError("Native surfaces cannot contain executable HTML")
        if self.kind == "mini_app" and self.app is None:
            raise ValueError("Mini-app surfaces require complete HTML")
        seen: set[str] = set()
        def walk(nodes, depth):
            if depth > 8:
                raise ValueError("UI nesting exceeds 8 levels")
            for node in nodes:
                if node.id in seen:
                    raise ValueError(f"Duplicate node id: {node.id}")
                seen.add(node.id)
                if len(seen) > 128:
                    raise ValueError("UI exceeds 128 nodes")
                walk(node.children, depth + 1)
        walk(self.nodes, 0)
        if len({a.id for a in self.actions}) != len(self.actions):
            raise ValueError("Action ids must be unique")
        validate_json_keys(self.model_dump(mode="json"))
        raw = json.dumps(self.model_dump(mode="json"), allow_nan=False)
        if len(raw.encode()) > 1048576:
            raise ValueError("UI payload exceeds 1 MiB")
        return self


class UIStateRequest(WireModel):
    revision: int = Field(ge=1)
    state: dict[str, JsonValue]


class UIActionRequest(WireModel):
    action_id: Identifier
    revision: int = Field(ge=1)
    values: dict[str, JsonValue] = Field(default_factory=dict)
    idempotency_key: Identifier


def walk_nodes(nodes):
    for node in nodes:
        yield node
        yield from walk_nodes(node.get("children", []))


def validate_state(surface: dict, values: dict) -> dict:
    """Validate only declared state keys, including native control domains."""
    validate_json_keys(values)
    json.dumps(values, allow_nan=False)
    if len(json.dumps(values).encode()) > 65536:
        raise ValueError("UI state exceeds 64 KiB")
    controls = {n["props"]["stateKey"]: n for n in walk_nodes(surface.get("nodes", []))
                if n["type"] in {"slider", "number", "select", "toggle"}}
    unknown = set(values) - set(surface.get("state", {})) - set(controls)
    if unknown:
        raise ValueError("Undeclared UI state keys: " + ", ".join(sorted(unknown)))
    for key, value in values.items():
        node = controls.get(key)
        if not node:
            continue
        props = node["props"]
        if node["type"] in {"slider", "number"}:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number")
            if ("min" in props and value < props["min"]) or ("max" in props and value > props["max"]):
                raise ValueError(f"{key} is outside its declared range")
        elif node["type"] == "toggle" and type(value) is not bool:
            raise ValueError(f"{key} must be boolean")
        elif node["type"] == "select":
            allowed = [(o.get("value") if o.get("value") is not None else o.get("label")) if isinstance(o, dict) else o for o in props["options"]]
            # Match the renderer's JSON identity, not Python's True == 1.
            encoded = json.dumps(value, sort_keys=True, allow_nan=False)
            if not any(encoded == json.dumps(option, sort_keys=True, allow_nan=False) for option in allowed):
                raise ValueError(f"{key} is not an available option")
    return {**surface.get("state", {}), **values}
