"""
history.py — HistoryStore: JSON-backed, thread-safe event storage.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .card import HistoryEvent, EventType, Severity

logger = logging.getLogger(__name__)


class HistoryStore:
    """
    Persists HistoryEvent objects to a JSON file.
    Thread-safe via an internal lock.

    Storage path: {output_dir}/{card_id}.history.json
    """

    def __init__(self, storage_path: Path) -> None:
        self.storage_path = Path(storage_path)
        self._lock = threading.Lock()
        self._events: list[HistoryEvent] = []
        self._load()

    def _load(self) -> None:
        if self.storage_path.exists():
            try:
                with open(self.storage_path) as fh:
                    data = json.load(fh)
                self._events = [HistoryEvent(**e) for e in data]
                logger.debug("HistoryStore: loaded %d events from %s", len(self._events), self.storage_path)
            except Exception as exc:
                logger.error("HistoryStore: could not load %s: %s", self.storage_path, exc)
                self._events = []

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            raw = [
                json.loads(e.model_dump_json())
                for e in self._events
            ]
            with open(self.storage_path, "w") as fh:
                json.dump(raw, fh, default=str, indent=2)
        except Exception as exc:
            logger.error("HistoryStore: could not save %s: %s", self.storage_path, exc)

    def append_event(self, event: HistoryEvent) -> None:
        with self._lock:
            self._events.append(event)
            self._save()

    def get_recent(self, n: int) -> list[HistoryEvent]:
        with self._lock:
            return list(self._events[-n:])

    def get_all(self) -> list[HistoryEvent]:
        with self._lock:
            return list(self._events)

    def get_summary(self, days: int = 30) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self._lock:
            recent = [e for e in self._events if _event_ts(e) >= cutoff]

        if not recent:
            return f"No events in the last {days} days."

        counts: dict[str, int] = {}
        for e in recent:
            et = e.event_type if isinstance(e.event_type, str) else e.event_type.value
            counts[et] = counts.get(et, 0) + 1

        parts = [f"{k}: {v}" for k, v in sorted(counts.items())]
        return f"Last {days} days ({len(recent)} events): " + ", ".join(parts)

    def get_fault_count(self, event_type: str, days: int = 30) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self._lock:
            return sum(
                1 for e in self._events
                if _event_ts(e) >= cutoff
                and (e.event_type == event_type or (hasattr(e.event_type, "value") and e.event_type.value == event_type))
            )

    def trim(self, max_events: int) -> None:
        with self._lock:
            if len(self._events) > max_events:
                self._events = self._events[-max_events:]
                self._save()


def _event_ts(event: HistoryEvent) -> datetime:
    ts = event.timestamp
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts
