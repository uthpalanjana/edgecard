"""
rules.py — Rule, RuleEngine, ConditionParser, DirectiveRenderer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from .card import (
    CardType,
    InstructionsBlock,
    Priority,
    StateBlock,
)

if TYPE_CHECKING:
    from .card import CardDefinition


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InvalidConditionError(ValueError):
    """Raised when a condition string cannot be parsed."""


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    rule_id: str
    name: str
    condition: str
    directive: str
    priority: Priority
    applies_to_card_types: list[CardType] = field(default_factory=list)
    enabled: bool = True

    def validate(self) -> None:
        """Validate rule fields at authoring time."""
        if not self.rule_id:
            raise ValueError("rule_id must not be empty")
        if not self.condition.strip():
            raise ValueError(f"Rule {self.rule_id}: condition must not be empty")
        # Attempt a parse to catch invalid conditions early
        parser = ConditionParser()
        parser.parse(self.condition)

    def applies_to(self, card_type: CardType) -> bool:
        if not self.applies_to_card_types:
            return True  # applies to all types
        return card_type in self.applies_to_card_types


# ---------------------------------------------------------------------------
# ConditionParser
# ---------------------------------------------------------------------------

# Tokenise a condition expression into a simple AST for evaluation
_TOKEN_RE = re.compile(
    r"""
    (?P<float>-?\d+\.\d+)          |
    (?P<int>-?\d+)                 |
    (?P<string>'[^']*'|"[^"]*")    |
    (?P<op>>=|<=|!=|==|>|<)        |
    (?P<and>\band\b)               |
    (?P<or>\bor\b)                 |
    (?P<not>\bnot\b)               |
    (?P<ident>[A-Za-z_][A-Za-z0-9_.]*) |
    (?P<ws>\s+)
    """,
    re.VERBOSE | re.IGNORECASE,
)

_OP_MAP = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


class ConditionParser:
    """
    Parses and evaluates simple condition strings.

    Supported syntax:
        field_name > 8.0
        field_name == OFF
        field_a > 7.5 and field_b == ON
        field_a > 7.5 or field_b < 5.0
        not field_a > 7.5
    """

    def parse(self, condition: str) -> list[dict]:
        """
        Returns a list of token dicts for the condition.
        Raises InvalidConditionError if the string cannot be tokenised.
        """
        tokens = []
        pos = 0
        text = condition.strip()
        while pos < len(text):
            m = _TOKEN_RE.match(text, pos)
            if not m:
                raise InvalidConditionError(
                    f"Cannot parse condition at position {pos}: '{text[pos:pos+20]}'"
                )
            pos = m.end()
            kind = m.lastgroup
            if kind == "ws":
                continue
            tokens.append({"kind": kind, "value": m.group()})

        if not tokens:
            raise InvalidConditionError("Empty condition string")
        return tokens

    def translate(self, condition: str) -> str:
        """Return a human-readable English translation of the condition."""
        tokens = self.parse(condition)
        parts = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t["kind"] in ("and",):
                parts.append("AND")
                i += 1
            elif t["kind"] in ("or",):
                parts.append("OR")
                i += 1
            elif t["kind"] in ("not",):
                parts.append("NOT")
                i += 1
            elif t["kind"] == "ident" and i + 2 < len(tokens) and tokens[i + 1]["kind"] == "op":
                field = t["value"]
                op = tokens[i + 1]["value"]
                val = tokens[i + 2]["value"]
                parts.append(f"{field} {op} {val}")
                i += 3
            else:
                parts.append(t["value"])
                i += 1
        return " ".join(parts)

    def evaluate(self, condition: str, state: StateBlock) -> bool:
        """
        Evaluate the condition against a StateBlock.
        Returns True if the condition is met.
        """
        tokens = self.parse(condition)
        result, _ = self._eval_expr(tokens, 0, state)
        return bool(result)

    def _resolve_value(self, token: dict, state: StateBlock) -> Any:
        kind = token["kind"]
        val = token["value"]
        if kind == "float":
            return float(val)
        if kind == "int":
            return int(val)
        if kind == "string":
            return val[1:-1]  # strip quotes
        if kind == "ident":
            # Handle Python boolean literals
            if val == "True":
                return True
            if val == "False":
                return False
            # Try readings first, then derived_state
            if val in state.readings:
                return state.readings[val].value
            if val in state.derived_state:
                return state.derived_state[val]
            # Return the raw string (for e.g. enum comparisons like == OFF)
            return val
        raise InvalidConditionError(f"Cannot resolve token: {token}")

    def _eval_simple(self, tokens: list, pos: int, state: StateBlock):
        """Evaluate a single comparison or identifier, returns (value, new_pos)."""
        if pos >= len(tokens):
            raise InvalidConditionError("Unexpected end of condition")

        t = tokens[pos]

        # NOT prefix
        if t["kind"] == "not":
            val, pos = self._eval_simple(tokens, pos + 1, state)
            return not val, pos

        # Must be an ident or literal
        if t["kind"] not in ("ident", "float", "int", "string"):
            raise InvalidConditionError(f"Expected value token at position {pos}, got {t}")

        left = self._resolve_value(t, state)
        pos += 1

        # Check for operator
        if pos < len(tokens) and tokens[pos]["kind"] == "op":
            op_tok = tokens[pos]
            pos += 1
            if pos >= len(tokens):
                raise InvalidConditionError("Expected right-hand value after operator")
            right = self._resolve_value(tokens[pos], state)
            pos += 1
            op_fn = _OP_MAP.get(op_tok["value"])
            if op_fn is None:
                raise InvalidConditionError(f"Unknown operator: {op_tok['value']}")
            # Type coerce: if left is a string representation of a number, try numeric
            left_c, right_c = self._coerce(left, right)
            try:
                return op_fn(left_c, right_c), pos
            except TypeError:
                # Incompatible types (e.g. str vs float for > operator)
                return False, pos

        return left, pos

    @staticmethod
    def _coerce(left: Any, right: Any):
        """Try to coerce both sides to the same type for comparison."""
        if isinstance(left, str) and isinstance(right, (int, float)):
            try:
                return float(left), float(right)
            except (ValueError, TypeError):
                pass
        if isinstance(right, str) and isinstance(left, (int, float)):
            try:
                return float(left), float(right)
            except (ValueError, TypeError):
                pass
        return left, right

    def _eval_expr(self, tokens: list, pos: int, state: StateBlock):
        left, pos = self._eval_simple(tokens, pos, state)

        while pos < len(tokens):
            t = tokens[pos]
            if t["kind"] == "and":
                pos += 1
                right, pos = self._eval_simple(tokens, pos, state)
                left = bool(left) and bool(right)
            elif t["kind"] == "or":
                pos += 1
                right, pos = self._eval_simple(tokens, pos, state)
                left = bool(left) or bool(right)
            else:
                break

        return left, pos


# ---------------------------------------------------------------------------
# DirectiveRenderer
# ---------------------------------------------------------------------------

class DirectiveRenderer:
    """Renders a rule directive with appropriate priority emphasis."""

    def render(self, directive: str, priority: Priority) -> str:
        if priority == Priority.critical:
            return f"[CRITICAL] {directive} — This MUST NOT be omitted."
        if priority == Priority.warning:
            return f"[WARNING] {directive}"
        return f"[INFO] {directive}"

    def render_numbered(self, directives: list[tuple[str, Priority]]) -> list[str]:
        return [
            f"{i + 1}. {self.render(d, p)}"
            for i, (d, p) in enumerate(directives)
        ]


# ---------------------------------------------------------------------------
# MandatoryDirectiveGenerator
# ---------------------------------------------------------------------------

class MandatoryDirectiveGenerator:
    def generate_role(self, definition: "CardDefinition") -> str:
        return (
            f"You are a {definition.subject} assistant. "
            "Reason only from the data in this card."
        )

    def generate_freshness(self, definition: "CardDefinition") -> str:
        return (
            f"If data_age_seconds exceeds {definition.staleness_threshold_seconds}, "
            "preface response with WARNING - readings may be stale."
        )

    def generate_hallucination_guard(self) -> str:
        return (
            "Do not invent readings, thresholds, or contact details not present in this card."
        )

    def generate_output_terminus(self) -> str:
        return (
            "End every response with one of: [RESOLVED], [ESCALATED], "
            "[MONITORING], [AWAITING_DATA], [NO_ACTION_REQUIRED]."
        )


# ---------------------------------------------------------------------------
# RuleEngine
# ---------------------------------------------------------------------------

class RuleEngine:
    def __init__(self) -> None:
        self._parser = ConditionParser()
        self._renderer = DirectiveRenderer()
        self._mandatory = MandatoryDirectiveGenerator()

    def evaluate(
        self,
        rules: list[Rule],
        state: StateBlock,
        definition: "CardDefinition",
    ) -> InstructionsBlock:
        """
        Evaluate all applicable rules against the current state and return
        a fully populated InstructionsBlock.
        """
        fired_rules: list[tuple[str, Priority]] = []
        fired_rule_ids: list[str] = []

        for rule in rules:
            if not rule.enabled:
                continue
            if not rule.applies_to(definition.card_type):
                continue
            try:
                triggered = self._parser.evaluate(rule.condition, state)
            except InvalidConditionError:
                triggered = False
            if triggered:
                fired_rules.append((rule.directive, rule.priority))
                fired_rule_ids.append(rule.rule_id)

        # Build reasoning directives: mandatory + fired rules
        hallucination = self._mandatory.generate_hallucination_guard()
        terminus = self._mandatory.generate_output_terminus()

        reasoning_directives = [hallucination]
        numbered = self._renderer.render_numbered(fired_rules)
        reasoning_directives.extend(numbered)

        # Output format
        output_format = [terminus]

        # Escalation contacts
        escalation_contacts = [c.format() for c in definition.contacts]

        return InstructionsBlock(
            generated_from_rules=fired_rule_ids,
            token_budget_pct=definition.token_allocation.get("instructions", 0.20),
            role_directive=self._mandatory.generate_role(definition),
            data_freshness_directive=self._mandatory.generate_freshness(definition),
            reasoning_directives=reasoning_directives,
            output_format=output_format,
            escalation_contacts=escalation_contacts,
        )
