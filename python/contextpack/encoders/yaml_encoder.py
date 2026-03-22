"""
encoders/yaml_encoder.py — Encode a Card to YAML.
"""
from __future__ import annotations

import yaml
from datetime import datetime

from ..card import Card


class YamlEncoder:
    """Encodes a Card to a YAML string."""

    def encode(self, card: Card) -> str:
        data = self._card_to_dict(card)
        return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def _card_to_dict(self, card: Card) -> dict:
        """Convert Card to a plain dict suitable for YAML serialization."""
        raw = card.model_dump(mode="json")
        return raw
