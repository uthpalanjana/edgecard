"""
encoders/toon_encoder.py — Encode a Card to TOON (Token-Optimised Output Notation) format.

TOON format spec:
  ## CARD: {card_id}
  card_type: ...
  subject: ...
  location: ...
  generated_at: ...
  valid_until: ...
  data_age_seconds: N

  readings[N]{field,value,unit,quality,trend,rate_of_change_per_hour}:
  field,value,unit,quality,trend,roc
  ...

  derived:
  key: value
  ...

  thresholds[N]{field,operator,value,severity,label}:
  field,op,value,severity,label
  ...

  facts[N]{key,value}:
  key,value
  ...

  history[N]{event_id,event_type,timestamp,severity,resolved,resolution}:
  event_id,event_type,timestamp,severity,resolved,resolution_note
  ...

  ## INSTRUCTIONS
  Role: ...

  Freshness: ...

  Rules:
  1. ...

  Do not invent readings, thresholds, or contact details not present in this card.

  Output format:
  - ...

  Escalation contacts:
  - ...
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..card import Card


def _toon_escape(value: Any) -> str:
    """
    Convert a value to a string for TOON.
    Values containing commas must be quoted.
    Empty/None becomes empty string.
    """
    if value is None:
        return ""
    s = str(value)
    if "," in s:
        # Escape any inner quotes then wrap in double-quotes
        s = s.replace('"', '""')
        return f'"{s}"'
    return s


def _fmt_ts(ts) -> str:
    """Format a timestamp as ISO8601 string."""
    if ts is None:
        return ""
    if isinstance(ts, str):
        return ts
    if isinstance(ts, datetime):
        s = ts.isoformat()
        if ts.tzinfo is not None and ts.utcoffset().total_seconds() == 0:
            # Replace +00:00 with Z
            s = s.replace("+00:00", "Z")
        return s
    return str(ts)


def _fmt_float(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        # Trim unnecessary trailing zeros
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


class ToonEncoder:
    """Encodes a Card to TOON format."""

    def encode(self, card: Card) -> str:
        lines = []

        # Header
        lines.append(f"## CARD: {card.card_id}")

        card_type_val = card.card_type if isinstance(card.card_type, str) else card.card_type.value
        lines.append(f"card_type: {card_type_val}")
        lines.append(f"subject: {card.identity.subject}")
        lines.append(f"location: {card.identity.location}")
        lines.append(f"generated_at: {_fmt_ts(card.generated_at)}")
        lines.append(f"valid_until: {_fmt_ts(card.valid_until)}")
        lines.append(f"data_age_seconds: {card.state.data_age_seconds}")
        lines.append("")

        # Readings block
        readings = card.state.readings
        lines.append(f"readings[{len(readings)}]{{field,value,unit,quality,trend,rate_of_change_per_hour}}:")
        for field_name, r in readings.items():
            value_str = _toon_escape(r.value)
            unit_str = _toon_escape(r.unit)
            quality_str = r.quality if isinstance(r.quality, str) else r.quality.value
            trend_str = ""
            if r.trend is not None:
                trend_str = r.trend if isinstance(r.trend, str) else r.trend.value
            roc_str = ""
            if r.rate_of_change is not None:
                roc_str = _fmt_float(r.rate_of_change)
            lines.append(f"{_toon_escape(field_name)},{value_str},{unit_str},{quality_str},{trend_str},{roc_str}")
        lines.append("")

        # Derived state
        derived = card.state.derived_state
        if derived:
            lines.append("derived:")
            for k, v in derived.items():
                lines.append(f"{k}: {_toon_escape(v)}")
            lines.append("")

        # Thresholds block
        thresholds = card.knowledge.thresholds
        if thresholds:
            lines.append(f"thresholds[{len(thresholds)}]{{field,operator,value,severity,label}}:")
            for name, t in thresholds.items():
                op_str = t.operator if isinstance(t.operator, str) else t.operator.value
                sev_str = t.severity if isinstance(t.severity, str) else t.severity.value
                val_str = _toon_escape(t.value)
                label_str = _toon_escape(t.label)
                field_str = _toon_escape(t.field)
                lines.append(f"{field_str},{op_str},{val_str},{sev_str},{label_str}")
            lines.append("")

        # Facts block
        facts = card.knowledge.domain_facts
        if facts:
            lines.append(f"facts[{len(facts)}]{{key,value}}:")
            for fact in facts:
                lines.append(f"{_toon_escape(fact.key)},{_toon_escape(fact.value)}")
            lines.append("")

        # Relationships block
        relationships = card.knowledge.relationships
        if relationships:
            lines.append(f"relationships[{len(relationships)}]{{type,target_card_id,description}}:")
            for rel in relationships:
                rel_type = rel.type if isinstance(rel.type, str) else rel.type.value
                lines.append(f"{rel_type},{_toon_escape(rel.target_card_id)},{_toon_escape(rel.description)}")
            lines.append("")

        # History block
        events = card.history.events
        if events:
            lines.append(f"history[{len(events)}]{{event_id,event_type,timestamp,severity,resolved,resolution}}:")
            for evt in events:
                evt_type = evt.event_type if isinstance(evt.event_type, str) else evt.event_type.value
                sev_str = evt.severity if isinstance(evt.severity, str) else evt.severity.value
                resolution = _toon_escape(evt.resolution_note) if evt.resolved and evt.resolution_note else ""
                lines.append(
                    f"{_toon_escape(evt.event_id)},{evt_type},{_fmt_ts(evt.timestamp)}"
                    f",{sev_str},{str(evt.resolved).lower()},{resolution}"
                )
            lines.append("")

        # Instructions separator
        lines.append("## INSTRUCTIONS")

        inst = card.instructions
        lines.append(f"Role: {inst.role_directive}")
        lines.append("")
        lines.append(f"Freshness: {inst.data_freshness_directive}")
        lines.append("")

        # Reasoning directives — split mandatory hallucination guard from numbered rules
        reasoning = inst.reasoning_directives
        if reasoning:
            # First directive is the hallucination guard (not a numbered rule)
            hallucination_guard = None
            numbered_directives = []
            for d in reasoning:
                if d.startswith("1.") or d.startswith("[CRITICAL]") or d.startswith("[WARNING]") or d.startswith("[INFO]"):
                    numbered_directives.append(d)
                elif not numbered_directives and hallucination_guard is None and not d[0].isdigit():
                    hallucination_guard = d
                else:
                    numbered_directives.append(d)

            if numbered_directives:
                lines.append("Rules:")
                for d in numbered_directives:
                    lines.append(d)
                lines.append("")

            if hallucination_guard:
                lines.append(hallucination_guard)
                lines.append("")

        # Output format
        if inst.output_format:
            lines.append("Output format:")
            for item in inst.output_format:
                lines.append(f"- {item}")
            lines.append("")

        # Escalation contacts
        if inst.escalation_contacts:
            lines.append("Escalation contacts:")
            for contact in inst.escalation_contacts:
                lines.append(f"- {contact}")
            lines.append("")

        # Remove trailing blank line if present
        while lines and lines[-1] == "":
            lines.pop()

        return "\n".join(lines)
