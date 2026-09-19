"""The published `schemas/v1` contracts and the bounded validator that enforces them."""

from __future__ import annotations

import json

import pytest

from recap_core.domain.errors import SchemaValidationError, ValidationError
from recap_core.schemas.validator import (
    SchemaRegistry,
    assert_supported_schemas,
    validate_document,
)

EXPECTED_SCHEMAS = {
    "transcript",
    "shots",
    "scenes",
    "events",
    "story-graph",
    "recap-plan",
}


def test_every_stage_document_has_a_published_schema():
    assert EXPECTED_SCHEMAS <= set(SchemaRegistry().names())


def test_published_schemas_use_only_supported_keywords():
    assert set(assert_supported_schemas()) >= EXPECTED_SCHEMAS


def test_unknown_schema_fails_closed():
    with pytest.raises(SchemaValidationError):
        validate_document("does-not-exist", {})


def test_empty_schema_directory_fails_closed(tmp_path):
    with pytest.raises(ValidationError):
        assert_supported_schemas(SchemaRegistry(tmp_path))


def registry_with(tmp_path, name: str, schema: dict) -> SchemaRegistry:
    (tmp_path / f"{name}.json").write_text(json.dumps(schema), encoding="utf-8")
    return SchemaRegistry(tmp_path)


def test_validator_rejects_unsupported_keywords_instead_of_passing(tmp_path):
    registry = registry_with(tmp_path, "risky", {"type": "object", "oneOf": [{"type": "object"}]})
    with pytest.raises(SchemaValidationError):
        registry.validate("risky", {})
    with pytest.raises(SchemaValidationError):
        assert_supported_schemas(registry)


def test_validator_enforces_the_keyword_subset_it_claims(tmp_path):
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "count", "tags", "kind"],
        "properties": {
            "id": {"type": "string", "pattern": "^[a-f0-9]{4}$", "minLength": 4},
            "count": {"type": "integer", "minimum": 1, "maximum": 3, "multipleOf": 1},
            "ratio": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
            "tags": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "uniqueItems": True,
                "items": {"type": "string"},
            },
            "kind": {"enum": ["A", "B"]},
            "version": {"const": 1},
            "nullable": {"type": ["string", "null"]},
        },
    }
    registry = registry_with(tmp_path, "sample", schema)
    valid = {"id": "abcd", "count": 2, "tags": ["x"], "kind": "A", "nullable": None}
    registry.validate("sample", valid)

    for broken in (
        {**valid, "id": "zzzz"},
        {**valid, "count": 9},
        {**valid, "count": "2"},
        {**valid, "tags": []},
        {**valid, "tags": ["x", "x"]},
        {**valid, "tags": ["x", "y", "z"]},
        {**valid, "kind": "C"},
        {**valid, "rogue": 1},
        {**valid, "version": 2},
        {**valid, "ratio": 1},
        {**valid, "nullable": 5},
        {k: v for k, v in valid.items() if k != "id"},
    ):
        with pytest.raises(SchemaValidationError):
            registry.validate("sample", broken)


def test_validator_resolves_local_refs_and_rejects_remote_ones(tmp_path):
    registry = registry_with(
        tmp_path,
        "reffed",
        {
            "type": "object",
            "required": ["child"],
            "properties": {"child": {"$ref": "#/$defs/Child"}},
            "$defs": {"Child": {"type": "integer", "minimum": 0}},
        },
    )
    registry.validate("reffed", {"child": 1})
    with pytest.raises(SchemaValidationError):
        registry.validate("reffed", {"child": -1})

    remote = registry_with(
        tmp_path, "remote", {"type": "object", "properties": {"x": {"$ref": "https://x/y.json"}}}
    )
    with pytest.raises(SchemaValidationError):
        remote.validate("remote", {"x": 1})


def test_malformed_schema_file_fails_closed(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SchemaValidationError):
        SchemaRegistry(tmp_path).validate("broken", {})
