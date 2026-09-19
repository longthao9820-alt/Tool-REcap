"""Bounded JSON Schema validator for the published `schemas/v1` contracts.

This project has no third-party runtime dependencies, so instead of pretending to
support all of JSON Schema 2020-12 this module implements exactly the keyword
subset the published schemas use and *fails closed on any keyword it does not
know*. An unsupported keyword is a schema authoring error, never a silent pass.

Supported: `$ref` (local `#/$defs/...`), type, const, enum, required,
properties, additionalProperties (false or schema), items, minItems, maxItems,
uniqueItems, minimum, maximum, exclusiveMinimum, exclusiveMaximum, minLength,
pattern, multipleOf.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..domain.errors import SchemaValidationError, ValidationError

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "v1"

_IGNORED_KEYWORDS = frozenset({"$schema", "$id", "$defs", "title", "description", "examples"})
_SUPPORTED_KEYWORDS = frozenset(
    {
        "$ref",
        "type",
        "const",
        "enum",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "pattern",
        "multipleOf",
    }
)

_TYPE_CHECKS = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "null": lambda value: value is None,
}


class SchemaRegistry:
    """Loads and caches the published schemas by file name."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = Path(directory or SCHEMA_DIR)
        self._cache: dict[str, dict[str, Any]] = {}

    @property
    def directory(self) -> Path:
        return self._directory

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(path.stem for path in self._directory.glob("*.json")))

    def load(self, name: str) -> dict[str, Any]:
        if name not in self._cache:
            path = self._directory / f"{name}.json"
            if not path.is_file():
                raise SchemaValidationError(f"published schema not found: {name}")
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise SchemaValidationError(f"schema {name} is not valid JSON: {exc}") from exc
            if not isinstance(document, dict):
                raise SchemaValidationError(f"schema {name} is not an object")
            self._cache[name] = document
        return self._cache[name]

    def validate(self, name: str, document: Any) -> None:
        """Raise `SchemaValidationError` unless `document` satisfies schema `name`."""
        schema = self.load(name)
        errors: list[str] = []
        _validate(document, schema, schema, "$", errors)
        if errors:
            raise SchemaValidationError(f"{name}: " + "; ".join(errors))


_DEFAULT_REGISTRY = SchemaRegistry()


def validate_document(name: str, document: Any) -> None:
    """Validate against the default published schema directory."""
    _DEFAULT_REGISTRY.validate(name, document)


def _resolve(reference: str, root: dict[str, Any]) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise SchemaValidationError(f"unsupported $ref target: {reference}")
    node: Any = root
    for token in reference[2:].split("/"):
        if not isinstance(node, dict) or token not in node:
            raise SchemaValidationError(f"unresolvable $ref: {reference}")
        node = node[token]
    if not isinstance(node, dict):
        raise SchemaValidationError(f"$ref does not point at a schema: {reference}")
    return node


def _validate(
    value: Any, schema: dict[str, Any], root: dict[str, Any], path: str, errors: list[str]
) -> None:
    unsupported = set(schema) - _SUPPORTED_KEYWORDS - _IGNORED_KEYWORDS
    if unsupported:
        raise SchemaValidationError(
            f"{path}: schema uses unsupported keywords {sorted(unsupported)}"
        )

    if "$ref" in schema:
        _validate(value, _resolve(schema["$ref"], root), root, path, errors)

    if "type" in schema:
        expected = schema["type"]
        candidates = expected if isinstance(expected, list) else [expected]
        for candidate in candidates:
            if candidate not in _TYPE_CHECKS:
                raise SchemaValidationError(f"{path}: unsupported type {candidate!r}")
        if not any(_TYPE_CHECKS[candidate](value) for candidate in candidates):
            errors.append(f"{path} must be of type {expected!r}, got {type(value).__name__}")
            return

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']!r}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path} must be at least {schema['minLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path} must match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path} must be <= {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path} must be > {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path} must be < {schema['exclusiveMaximum']}")
        if "multipleOf" in schema and value % schema["multipleOf"] != 0:
            errors.append(f"{path} must be a multiple of {schema['multipleOf']}")

    if isinstance(value, dict):
        _validate_object(value, schema, root, path, errors)
    if isinstance(value, list):
        _validate_array(value, schema, root, path, errors)


def _validate_object(
    value: dict[str, Any],
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    for name in schema.get("required", []):
        if name not in value:
            errors.append(f"{path} is missing required property {name!r}")
    properties = schema.get("properties", {})
    for key, item in value.items():
        if key in properties:
            _validate(item, properties[key], root, f"{path}.{key}", errors)
            continue
        extra = schema.get("additionalProperties", True)
        if extra is False:
            errors.append(f"{path} has unexpected property {key!r}")
        elif isinstance(extra, dict):
            _validate(item, extra, root, f"{path}.{key}", errors)


def _validate_array(
    value: list[Any],
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        errors.append(f"{path} must contain at least {schema['minItems']} items")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        errors.append(f"{path} must contain at most {schema['maxItems']} items")
    if schema.get("uniqueItems") is True:
        seen: list[Any] = []
        for item in value:
            if item in seen:
                errors.append(f"{path} must contain unique items")
                break
            seen.append(item)
    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for index, item in enumerate(value):
            _validate(item, item_schema, root, f"{path}[{index}]", errors)
    elif item_schema is not None:
        raise SchemaValidationError(f"{path}: unsupported items schema {item_schema!r}")


def assert_supported_schemas(registry: SchemaRegistry | None = None) -> tuple[str, ...]:
    """Fail closed if any published schema uses an unsupported keyword."""
    active = registry or _DEFAULT_REGISTRY
    names = active.names()
    if not names:
        raise ValidationError(f"no published schemas found in {active.directory}")
    for name in names:
        _walk_keywords(active.load(name), name)
    return names


def _walk_keywords(node: Any, name: str) -> None:
    if isinstance(node, dict):
        unsupported = set(node) - _SUPPORTED_KEYWORDS - _IGNORED_KEYWORDS
        if unsupported and not _looks_like_container(node):
            raise SchemaValidationError(
                f"schema {name} uses unsupported keywords {sorted(unsupported)}"
            )
        for key, child in node.items():
            if key in ("properties", "$defs"):
                for sub in child.values():
                    _walk_keywords(sub, name)
            elif key in ("items", "additionalProperties"):
                _walk_keywords(child, name)
    return None


def _looks_like_container(node: dict[str, Any]) -> bool:
    """True for `properties`/`$defs` maps, whose keys are names, not keywords."""
    return not (set(node) & _SUPPORTED_KEYWORDS)
