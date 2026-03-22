"""
encoders/json_encoder.py — Encode a Card to JSON.
"""
from __future__ import annotations

import json

from ..card import Card


class JsonEncoder:
    """Encodes a Card to a JSON string."""

    def encode(self, card: Card, indent: int = 2) -> str:
        return card.model_dump_json(indent=indent)
