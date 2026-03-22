"""
encoders/text_encoder.py — Encode a Card to human-readable plain text.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..card import Card


def _age_str(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"


def _fmt_ts(ts) -> str:
    if ts is None:
        return ""
    if isinstance(ts, str):
        return ts
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%dT%H:%M:%SZ") if ts.tzinfo is not None else ts.isoformat()
    return str(ts)


class TextEncoder:
    """Encodes a Card to plain text suitable for human reading or simple LLM prompting."""

    def encode(self, card: Card) -> str:
        lines = []

        card_type_val = card.card_type if isinstance(card.card_type, str) else card.card_type.value

        lines.append(f"CARD: {card.identity.subject}")
        lines.append(f"Type: {card_type_val}")
        lines.append(f"Location: {card.identity.location}")
        lines.append(f"Generated: {_fmt_ts(card.generated_at)} ({_age_str(card.state.data_age_seconds)} data)")
        lines.append("")

        # State
        lines.append("CURRENT STATE:")
        for field_name, r in card.state.readings.items():
            unit_str = f" {r.unit}" if r.unit else ""
            trend_str = ""
            if r.trend:
                trend_val = r.trend if isinstance(r.trend, str) else r.trend.value
                roc_str = ""
                if r.rate_of_change is not None:
                    roc_str = f" at {r.rate_of_change:.1f}{unit_str}/hour"
                trend_str = f" ({trend_val.upper()}{roc_str})"
            quality_str = r.quality if isinstance(r.quality, str) else r.quality.value

            # Check threshold breach
            breach_str = ""
            for t_name, threshold in card.knowledge.thresholds.items():
                if threshold.field == field_name:
                    op = threshold.operator if isinstance(threshold.operator, str) else threshold.operator.value
                    sev = threshold.severity if isinstance(threshold.severity, str) else threshold.severity.value
                    val = threshold.value
                    label = threshold.label
                    # Simple breach check
                    r_val = r.value
                    breached = False
                    if isinstance(r_val, (int, float)):
                        if op == "gt" and r_val > val:
                            breached = True
                        elif op == "gte" and r_val >= val:
                            breached = True
                        elif op == "lt" and r_val < val:
                            breached = True
                        elif op == "lte" and r_val <= val:
                            breached = True
                    if breached:
                        breach_str = f" [{sev.upper()} - {label}]"
                        break

            lines.append(f"  - {field_name}: {r.value}{unit_str}{trend_str}{breach_str} [{quality_str}]")

        if card.state.derived_state:
            lines.append("")
            for k, v in card.state.derived_state.items():
                lines.append(f"  {k}: {v}")

        lines.append("")

        # Knowledge
        if card.knowledge.thresholds or card.knowledge.domain_facts:
            lines.append("PRODUCT KNOWLEDGE:")

            if card.knowledge.thresholds:
                lines.append("  Thresholds:")
                for name, t in card.knowledge.thresholds.items():
                    op = t.operator if isinstance(t.operator, str) else t.operator.value
                    sev = t.severity if isinstance(t.severity, str) else t.severity.value
                    lines.append(f"    - {t.label} ({sev.upper()}): {t.field} {op} {t.value}")

            if card.knowledge.domain_facts:
                lines.append("  Facts:")
                for fact in card.knowledge.domain_facts:
                    lines.append(f"    - {fact.key}: {fact.value}")

            lines.append("")

        # History
        if card.history.events:
            lines.append("RECENT HISTORY:")
            for evt in card.history.events:
                evt_type = evt.event_type if isinstance(evt.event_type, str) else evt.event_type.value
                sev_str = evt.severity if isinstance(evt.severity, str) else evt.severity.value
                resolved_str = "RESOLVED" if evt.resolved else "OPEN"
                resolution = f" ({evt.resolution_note})" if evt.resolved and evt.resolution_note else ""
                lines.append(
                    f"  - [{sev_str.upper()}] {_fmt_ts(evt.timestamp)} {evt_type}: "
                    f"{evt.description} [{resolved_str}]{resolution}"
                )
            lines.append("")

        # Instructions
        inst = card.instructions
        lines.append("INSTRUCTIONS:")
        lines.append(f"  Role: {inst.role_directive}")
        lines.append(f"  Freshness: {inst.data_freshness_directive}")

        if inst.reasoning_directives:
            lines.append("  Directives:")
            for d in inst.reasoning_directives:
                lines.append(f"    - {d}")

        if inst.output_format:
            lines.append("  Output format:")
            for item in inst.output_format:
                lines.append(f"    - {item}")

        if inst.escalation_contacts:
            lines.append("  Escalation contacts:")
            for contact in inst.escalation_contacts:
                lines.append(f"    - {contact}")

        return "\n".join(lines)
