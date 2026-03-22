"""
validator.py — Validate Card objects against the JSON schema.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .card import Card

logger = logging.getLogger(__name__)

# Path to the schema file (relative to this package)
_SCHEMA_PATH = Path(__file__).parent.parent.parent / "schema" / "contextpack-v1.schema.json"


class CardValidationError(Exception):
    """Raised when a Card fails schema validation."""


class Validator:
    """
    Validates Card objects against the contextpack JSON schema.
    """

    def __init__(self, schema_path: Path | None = None) -> None:
        self._schema_path = schema_path or _SCHEMA_PATH
        self._schema: dict | None = None

    def _load_schema(self) -> dict:
        if self._schema is None:
            with open(self._schema_path) as fh:
                self._schema = json.load(fh)
        return self._schema

    def validate(self, card: Card) -> None:
        """
        Validate a Card against the JSON schema.
        Raises CardValidationError on failure.
        """
        try:
            import jsonschema
        except ImportError:
            logger.warning("jsonschema not available; skipping schema validation")
            return

        schema = self._load_schema()
        # Exclude None values so optional fields (like provenance) are absent rather than null
        data = json.loads(card.model_dump_json(exclude_none=True))

        try:
            jsonschema.validate(instance=data, schema=schema)
        except jsonschema.ValidationError as exc:
            raise CardValidationError(f"Card validation failed: {exc.message}") from exc
        except jsonschema.SchemaError as exc:
            raise CardValidationError(f"Schema error: {exc.message}") from exc

    def validate_schema_version(self, version: str) -> bool:
        """Check if a schema version is compatible."""
        return version == "1.0"

    def validate_dict(self, data: dict) -> None:
        """Validate a raw dict against the schema."""
        try:
            import jsonschema
        except ImportError:
            logger.warning("jsonschema not available; skipping schema validation")
            return

        schema = self._load_schema()
        try:
            jsonschema.validate(instance=data, schema=schema)
        except jsonschema.ValidationError as exc:
            raise CardValidationError(f"Card validation failed: {exc.message}") from exc
