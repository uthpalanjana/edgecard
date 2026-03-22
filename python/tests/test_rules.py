"""
tests/test_rules.py — Condition parsing, directive rendering, RuleEngine.evaluate(), determinism.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from contextpack.card import (
    CardDefinition,
    CardType,
    Contact,
    IdentityBlock,
    Priority,
    Quality,
    Reading,
    StateBlock,
)
from contextpack.rules import (
    ConditionParser,
    DirectiveRenderer,
    InvalidConditionError,
    MandatoryDirectiveGenerator,
    Rule,
    RuleEngine,
)


def _now():
    return datetime.now(timezone.utc)


def _make_state(readings: dict) -> StateBlock:
    return StateBlock(
        data_age_seconds=0,
        readings={
            k: Reading(value=v, quality=Quality.measured, timestamp=_now())
            for k, v in readings.items()
        },
    )


def _make_defn(card_type=CardType.system) -> CardDefinition:
    return CardDefinition(
        card_id="test-card",
        card_type=card_type,
        subject="Test Subject",
        location="Test Location",
    )


# ---------------------------------------------------------------------------
# ConditionParser
# ---------------------------------------------------------------------------

class TestConditionParser:
    def setup_method(self):
        self.parser = ConditionParser()

    def test_simple_gt(self):
        state = _make_state({"temperature_c": 9.0})
        assert self.parser.evaluate("temperature_c > 8.0", state) is True

    def test_simple_gt_false(self):
        state = _make_state({"temperature_c": 7.0})
        assert self.parser.evaluate("temperature_c > 8.0", state) is False

    def test_lt(self):
        state = _make_state({"pressure": 5.0})
        assert self.parser.evaluate("pressure < 10.0", state) is True

    def test_gte(self):
        state = _make_state({"temp": 8.0})
        assert self.parser.evaluate("temp >= 8.0", state) is True

    def test_lte(self):
        state = _make_state({"temp": 7.9})
        assert self.parser.evaluate("temp <= 8.0", state) is True

    def test_eq_numeric(self):
        state = _make_state({"count": 5})
        assert self.parser.evaluate("count == 5", state) is True

    def test_neq(self):
        state = _make_state({"count": 3})
        assert self.parser.evaluate("count != 5", state) is True

    def test_eq_string_identifier(self):
        state = _make_state({"compressor_status": "OFF"})
        assert self.parser.evaluate("compressor_status == OFF", state) is True

    def test_and_both_true(self):
        state = _make_state({"temperature_c": 8.5, "door_status": "OPEN"})
        assert self.parser.evaluate("temperature_c > 8.0 and door_status == OPEN", state) is True

    def test_and_one_false(self):
        state = _make_state({"temperature_c": 7.0, "door_status": "OPEN"})
        assert self.parser.evaluate("temperature_c > 8.0 and door_status == OPEN", state) is False

    def test_or_first_true(self):
        state = _make_state({"temperature_c": 9.0, "battery": 100})
        assert self.parser.evaluate("temperature_c > 8.0 or battery < 10", state) is True

    def test_or_second_true(self):
        state = _make_state({"temperature_c": 7.0, "battery": 5})
        assert self.parser.evaluate("temperature_c > 8.0 or battery < 10", state) is True

    def test_not(self):
        state = _make_state({"flag": 0})
        assert self.parser.evaluate("not flag > 5", state) is True

    def test_parse_returns_tokens(self):
        tokens = self.parser.parse("temperature_c > 8.0")
        assert len(tokens) == 3
        assert tokens[0]["kind"] == "ident"
        assert tokens[1]["kind"] == "op"
        assert tokens[2]["kind"] == "float"

    def test_empty_condition_raises(self):
        with pytest.raises(InvalidConditionError):
            self.parser.parse("")

    def test_invalid_condition_raises(self):
        with pytest.raises(InvalidConditionError):
            self.parser.parse("temperature_c @ 8.0")

    def test_translate(self):
        result = self.parser.translate("temperature_c > 8.0")
        assert "temperature_c" in result
        assert ">" in result
        assert "8.0" in result

    def test_field_not_in_state_returns_false(self):
        state = _make_state({"other_field": 5.0})
        # field not present, will return the string name == 8.0 which is False
        result = self.parser.evaluate("missing_field > 8.0", state)
        assert result is False

    def test_derived_state(self):
        state = StateBlock(
            data_age_seconds=0,
            readings={},
            derived_state={"breach_active": True},
        )
        assert self.parser.evaluate("breach_active == True", state) is True


# ---------------------------------------------------------------------------
# DirectiveRenderer
# ---------------------------------------------------------------------------

class TestDirectiveRenderer:
    def setup_method(self):
        self.renderer = DirectiveRenderer()

    def test_critical_priority(self):
        result = self.renderer.render("Check temperature", Priority.critical)
        assert "CRITICAL" in result
        assert "MUST NOT" in result.upper() or "must not" in result.lower()

    def test_warning_priority(self):
        result = self.renderer.render("Monitor pressure", Priority.warning)
        assert "WARNING" in result

    def test_info_priority(self):
        result = self.renderer.render("Log event", Priority.info)
        assert "INFO" in result

    def test_render_numbered(self):
        directives = [
            ("Check temperature", Priority.critical),
            ("Monitor level", Priority.warning),
        ]
        result = self.renderer.render_numbered(directives)
        assert len(result) == 2
        assert result[0].startswith("1.")
        assert result[1].startswith("2.")


# ---------------------------------------------------------------------------
# MandatoryDirectiveGenerator
# ---------------------------------------------------------------------------

class TestMandatoryDirectiveGenerator:
    def setup_method(self):
        self.gen = MandatoryDirectiveGenerator()
        self.defn = _make_defn()

    def test_role_directive(self):
        result = self.gen.generate_role(self.defn)
        assert "Test Subject" in result
        assert "assistant" in result.lower()

    def test_freshness_directive(self):
        result = self.gen.generate_freshness(self.defn)
        assert "300" in result
        assert "stale" in result.lower()

    def test_hallucination_guard(self):
        result = self.gen.generate_hallucination_guard()
        assert "invent" in result.lower() or "do not" in result.lower()

    def test_output_terminus(self):
        result = self.gen.generate_output_terminus()
        assert "RESOLVED" in result or "response" in result.lower()


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------

class TestRule:
    def test_rule_construction(self):
        rule = Rule(
            rule_id="rule-001",
            name="Temperature critical",
            condition="temperature_c > 8.0",
            directive="Alert on temperature breach",
            priority=Priority.critical,
        )
        assert rule.rule_id == "rule-001"
        assert rule.enabled is True

    def test_rule_validate_ok(self):
        rule = Rule(
            rule_id="rule-001",
            name="Test",
            condition="temperature_c > 8.0",
            directive="Alert",
            priority=Priority.warning,
        )
        rule.validate()  # should not raise

    def test_rule_validate_empty_condition_raises(self):
        rule = Rule(
            rule_id="rule-001",
            name="Test",
            condition="",
            directive="Alert",
            priority=Priority.warning,
        )
        with pytest.raises(ValueError):
            rule.validate()

    def test_rule_applies_to_all_types(self):
        rule = Rule(
            rule_id="r1",
            name="Test",
            condition="x > 1",
            directive="d",
            priority=Priority.info,
        )
        assert rule.applies_to(CardType.system) is True
        assert rule.applies_to(CardType.device) is True

    def test_rule_applies_to_specific_types(self):
        rule = Rule(
            rule_id="r1",
            name="Test",
            condition="x > 1",
            directive="d",
            priority=Priority.info,
            applies_to_card_types=[CardType.device],
        )
        assert rule.applies_to(CardType.device) is True
        assert rule.applies_to(CardType.system) is False


# ---------------------------------------------------------------------------
# RuleEngine.evaluate()
# ---------------------------------------------------------------------------

class TestRuleEngine:
    def setup_method(self):
        self.engine = RuleEngine()

    def test_no_rules_returns_mandatory_directives(self):
        state = _make_state({"temperature_c": 5.0})
        defn = _make_defn()
        instructions = self.engine.evaluate([], state, defn)
        assert instructions.role_directive != ""
        assert instructions.data_freshness_directive != ""
        # hallucination guard should be present
        assert any("invent" in d.lower() for d in instructions.reasoning_directives)

    def test_triggered_rule_appears_in_directives(self):
        state = _make_state({"temperature_c": 9.0})
        defn = _make_defn()
        rule = Rule(
            rule_id="rule-temp",
            name="Temperature alert",
            condition="temperature_c > 8.0",
            directive="Immediate temperature alert required",
            priority=Priority.critical,
        )
        instructions = self.engine.evaluate([rule], state, defn)
        assert "rule-temp" in instructions.generated_from_rules
        assert any("Immediate temperature alert" in d for d in instructions.reasoning_directives)

    def test_untriggered_rule_not_in_directives(self):
        state = _make_state({"temperature_c": 5.0})
        defn = _make_defn()
        rule = Rule(
            rule_id="rule-temp",
            name="Temperature alert",
            condition="temperature_c > 8.0",
            directive="Alert",
            priority=Priority.critical,
        )
        instructions = self.engine.evaluate([rule], state, defn)
        assert "rule-temp" not in instructions.generated_from_rules

    def test_disabled_rule_not_evaluated(self):
        state = _make_state({"temperature_c": 9.0})
        defn = _make_defn()
        rule = Rule(
            rule_id="rule-disabled",
            name="Disabled rule",
            condition="temperature_c > 8.0",
            directive="Should not appear",
            priority=Priority.critical,
            enabled=False,
        )
        instructions = self.engine.evaluate([rule], state, defn)
        assert "rule-disabled" not in instructions.generated_from_rules

    def test_contacts_appear_in_escalation(self):
        state = _make_state({})
        defn = _make_defn()
        defn.add_contact(Contact(name="Alice", role="engineer", phone="+1-555-0100"))
        instructions = self.engine.evaluate([], state, defn)
        assert any("Alice" in c for c in instructions.escalation_contacts)

    def test_determinism(self):
        """Evaluating the same rules twice produces identical results."""
        state = _make_state({"temperature_c": 9.0, "humidity": 85.0})
        defn = _make_defn()
        rules = [
            Rule(
                rule_id="r1",
                name="Temp",
                condition="temperature_c > 8.0",
                directive="Temp alert",
                priority=Priority.critical,
            ),
            Rule(
                rule_id="r2",
                name="Humidity",
                condition="humidity > 80.0",
                directive="Humidity alert",
                priority=Priority.warning,
            ),
        ]
        result1 = self.engine.evaluate(rules, state, defn)
        result2 = self.engine.evaluate(rules, state, defn)
        assert result1.generated_from_rules == result2.generated_from_rules
        assert result1.reasoning_directives == result2.reasoning_directives
        assert result1.role_directive == result2.role_directive

    def test_multiple_rules_ordered(self):
        state = _make_state({"temperature_c": 9.0, "pressure": 5.0})
        defn = _make_defn()
        rules = [
            Rule(rule_id="r1", name="Temp", condition="temperature_c > 8.0", directive="Temp alert", priority=Priority.critical),
            Rule(rule_id="r2", name="Pressure", condition="pressure > 3.0", directive="Pressure alert", priority=Priority.warning),
        ]
        instructions = self.engine.evaluate(rules, state, defn)
        assert "r1" in instructions.generated_from_rules
        assert "r2" in instructions.generated_from_rules
        assert instructions.generated_from_rules.index("r1") < instructions.generated_from_rules.index("r2")
