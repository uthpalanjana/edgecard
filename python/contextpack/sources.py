"""
sources.py — DataSource ABC and ConnectionResult.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class ConnectionResult:
    success: bool
    message: str = ""


class DataSource(ABC):
    """Abstract base class for all data adapters."""

    def __init__(self, name: str, poll_interval_seconds: int = 30) -> None:
        self.name = name
        self.poll_interval_seconds = poll_interval_seconds

    @abstractmethod
    def poll(self) -> dict:
        """
        Poll the data source and return a dict mapping field_name -> Reading.
        NEVER raises — log errors and return stale readings.
        """

    @abstractmethod
    def test_connection(self) -> ConnectionResult:
        """Test connectivity to the data source."""

    def version(self) -> str:
        return "0.1.0"
