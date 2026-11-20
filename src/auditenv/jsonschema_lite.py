"""Dependency-free validator for the JSON Schema subset used in src/auditenv/schemas.

Supported keywords: $ref (to #/$defs/...), type (string or list), const, enum, required, properties,
additionalProperties (bool or schema), items, minItems, maxItems, minLength, maxLength (Unicode code
points), pattern, minimum, maximum, anyOf. "integer" excludes booleans and floats; "number" excludes
booleans.
"""

import json
import os
import re
from typing import Any, Dict, List

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas")

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}
_SUPPORTED = {"$schema", "$id", "$ref", "$defs", "title", "description", "type", "const", "enum", "required",
              "properties", "additionalProperties", "items", "minItems", "maxItems", "minLength", "maxLength",
              "pattern", "minimum", "maximum", "anyOf"}


def load(name: str) -> Dict[str, Any]:
    with open(os.path.join(SCHEMA_DIR, f"{name}.schema.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _check_keywords(schema: Any, where: str) -> None:
    if isinstance(schema, dict):
        unknown = set(schema) - _SUPPORTED
        if unknown and not where.endswith("properties"):
            raise ValueError(f"unsupported schema keywords {sorted(unknown)} at {where}")
        for key, value in schema.items():
            if key in ("properties", "$defs"):
                for name, sub in value.items():
                    _check_keywords(sub, f"{where}/{key}/{name}")
            elif key in ("items", "additionalProperties") and isinstance(value, dict):
                _check_keywords(value, f"{where}/{key}")
            elif key == "anyOf":
                for n, sub in enumerate(value):
                    _check_keywords(sub, f"{where}/anyOf/{n}")


def validate(instance: Any, schema: Dict[str, Any]) -> List[str]:
    """All violations as 'path: message' strings; an empty list means valid."""
    _check_keywords(schema, "#")
    root = schema
    errors: List[str] = []

    def resolve(s: Dict[str, Any]) -> Dict[str, Any]:
        while "$ref" in s:
            ref = s["$ref"]
            if not ref.startswith("#/$defs/"):
                raise ValueError(f"unsupported $ref {ref}")
            s = root["$defs"][ref[len("#/$defs/"):]]
        return s

    def walk(value: Any, s: Dict[str, Any], path: str) -> None:
        s = resolve(s)
        if "anyOf" in s:
            if not any(not _errors(value, sub) for sub in s["anyOf"]):
                errors.append(f"{path}: does not match any allowed form")
            return
        if "type" in s:
            types = s["type"] if isinstance(s["type"], list) else [s["type"]]
            if not any(_TYPES[t](value) for t in types):
                errors.append(f"{path}: expected {'/'.join(types)}, got {type(value).__name__}")
                return
        if "const" in s and value != s["const"]:
            errors.append(f"{path}: must equal {s['const']!r}")
        if "enum" in s and value not in s["enum"]:
            errors.append(f"{path}: must be one of {s['enum']}")
        if isinstance(value, str):
            if "minLength" in s and len(value) < s["minLength"]:
                errors.append(f"{path}: shorter than {s['minLength']}")
            if "maxLength" in s and len(value) > s["maxLength"]:
                errors.append(f"{path}: longer than {s['maxLength']}")
            if "pattern" in s and not re.search(s["pattern"], value):
                errors.append(f"{path}: does not match {s['pattern']}")
        if _TYPES["number"](value):
            if "minimum" in s and value < s["minimum"]:
                errors.append(f"{path}: below {s['minimum']}")
            if "maximum" in s and value > s["maximum"]:
                errors.append(f"{path}: above {s['maximum']}")
        if isinstance(value, list):
            if "minItems" in s and len(value) < s["minItems"]:
                errors.append(f"{path}: fewer than {s['minItems']} items")
            if "maxItems" in s and len(value) > s["maxItems"]:
                errors.append(f"{path}: more than {s['maxItems']} items")
            if "items" in s:
                for n, item in enumerate(value):
                    walk(item, s["items"], f"{path}[{n}]")
        if isinstance(value, dict):
            for key in s.get("required", []):
                if key not in value:
                    errors.append(f"{path}: missing required '{key}'")
            props = s.get("properties", {})
            extra = s.get("additionalProperties", True)
            for key, item in value.items():
                if key in props:
                    walk(item, props[key], f"{path}.{key}")
                elif extra is False:
                    errors.append(f"{path}: unexpected property '{key}'")
                elif isinstance(extra, dict):
                    walk(item, extra, f"{path}.{key}")

    def _errors(value: Any, sub: Dict[str, Any]) -> List[str]:
        saved = list(errors)
        errors.clear()
        walk(value, sub, "")
        found = list(errors)
        errors[:] = saved
        return found

    walk(instance, schema, "$")
    return errors


def validate_named(instance: Any, name: str) -> List[str]:
    return validate(instance, load(name))
