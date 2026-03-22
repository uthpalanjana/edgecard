"""
tests/test_writer.py — Write cycle with MockAdapter, derived state, budget enforcement.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from contextpack.card import (
    Card,
    CardDefinition,
    CardType,
    Contact,
    EventType,
    HistoryEvent,
    MockField,
    Priority,
    Quality,
    Reading,
    Severity,
    StateBlock,
    Trend,
)
from contextpack.adapters.mock import MockAdapter
from contextpack.rules import Rule
from contextpack.writer import BudgetEnforcer, CardWriter, DerivedStateComputer
from contextpack.history import HistoryStore


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# DerivedStateComputer
# ---------------------------------------------------------------------------

class TestDerivedStateComputer:
    def setup_method(self):
        self.dsc = DerivedStateComputer()

    def test_trend_rising(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert self.dsc.compute_trend(values) == Trend.rising

    def test_trend_falling(self):
        values = [5.0, 4.0, 3.0, 2.0, 1.0]
        assert self.dsc.compute_trend(values) == Trend.falling

    def test_trend_stable(self):
        values = [5.0, 5.0, 5.0, 5.0, 5.0]
        assert self.dsc.compute_trend(values) == Trend.stable

    def test_trend_unknown_insufficient_data(self):
        assert self.dsc.compute_trend([]) == Trend.unknown
        assert self.dsc.compute_trend([5.0]) == Trend.unknown

    def test_trend_uses_last_5(self):
        # First values falling, last 5 rising
        values = [10.0, 9.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        assert self.dsc.compute_trend(values) == Trend.rising

    def test_rate_of_change(self):
        base = _now()
        timestamps = [
            base - timedelta(hours=1),
            base,
        ]
        values = [0.0, 5.0]
        roc = self.dsc.compute_rate_of_change(values, timestamps)
        assert roc == pytest.approx(5.0, rel=0.01)

    def test_rate_of_change_insufficient(self):
        assert self.dsc.compute_rate_of_change([], []) is None
        assert self.dsc.compute_rate_of_change([1.0], [_now()]) is None

    def test_breach_state_gt(self):
        from contextpack.card import Threshold, Severity, Operator
        readings = {
            "temperature_c": Reading(value=9.0, quality=Quality.measured, timestamp=_now()),
        }
        thresholds = {
            "temp_critical": Threshold(
                field="temperature_c",
                operator=Operator.gt,
                value=8.0,
                severity=Severity.critical,
                label="Temp breach",
            )
        }
        result = self.dsc.compute_breach_state(readings, thresholds)
        assert result.get("breach_temp_critical") is True
        assert result.get("breach_active") is True

    def test_breach_state_no_breach(self):
        from contextpack.card import Threshold, Severity, Operator
        readings = {
            "temperature_c": Reading(value=5.0, quality=Quality.measured, timestamp=_now()),
        }
        thresholds = {
            "temp_critical": Threshold(
                field="temperature_c",
                operator=Operator.gt,
                value=8.0,
                severity=Severity.critical,
                label="Temp breach",
            )
        }
        result = self.dsc.compute_breach_state(readings, thresholds)
        assert result.get("breach_temp_critical") is False
        assert result.get("breach_active") is False


# ---------------------------------------------------------------------------
# MockAdapter
# ---------------------------------------------------------------------------

class TestMockAdapter:
    def test_poll_returns_readings(self):
        adapter = MockAdapter(
            name="test",
            fields={
                "temperature_c": MockField(value=8.5, unit="celsius"),
                "door_status": MockField(value="CLOSED"),
            },
        )
        readings = adapter.poll()
        assert "temperature_c" in readings
        assert "door_status" in readings
        assert readings["temperature_c"].value == 8.5
        assert readings["temperature_c"].quality == "simulated"
        assert readings["temperature_c"].unit == "celsius"

    def test_poll_with_callable(self):
        counter = {"n": 0}

        def incrementing():
            counter["n"] += 1
            return float(counter["n"])

        adapter = MockAdapter(
            name="test",
            fields={"counter": MockField(value_fn=incrementing)},
        )
        r1 = adapter.poll()["counter"]
        r2 = adapter.poll()["counter"]
        assert r1.value == 1.0
        assert r2.value == 2.0

    def test_poll_with_noise(self):
        adapter = MockAdapter(
            name="test",
            fields={"temp": MockField(value=20.0, noise_sigma=0.5, seed=42)},
        )
        readings = [adapter.poll()["temp"].value for _ in range(20)]
        # Values should differ from base value due to noise
        assert any(abs(v - 20.0) > 0.01 for v in readings)

    def test_poll_reproducible_with_seed(self):
        adapter1 = MockAdapter(
            name="a", fields={"temp": MockField(value=20.0, noise_sigma=1.0, seed=99)}
        )
        adapter2 = MockAdapter(
            name="b", fields={"temp": MockField(value=20.0, noise_sigma=1.0, seed=99)}
        )
        r1 = [adapter1.poll()["temp"].value for _ in range(5)]
        r2 = [adapter2.poll()["temp"].value for _ in range(5)]
        assert r1 == r2

    def test_test_connection_always_succeeds(self):
        adapter = MockAdapter(name="test", fields={})
        result = adapter.test_connection()
        assert result.success is True


# ---------------------------------------------------------------------------
# BudgetEnforcer
# ---------------------------------------------------------------------------

class TestBudgetEnforcer:
    def setup_method(self):
        self.enforcer = BudgetEnforcer()

    def test_measure_tokens_nonempty(self):
        tokens = self.enforcer.measure_tokens("Hello world")
        assert tokens > 0

    def test_measure_tokens_empty(self):
        tokens = self.enforcer.measure_tokens("")
        assert tokens >= 0

    def test_enforce_under_budget_unchanged(self):
        from contextpack.card import HistoryBlock, KnowledgeBlock, InstructionsBlock, IdentityBlock
        now = _now()
        card = Card(
            card_id="test",
            card_type=CardType.device,
            generated_at=now,
            valid_until=now + timedelta(hours=1),
            authored_by="test",
            identity=IdentityBlock(subject="Test", entity_type="device", location="Lab"),
            state=StateBlock(data_age_seconds=0),
            knowledge=KnowledgeBlock(),
            history=HistoryBlock(token_budget_pct=0.25, events=[]),
            instructions=InstructionsBlock(role_directive="You are a test assistant."),
        )
        allocation = {"state": 0.30, "knowledge": 0.25, "history": 0.25, "instructions": 0.20}
        result = self.enforcer.enforce(card, 800, allocation)
        assert result.card_id == "test"

    def test_summarise_history_keeps_unresolved(self):
        from contextpack.card import HistoryBlock, HistoryEvent
        unresolved = HistoryEvent(
            event_id="unresolved-001",
            event_type=EventType.fault,
            timestamp=_now(),
            severity=Severity.critical,
            description="Active fault",
            resolved=False,
        )
        resolved = [
            HistoryEvent(
                event_id=f"evt-{i:03d}",
                event_type=EventType.fault,
                timestamp=_now(),
                severity=Severity.warning,
                description=f"Old fault {i}",
                resolved=True,
                resolution_note="Fixed",
            )
            for i in range(20)
        ]
        history = HistoryBlock(token_budget_pct=0.25, events=[unresolved] + resolved)
        result = self.enforcer._summarise_history(history, budget=100)
        # Unresolved event must be kept
        event_ids = [e.event_id for e in result.events]
        assert "unresolved-001" in event_ids


# ---------------------------------------------------------------------------
# CardWriter full write cycle
# ---------------------------------------------------------------------------

class TestCardWriter:
    def test_write_now_produces_card(self, tmp_path):
        defn = CardDefinition(
            card_id="test-fridge",
            card_type=CardType.system,
            subject="Test Fridge",
            location="Lab A",
            output_dir=tmp_path,
        )
        defn.add_threshold("temperature_c", "gt", 8.0, "critical", "Temp critical")
        defn.add_data_source(MockAdapter(
            name="mock",
            fields={
                "temperature_c": MockField(value=9.0, unit="celsius"),
                "door_status": MockField(value="CLOSED"),
            },
        ))

        writer = CardWriter(defn)
        card = writer.write_now()

        assert card.card_id == "test-fridge"
        assert "temperature_c" in card.state.readings
        assert card.state.readings["temperature_c"].value == pytest.approx(9.0)

    def test_write_creates_file(self, tmp_path):
        defn = CardDefinition(
            card_id="file-test",
            card_type=CardType.device,
            subject="File Test",
            location="Room 1",
            output_dir=tmp_path,
        )
        defn.add_data_source(MockAdapter(
            name="mock",
            fields={"temp": MockField(value=25.0)},
        ))
        writer = CardWriter(defn)
        writer.write_now()

        card_file = tmp_path / "file-test.card.yaml"
        assert card_file.exists()

    def test_write_with_rule_evaluation(self, tmp_path):
        defn = CardDefinition(
            card_id="rule-test",
            card_type=CardType.system,
            subject="Rule Test",
            location="Lab",
            output_dir=tmp_path,
        )
        rule = Rule(
            rule_id="temp-rule",
            name="Temperature alert",
            condition="temperature_c > 8.0",
            directive="Temperature is critically high!",
            priority=Priority.critical,
        )
        defn.add_rule(rule)
        defn.add_data_source(MockAdapter(
            name="mock",
            fields={"temperature_c": MockField(value=9.5, unit="celsius")},
        ))

        writer = CardWriter(defn)
        card = writer.write_now()

        assert "temp-rule" in card.instructions.generated_from_rules

    def test_get_last_card(self, tmp_path):
        defn = CardDefinition(
            card_id="last-card-test",
            card_type=CardType.device,
            subject="Test",
            location="Lab",
            output_dir=tmp_path,
        )
        defn.add_data_source(MockAdapter(name="mock", fields={"x": MockField(value=1.0)}))
        writer = CardWriter(defn)
        assert writer.get_last_card() is None
        writer.write_now()
        assert writer.get_last_card() is not None

    def test_derived_state_breach(self, tmp_path):
        defn = CardDefinition(
            card_id="breach-test",
            card_type=CardType.system,
            subject="Breach Test",
            location="Lab",
            output_dir=tmp_path,
        )
        defn.add_threshold("temperature_c", "gt", 8.0, "critical", "Critical breach")
        defn.add_data_source(MockAdapter(
            name="mock",
            fields={"temperature_c": MockField(value=9.5, unit="celsius")},
        ))

        writer = CardWriter(defn)
        card = writer.write_now()

        assert card.state.derived_state.get("breach_active") is True

    def test_trend_computed_after_multiple_polls(self, tmp_path):
        """After several writes with rising values, trend should be 'rising'."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        idx = {"i": 0}

        def incrementing():
            v = values[min(idx["i"], len(values) - 1)]
            idx["i"] += 1
            return v

        defn = CardDefinition(
            card_id="trend-test",
            card_type=CardType.device,
            subject="Trend Test",
            location="Lab",
            output_dir=tmp_path,
        )
        defn.add_data_source(MockAdapter(
            name="mock",
            fields={"temp": MockField(value_fn=incrementing)},
        ))

        writer = CardWriter(defn)
        # Write 5 times to fill ring buffer
        for _ in range(5):
            card = writer.write_now()

        assert card.state.readings["temp"].trend == "rising"
