"""
tests/test_encoders.py — TOON per-block, round-trip, token counts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest
import yaml

from contextpack.card import (
    Card,
    CardType,
    DomainFact,
    EventType,
    HistoryBlock,
    HistoryEvent,
    IdentityBlock,
    InstructionsBlock,
    KnowledgeBlock,
    Operator,
    Quality,
    Reading,
    Relationship,
    RelationshipType,
    Severity,
    StateBlock,
    Threshold,
    Trend,
)
from contextpack.encoders import Encoding, get_encoder
from contextpack.encoders.toon_encoder import ToonEncoder
from contextpack.encoders.yaml_encoder import YamlEncoder
from contextpack.encoders.json_encoder import JsonEncoder
from contextpack.encoders.text_encoder import TextEncoder


def _now():
    return datetime.now(timezone.utc)


def _make_full_card() -> Card:
    now = _now()
    return Card(
        card_id="cold-chain-001",
        card_type=CardType.system,
        generated_at=now,
        valid_until=now + timedelta(hours=4),
        authored_by="contextpack-sdk",
        identity=IdentityBlock(
            subject="Pharmacy Cold Chain Monitor",
            entity_type="system",
            location="Pharmacy Fridge A",
            tags=["cold-chain", "vaccines"],
        ),
        state=StateBlock(
            data_age_seconds=47,
            readings={
                "temperature_c": Reading(
                    value=8.7,
                    unit="celsius",
                    quality=Quality.measured,
                    timestamp=now,
                    trend=Trend.rising,
                    rate_of_change=1.2,
                ),
                "compressor_status": Reading(
                    value="OFF",
                    quality=Quality.measured,
                    timestamp=now,
                ),
                "door_status": Reading(
                    value="CLOSED",
                    quality=Quality.measured,
                    timestamp=now,
                ),
            },
            derived_state={
                "breach_active": True,
                "breach_started": "2026-03-21T02:08:00Z",
            },
        ),
        knowledge=KnowledgeBlock(
            thresholds={
                "temp_critical": Threshold(
                    field="temperature_c",
                    operator=Operator.gt,
                    value=8.0,
                    severity=Severity.critical,
                    label="Temperature critical breach",
                ),
                "temp_warning": Threshold(
                    field="temperature_c",
                    operator=Operator.gt,
                    value=7.5,
                    severity=Severity.warning,
                    label="Temperature warning",
                ),
            },
            domain_facts=[
                DomainFact(
                    key="mrna_stability",
                    value="COVID-19 mRNA vaccines require 2-8C. Zero tolerance above 8C.",
                ),
                DomainFact(
                    key="hepb_stability",
                    value="Hepatitis B (Engerix-B) tolerates up to 12C for 72 hours.",
                ),
            ],
        ),
        history=HistoryBlock(
            token_budget_pct=0.25,
            events=[
                HistoryEvent(
                    event_id="evt-002",
                    event_type=EventType.fault,
                    timestamp=datetime(2026, 2, 14, 11, 20, 0, tzinfo=timezone.utc),
                    severity=Severity.critical,
                    description="Compressor failure detected",
                    resolved=True,
                    resolution_note="Compressor restart resolved fault",
                ),
                HistoryEvent(
                    event_id="evt-001",
                    event_type=EventType.fault,
                    timestamp=datetime(2026, 1, 30, 3, 10, 0, tzinfo=timezone.utc),
                    severity=Severity.warning,
                    description="Temperature deviation",
                    resolved=True,
                    resolution_note="Door seal replaced",
                ),
            ],
        ),
        instructions=InstructionsBlock(
            generated_from_rules=["rule-temp-critical"],
            token_budget_pct=0.20,
            role_directive="You are a cold chain incident assistant. Reason only from the data in this card.",
            data_freshness_directive="If data_age_seconds exceeds 300, preface response with WARNING - readings may be stale.",
            reasoning_directives=[
                "Do not invent readings, thresholds, or contact details not present in this card.",
                "1. [CRITICAL] Immediate escalation required. — This MUST NOT be omitted.",
            ],
            output_format=[
                "End every response with one of: [RESOLVED], [ESCALATED], [MONITORING].",
            ],
            escalation_contacts=[
                "Alice Smith (pharmacist) +1-555-0101",
                "Bob Jones (engineer) +1-555-0102",
            ],
        ),
    )


# ---------------------------------------------------------------------------
# TOON Encoder
# ---------------------------------------------------------------------------

class TestToonEncoder:
    def setup_method(self):
        self.encoder = ToonEncoder()
        self.card = _make_full_card()

    def test_header_line(self):
        output = self.encoder.encode(self.card)
        assert output.startswith("## CARD: cold-chain-001")

    def test_card_type_line(self):
        output = self.encoder.encode(self.card)
        assert "card_type: system" in output

    def test_subject_line(self):
        output = self.encoder.encode(self.card)
        assert "subject: Pharmacy Cold Chain Monitor" in output

    def test_location_line(self):
        output = self.encoder.encode(self.card)
        assert "location: Pharmacy Fridge A" in output

    def test_data_age_seconds_line(self):
        output = self.encoder.encode(self.card)
        assert "data_age_seconds: 47" in output

    def test_readings_header(self):
        output = self.encoder.encode(self.card)
        assert "readings[3]{field,value,unit,quality,trend,rate_of_change_per_hour}:" in output

    def test_reading_row_with_all_fields(self):
        output = self.encoder.encode(self.card)
        lines = output.splitlines()
        # Find the readings section, then locate temperature_c within it
        # The reading row format is: field,value,unit,quality,trend,roc
        # The threshold row format is: field,op,value,severity,label (no quality column)
        # We find lines that match the 6-column reading pattern
        reading_section_lines = []
        in_readings = False
        for line in lines:
            if line.startswith("readings["):
                in_readings = True
                continue
            if in_readings and line == "":
                break
            if in_readings:
                reading_section_lines.append(line)

        temp_lines = [l for l in reading_section_lines if l.startswith("temperature_c,")]
        assert len(temp_lines) == 1
        parts = temp_lines[0].split(",")
        assert parts[0] == "temperature_c"
        assert parts[1] == "8.7"
        assert parts[2] == "celsius"
        assert parts[3] == "measured"
        assert parts[4] == "rising"
        # rate_of_change should be present
        assert parts[5] != ""

    def test_reading_row_empty_fields(self):
        output = self.encoder.encode(self.card)
        lines = output.splitlines()
        # compressor_status has no unit, no trend, no roc
        status_lines = [l for l in lines if l.startswith("compressor_status,")]
        assert len(status_lines) == 1
        parts = status_lines[0].split(",")
        assert parts[0] == "compressor_status"
        assert parts[1] == "OFF"
        assert parts[2] == ""  # empty unit

    def test_derived_block(self):
        output = self.encoder.encode(self.card)
        assert "derived:" in output
        assert "breach_active: True" in output or "breach_active: true" in output

    def test_thresholds_header(self):
        output = self.encoder.encode(self.card)
        assert "thresholds[2]{field,operator,value,severity,label}:" in output

    def test_threshold_row(self):
        output = self.encoder.encode(self.card)
        lines = output.splitlines()
        # Find temp_critical threshold row
        thresh_lines = [l for l in lines if "temperature_c,gt,8.0,critical" in l]
        assert len(thresh_lines) >= 1

    def test_facts_header(self):
        output = self.encoder.encode(self.card)
        assert "facts[2]{key,value}:" in output

    def test_fact_row(self):
        output = self.encoder.encode(self.card)
        assert "mrna_stability," in output

    def test_history_header(self):
        output = self.encoder.encode(self.card)
        assert "history[2]{event_id,event_type,timestamp,severity,resolved,resolution}:" in output

    def test_history_row(self):
        output = self.encoder.encode(self.card)
        lines = output.splitlines()
        evt_lines = [l for l in lines if l.startswith("evt-002,")]
        assert len(evt_lines) == 1
        parts = evt_lines[0].split(",")
        assert parts[0] == "evt-002"
        assert parts[1] == "fault"
        assert parts[3] == "critical"
        assert parts[4] == "true"

    def test_instructions_separator(self):
        output = self.encoder.encode(self.card)
        assert "## INSTRUCTIONS" in output

    def test_role_directive(self):
        output = self.encoder.encode(self.card)
        assert "Role: You are a cold chain incident assistant." in output

    def test_freshness_directive(self):
        output = self.encoder.encode(self.card)
        assert "Freshness:" in output
        assert "300" in output

    def test_output_format(self):
        output = self.encoder.encode(self.card)
        assert "Output format:" in output
        assert "- End every response" in output

    def test_escalation_contacts(self):
        output = self.encoder.encode(self.card)
        assert "Escalation contacts:" in output
        assert "- Alice Smith" in output

    def test_comma_in_value_quoted(self):
        from contextpack.encoders.toon_encoder import _toon_escape
        assert _toon_escape("value,with,commas") == '"value,with,commas"'

    def test_no_comma_value_unquoted(self):
        from contextpack.encoders.toon_encoder import _toon_escape
        assert _toon_escape("simple") == "simple"

    def test_empty_value(self):
        from contextpack.encoders.toon_encoder import _toon_escape
        assert _toon_escape(None) == ""
        assert _toon_escape("") == ""

    def test_instructions_separator_after_structured_blocks(self):
        """## INSTRUCTIONS must appear after all structured data blocks."""
        output = self.encoder.encode(self.card)
        history_pos = output.find("history[")
        instructions_pos = output.find("## INSTRUCTIONS")
        assert history_pos < instructions_pos

    def test_multi_card_separation(self):
        """Multi-card encoding uses --- separator."""
        card2 = self.card.model_copy(update={"card_id": "card-002"})
        combined = "\n---\n".join([self.encoder.encode(self.card), self.encoder.encode(card2)])
        assert "## CARD: cold-chain-001" in combined
        assert "## CARD: card-002" in combined
        assert "---" in combined


# ---------------------------------------------------------------------------
# YAML Encoder
# ---------------------------------------------------------------------------

class TestYamlEncoder:
    def test_encodes_card_to_yaml(self):
        card = _make_full_card()
        encoder = YamlEncoder()
        output = encoder.encode(card)
        data = yaml.safe_load(output)
        assert data["card_id"] == "cold-chain-001"
        assert data["card_type"] == "system"
        assert "identity" in data

    def test_roundtrip(self):
        card = _make_full_card()
        encoder = YamlEncoder()
        output = encoder.encode(card)
        data = yaml.safe_load(output)
        card2 = Card(**data)
        assert card2.card_id == card.card_id
        assert len(card2.state.readings) == len(card.state.readings)


# ---------------------------------------------------------------------------
# JSON Encoder
# ---------------------------------------------------------------------------

class TestJsonEncoder:
    def test_encodes_card_to_json(self):
        card = _make_full_card()
        encoder = JsonEncoder()
        output = encoder.encode(card)
        data = json.loads(output)
        assert data["card_id"] == "cold-chain-001"
        assert data["card_type"] == "system"

    def test_roundtrip(self):
        card = _make_full_card()
        encoder = JsonEncoder()
        output = encoder.encode(card)
        data = json.loads(output)
        card2 = Card(**data)
        assert card2.card_id == card.card_id


# ---------------------------------------------------------------------------
# Text Encoder
# ---------------------------------------------------------------------------

class TestTextEncoder:
    def test_encodes_card(self):
        card = _make_full_card()
        encoder = TextEncoder()
        output = encoder.encode(card)
        assert "Pharmacy Cold Chain Monitor" in output
        assert "Pharmacy Fridge A" in output
        assert "CURRENT STATE:" in output

    def test_contains_readings(self):
        card = _make_full_card()
        encoder = TextEncoder()
        output = encoder.encode(card)
        assert "temperature_c" in output
        assert "8.7" in output

    def test_contains_instructions(self):
        card = _make_full_card()
        encoder = TextEncoder()
        output = encoder.encode(card)
        assert "INSTRUCTIONS:" in output
        assert "cold chain incident assistant" in output


# ---------------------------------------------------------------------------
# Encoding enum and get_encoder
# ---------------------------------------------------------------------------

class TestEncodingFactory:
    def test_get_encoder_yaml(self):
        enc = get_encoder("yaml")
        assert isinstance(enc, YamlEncoder)

    def test_get_encoder_json(self):
        enc = get_encoder("json")
        assert isinstance(enc, JsonEncoder)

    def test_get_encoder_toon(self):
        enc = get_encoder("toon")
        assert isinstance(enc, ToonEncoder)

    def test_get_encoder_text(self):
        enc = get_encoder("text")
        assert isinstance(enc, TextEncoder)

    def test_get_encoder_enum(self):
        enc = get_encoder(Encoding.toon)
        assert isinstance(enc, ToonEncoder)

    def test_unknown_encoding_raises(self):
        with pytest.raises(ValueError):
            get_encoder("xml")


# ---------------------------------------------------------------------------
# Token count checks
# ---------------------------------------------------------------------------

class TestTokenCounts:
    def test_toon_smaller_than_yaml(self):
        """TOON encoding should be more token-efficient than YAML."""
        card = _make_full_card()
        toon_output = ToonEncoder().encode(card)
        yaml_output = YamlEncoder().encode(card)
        # TOON should be shorter (more compact format)
        assert len(toon_output) < len(yaml_output)

    def test_budget_enforcer_measures_nonzero(self):
        from contextpack.writer import BudgetEnforcer
        enforcer = BudgetEnforcer()
        card = _make_full_card()
        yaml_output = YamlEncoder().encode(card)
        tokens = enforcer.measure_tokens(yaml_output)
        # Either tiktoken or character-based fallback — result should be > 0
        assert tokens > 0
