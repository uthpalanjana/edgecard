"""
adapters/file.py — FileAdapter for reading CSV or JSON data files.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..card import Quality, Reading
from ..sources import ConnectionResult, DataSource

logger = logging.getLogger(__name__)


class FileAdapter(DataSource):
    """
    Reads readings from a CSV or JSON file.

    CSV format: timestamp,field_name,value,unit
    JSON format: {field_name: {value: x, unit: y, timestamp: z}}
    """

    def __init__(
        self,
        name: str,
        path: str | Path,
        format: str = "json",
        watch: bool = True,
        poll_interval_seconds: int = 30,
    ) -> None:
        super().__init__(name=name, poll_interval_seconds=poll_interval_seconds)
        self.path = Path(path)
        self.format = format.lower()
        self.watch = watch
        self._last_readings: dict[str, Reading] = {}

    def poll(self) -> dict[str, Reading]:
        """Returns readings from file. Returns stale on error."""
        try:
            if self.format == "csv":
                return self._read_csv()
            elif self.format == "json":
                return self._read_json()
            else:
                logger.error("FileAdapter '%s': unsupported format '%s'", self.name, self.format)
                return self._stale_copy()
        except FileNotFoundError:
            logger.error("FileAdapter '%s': file not found: %s", self.name, self.path)
            return self._stale_copy()
        except Exception as exc:
            logger.error("FileAdapter '%s': parse error: %s", self.name, exc)
            return self._stale_copy()

    def _read_csv(self) -> dict[str, Reading]:
        results: dict[str, Reading] = {}
        # Latest value per field (last wins)
        with open(self.path, newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                field_name = row.get("field_name", "").strip()
                if not field_name:
                    continue
                raw_val = row.get("value", "").strip()
                unit = row.get("unit", "").strip() or None
                ts_str = row.get("timestamp", "").strip()
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else datetime.now(timezone.utc)
                except ValueError:
                    ts = datetime.now(timezone.utc)
                value = _coerce(raw_val)
                results[field_name] = Reading(
                    value=value,
                    unit=unit,
                    quality=Quality.measured,
                    timestamp=ts,
                )
        self._last_readings = results
        return results

    def _read_json(self) -> dict[str, Reading]:
        results: dict[str, Reading] = {}
        with open(self.path) as fh:
            data = json.load(fh)
        for field_name, info in data.items():
            ts_str = info.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00")) if ts_str else datetime.now(timezone.utc)
            except ValueError:
                ts = datetime.now(timezone.utc)
            results[field_name] = Reading(
                value=_coerce(info.get("value")),
                unit=info.get("unit") or None,
                quality=Quality.measured,
                timestamp=ts,
            )
        self._last_readings = results
        return results

    def _stale_copy(self) -> dict[str, Reading]:
        """Return a copy of the last known readings, all marked stale."""
        now = datetime.now(timezone.utc)
        return {
            k: Reading(
                value=r.value,
                unit=r.unit,
                quality=Quality.stale,
                timestamp=now,
            )
            for k, r in self._last_readings.items()
        }

    def test_connection(self) -> ConnectionResult:
        if not self.path.exists():
            return ConnectionResult(success=False, message=f"File not found: {self.path}")
        if not self.path.is_file():
            return ConnectionResult(success=False, message=f"Path is not a file: {self.path}")
        try:
            with open(self.path) as fh:
                fh.read(1)
            return ConnectionResult(success=True, message=f"File readable: {self.path}")
        except PermissionError as exc:
            return ConnectionResult(success=False, message=str(exc))


def _coerce(value):
    """Try to convert a string value to int, float, bool, or keep as string."""
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return value
    s = str(value).strip()
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    try:
        i = int(s)
        return i
    except ValueError:
        pass
    try:
        f = float(s)
        return f
    except ValueError:
        pass
    return s
