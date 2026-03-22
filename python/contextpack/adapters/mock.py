"""
adapters/mock.py — MockAdapter for testing and simulation.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Union

from ..card import MockField, Quality, Reading
from ..sources import ConnectionResult, DataSource

logger = logging.getLogger(__name__)


class MockAdapter(DataSource):
    """
    A mock data source that returns simulated readings.
    Supports static values, callable value functions, and gaussian noise.
    """

    def __init__(
        self,
        name: str,
        fields: dict[str, MockField],
        poll_interval_seconds: int = 30,
    ) -> None:
        super().__init__(name=name, poll_interval_seconds=poll_interval_seconds)
        self._fields = fields
        # Set up per-field random generators for reproducibility
        self._rngs: dict[str, random.Random] = {}
        for field_name, mock_field in fields.items():
            if mock_field.seed is not None:
                self._rngs[field_name] = random.Random(mock_field.seed)
            else:
                self._rngs[field_name] = random.Random()

    def poll(self) -> dict[str, Reading]:
        """Always succeeds, returns quality=simulated."""
        results: dict[str, Reading] = {}
        now = datetime.now(timezone.utc)

        for field_name, mock_field in self._fields.items():
            try:
                raw_value = mock_field.get_value()
                value = raw_value

                # Apply gaussian noise if configured (only for numeric values)
                if mock_field.noise_sigma > 0.0 and isinstance(raw_value, (int, float)):
                    rng = self._rngs.get(field_name, random.Random())
                    noise = rng.gauss(0.0, mock_field.noise_sigma)
                    value = raw_value + noise

                results[field_name] = Reading(
                    value=value,
                    unit=mock_field.unit,
                    quality=Quality.simulated,
                    timestamp=now,
                )
            except Exception as exc:
                logger.warning("MockAdapter '%s': error generating field '%s': %s", self.name, field_name, exc)
                results[field_name] = Reading(
                    value=0,
                    unit=mock_field.unit,
                    quality=Quality.stale,
                    timestamp=now,
                )

        return results

    def test_connection(self) -> ConnectionResult:
        return ConnectionResult(success=True, message="Mock adapter always connected")
