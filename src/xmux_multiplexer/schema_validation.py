"""Validate payloads against the team's local shared JSON Schemas."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource


class SharedSchemaValidator:
    def __init__(self, schema_directory: str):
        directory = Path(schema_directory)
        schemas = {}
        registry = Registry()
        for path in sorted(directory.glob("*.schema.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            schema_id = document["$id"]
            schemas[path.name.removesuffix(".schema.json")] = document
            registry = registry.with_resource(schema_id, Resource.from_contents(document))
        if not schemas:
            raise ValueError("shared_schemas_not_found")
        self._schemas = schemas
        self._registry = registry
        self._checker = FormatChecker()

    def validate(self, name: str, payload: object) -> None:
        schema = self._schemas.get(name)
        if schema is None:
            raise ValueError("shared_schema_not_found")
        validator = Draft202012Validator(schema, registry=self._registry, format_checker=self._checker)
        try:
            validator.validate(payload)
        except ValidationError as exc:
            raise ValueError("shared_schema_validation_failed") from exc
