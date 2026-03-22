"""
tests/test_schema.py — Card construction, enum values, CardDefinition builder.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from contextpack.card import (
    Card,
    CardDefinition,
    CardType,
    Contact,
    DomainFact,
    EventType,
    HistoryBlock,
    HistoryEvent,
    IdentityBlock,
    InstructionsBlock,
    KnowledgeBlock,
    MockField,
    Operator,
    Priority,
    ProvenanceBlock,
    Quality,
    Reading,
    Relationship,
    RelationshipType,
    Severity,
    StateBlock,
    Threshold,
    Trend,
)
from contextpack.validator import CardValidationError, Validator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _make_minimal_card() -> Card:
    now = _now()
    return Card(
        card_id="test-card-001",
        card_type=CardType.system,
        generated_at=now,
        valid_until=now + timedelta(hours=1),
        authored_by="test",
        identity=IdentityBlock(
            subject="Test System",
            entity_type="system",
            location="Lab 1",
        ),
        state=StateBlock(data_age_seconds=0),
        knowledge=KnowledgeBlock(),
        history=HistoryBlock(token_budget_pct=0.25, events=[]),
        instructions=InstructionsBlock(
            role_directive="You are a test assistant.",
            data_freshness_directive="If data_age_seconds exceeds 300, warn.",
        ),
    )


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

class TestEnums:
    def test_card_type_values(self):
        assert CardType.device == "device"
        assert CardType.system == "system"
        assert CardType.zone == "zone"
        assert CardType.process == "process"
        assert CardType.asset == "asset"
        assert CardType.agent == "agent"

    def test_priority_values(self):
        assert Priority.info == "info"
        assert Priority.warning == "warning"
        assert Priority.critical == "critical"

    def test_quality_values(self):
        assert Quality.measured == "measured"
        assert Quality.estimated == "estimated"
        assert Quality.stale == "stale"
        assert Quality.simulated == "simulated"

    def test_trend_values(self):
        assert Trend.rising == "rising"
        assert Trend.falling == "falling"
        assert Trend.stable == "stable"
        assert Trend.unknown == "unknown"

    def test_operator_values(self):
        assert Operator.gt == "gt"
        assert Operator.lt == "lt"
        assert Operator.gte == "gte"
        assert Operator.lte == "lte"
        assert Operator.eq == "eq"
        assert Operator.neq == "neq"
        assert Operator.between == "between"

    def test_event_type_values(self):
        assert EventType.fault == "fault"
        assert EventType.recovery == "recovery"
        assert EventType.maintenance == "maintenance"
        assert EventType.threshold_breach == "threshold_breach"

    def test_severity_values(self):
        assert Severity.info == "info"
        assert Severity.warning == "warning"
        assert Severity.critical == "critical"

    def test_relationship_type_values(self):
        assert RelationshipType.depends_on == "depends_on"
        assert RelationshipType.monitors == "monitors"
        assert RelationshipType.controls == "controls"
        assert RelationshipType.feeds == "feeds"


# ---------------------------------------------------------------------------
# Reading tests
# ---------------------------------------------------------------------------

class TestReading:
    def test_reading_construction(self):
        r = Reading(value=8.7, unit="celsius", quality=Quality.measured, timestamp=_now())
        assert r.value == 8.7
        assert r.unit == "celsius"
        assert r.quality == "measured"

    def test_reading_string_value(self):
        r = Reading(value="OFF", quality=Quality.measured, timestamp=_now())
        assert r.value == "OFF"
        assert r.unit is None

    def test_reading_bool_value(self):
        r = Reading(value=True, quality=Quality.simulated, timestamp=_now())
        assert r.value is True

    def test_reading_with_trend(self):
        r = Reading(
            value=7.5,
            quality=Quality.measured,
            timestamp=_now(),
            trend=Trend.rising,
            rate_of_change=1.2,
        )
        assert r.trend == "rising"
        assert r.rate_of_change == pytest.approx(1.2)


# ---------------------------------------------------------------------------
# Card construction
# ---------------------------------------------------------------------------

class TestCardConstruction:
    def test_minimal_card(self):
        card = _make_minimal_card()
        assert card.card_id == "test-card-001"
        assert card.card_type == "system"
        assert card.contextpack_version == "1.0"
        assert card.schema_version == "1.0"

    def test_card_with_readings(self):
        now = _now()
        card = Card(
            card_id="device-001",
            card_type=CardType.device,
            generated_at=now,
            valid_until=now + timedelta(hours=1),
            authored_by="test",
            identity=IdentityBlock(subject="Fridge", entity_type="device", location="Kitchen"),
            state=StateBlock(
                data_age_seconds=47,
                readings={
                    "temperature_c": Reading(value=8.7, unit="celsius", quality=Quality.measured, timestamp=now),
                    "door_status": Reading(value="CLOSED", quality=Quality.measured, timestamp=now),
                },
            ),
            knowledge=KnowledgeBlock(),
            history=HistoryBlock(token_budget_pct=0.25, events=[]),
            instructions=InstructionsBlock(role_directive="You are a fridge assistant."),
        )
        assert len(card.state.readings) == 2
        assert card.state.readings["temperature_c"].value == 8.7
        assert card.state.data_age_seconds == 47

    def test_card_serialization(self):
        card = _make_minimal_card()
        data = json.loads(card.model_dump_json())
        assert data["card_id"] == "test-card-001"
        assert data["card_type"] == "system"
        assert "identity" in data
        assert "state" in data

    def test_card_with_history_events(self):
        now = _now()
        event = HistoryEvent(
            event_id="evt-001",
            event_type=EventType.fault,
            timestamp=now,
            severity=Severity.critical,
            description="Temperature exceeded threshold",
            resolved=True,
            resolution_note="Compressor restarted",
        )
        card = _make_minimal_card()
        card = card.model_copy(update={"history": HistoryBlock(token_budget_pct=0.25, events=[event])})
        assert len(card.history.events) == 1
        assert card.history.events[0].event_id == "evt-001"


# ---------------------------------------------------------------------------
# CardDefinition builder
# ---------------------------------------------------------------------------

class TestCardDefinition:
    def test_basic_construction(self):
        defn = CardDefinition(
            card_id="cold-chain-001",
            card_type=CardType.system,
            subject="Cold Chain Monitor",
            location="Pharmacy Fridge A",
        )
        assert defn.card_id == "cold-chain-001"
        assert defn.token_budget == 800
        assert defn.write_interval_seconds == 120
        assert defn.staleness_threshold_seconds == 300

    def test_add_threshold(self):
        defn = CardDefinition(
            card_id="test", card_type=CardType.device, subject="Test", location="Lab"
        )
        defn.add_threshold("temperature_c", "gt", 8.0, "critical", "Temperature critical")
        assert len(defn._thresholds) == 1
        t = list(defn._thresholds.values())[0]
        assert t.field == "temperature_c"
        assert t.operator == "gt"
        assert t.value == 8.0
        assert t.severity == "critical"

    def test_add_knowledge_fact(self):
        defn = CardDefinition(
            card_id="test", card_type=CardType.device, subject="Test", location="Lab"
        )
        defn.add_knowledge_fact("mrna_stability", "Requires 2-8C", tags=["cold-chain"])
        assert len(defn._domain_facts) == 1
        assert defn._domain_facts[0].key == "mrna_stability"

    def test_add_contact(self):
        defn = CardDefinition(
            card_id="test", card_type=CardType.device, subject="Test", location="Lab"
        )
        defn.add_contact(Contact(name="Alice", role="engineer", phone="+1-555-0100"))
        assert len(defn.contacts) == 1
        assert defn.contacts[0].name == "Alice"

    def test_build_identity_block(self):
        defn = CardDefinition(
            card_id="test",
            card_type=CardType.system,
            subject="Test System",
            location="Building A",
            entity_type="hvac_system",
        )
        identity = defn.build_identity_block()
        assert identity.subject == "Test System"
        assert identity.location == "Building A"
        assert identity.entity_type == "hvac_system"

    def test_build_knowledge_block(self):
        defn = CardDefinition(
            card_id="test", card_type=CardType.device, subject="Test", location="Lab"
        )
        defn.add_threshold("temperature_c", "gt", 8.0, "critical", "Temp breach")
        defn.add_knowledge_fact("key1", "value1")
        kb = defn.build_knowledge_block()
        assert len(kb.thresholds) == 1
        assert len(kb.domain_facts) == 1

    def test_add_dependency(self):
        defn = CardDefinition(
            card_id="test", card_type=CardType.device, subject="Test", location="Lab"
        )
        defn.add_dependency("upstream-card-001")
        identity = defn.build_identity_block()
        assert "upstream-card-001" in identity.dependencies

    def test_multiple_thresholds(self):
        defn = CardDefinition(
            card_id="test", card_type=CardType.device, subject="Test", location="Lab"
        )
        defn.add_threshold("temp", "gt", 8.0, "critical", "Critical breach")
        defn.add_threshold("temp", "gt", 7.5, "warning", "Warning breach")
        assert len(defn._thresholds) == 2


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestSchemaValidation:
    def test_valid_card_passes(self):
        card = _make_minimal_card()
        validator = Validator()
        # Should not raise
        validator.validate(card)

    def test_schema_version_compatible(self):
        validator = Validator()
        assert validator.validate_schema_version("1.0") is True
        assert validator.validate_schema_version("2.0") is False

    def test_card_with_readings_validates(self):
        now = _now()
        card = Card(
            card_id="device-001",
            card_type=CardType.device,
            generated_at=now,
            valid_until=now + timedelta(hours=1),
            authored_by="sdk",
            identity=IdentityBlock(subject="Device", entity_type="device", location="Room 1"),
            state=StateBlock(
                data_age_seconds=10,
                readings={
                    "temp": Reading(value=22.5, unit="celsius", quality=Quality.measured, timestamp=now),
                },
            ),
            knowledge=KnowledgeBlock(),
            history=HistoryBlock(token_budget_pct=0.25, events=[]),
            instructions=InstructionsBlock(
                role_directive="Assistant role.",
                data_freshness_directive="Check freshness.",
            ),
        )
        validator = Validator()
        validator.validate(card)

    def test_provenance_block_optional(self):
        card = _make_minimal_card()
        assert card.provenance is None
        validator = Validator()
        validator.validate(card)
