"""
writer.py — DerivedStateComputer, BudgetEnforcer, CardWriter.
"""
from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from .card import (
    Card,
    CardDefinition,
    CardType,
    HistoryBlock,
    HistoryEvent,
    IdentityBlock,
    InstructionsBlock,
    KnowledgeBlock,
    ProvenanceBlock,
    Quality,
    Reading,
    Severity,
    StateBlock,
    Trend,
)
from .history import HistoryStore
from .rules import RuleEngine
from .sources import DataSource

logger = logging.getLogger(__name__)

_SDK_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# DerivedStateComputer
# ---------------------------------------------------------------------------

class DerivedStateComputer:
    """Computes trend, rate_of_change, breach_state from reading history."""

    STABLE_VARIANCE_THRESHOLD = 0.01

    def compute_trend(self, values: list[float]) -> Trend:
        """
        Uses last 5 readings.
        rising: monotonically increasing
        falling: monotonically decreasing
        stable: variance below threshold
        unknown: insufficient data (< 2 readings)
        """
        if len(values) < 2:
            return Trend.unknown

        recent = values[-5:]

        # Check monotonically increasing
        if all(recent[i] < recent[i + 1] for i in range(len(recent) - 1)):
            return Trend.rising

        # Check monotonically decreasing
        if all(recent[i] > recent[i + 1] for i in range(len(recent) - 1)):
            return Trend.falling

        # Check stable (low variance)
        if len(recent) >= 2:
            try:
                var = statistics.variance(recent)
                if var < self.STABLE_VARIANCE_THRESHOLD:
                    return Trend.stable
            except statistics.StatisticsError:
                pass

        return Trend.unknown

    def compute_rate_of_change(
        self,
        values: list[float],
        timestamps: list[datetime],
    ) -> Optional[float]:
        """
        Compute float per hour using first and last point in the window.
        Returns None if insufficient data.
        """
        if len(values) < 2 or len(timestamps) < 2:
            return None

        dt_seconds = (timestamps[-1] - timestamps[0]).total_seconds()
        if dt_seconds == 0:
            return None

        dv = values[-1] - values[0]
        return (dv / dt_seconds) * 3600.0  # per hour

    def compute_breach_state(
        self,
        readings: dict[str, Reading],
        thresholds,
    ) -> dict[str, Any]:
        """
        Returns dict of breach flags from thresholds.
        {threshold_name: True/False, "breach_active": bool}
        """
        result: dict[str, Any] = {}
        any_breach = False

        for name, threshold in thresholds.items():
            field_name = threshold.field
            if field_name not in readings:
                continue
            reading = readings[field_name]
            value = reading.value

            # Skip non-numeric values for threshold evaluation
            if not isinstance(value, (int, float)):
                continue

            op = threshold.operator if isinstance(threshold.operator, str) else threshold.operator.value
            t_value = threshold.value

            breached = False
            if op == "gt":
                breached = value > t_value
            elif op == "gte":
                breached = value >= t_value
            elif op == "lt":
                breached = value < t_value
            elif op == "lte":
                breached = value <= t_value
            elif op == "eq":
                breached = value == t_value
            elif op == "neq":
                breached = value != t_value
            elif op == "between" and isinstance(t_value, list) and len(t_value) == 2:
                breached = t_value[0] <= value <= t_value[1]

            result[f"breach_{name}"] = breached
            if breached:
                any_breach = True

        result["breach_active"] = any_breach
        return result

    def update_derived_from_history(
        self,
        derived: dict[str, Any],
        history_store: HistoryStore,
    ) -> dict[str, Any]:
        """Add fault counts from history."""
        derived = dict(derived)
        derived["fault_count_30d"] = history_store.get_fault_count("fault", days=30)
        derived["fault_count_7d"] = history_store.get_fault_count("fault", days=7)
        return derived


# ---------------------------------------------------------------------------
# BudgetEnforcer
# ---------------------------------------------------------------------------

class BudgetEnforcer:
    """
    Measures token usage and summarises over-budget blocks.
    Uses tiktoken cl100k_base.
    """

    def __init__(self) -> None:
        self._enc = None

    def _get_encoder(self):
        if self._enc is None:
            try:
                import tiktoken
                self._enc = tiktoken.get_encoding("cl100k_base")
            except Exception:
                # tiktoken not available or encoding cannot be downloaded
                logger.warning("tiktoken not available or encoding not cached; using character-based estimation")
        return self._enc

    def measure_tokens(self, text: str) -> int:
        enc = self._get_encoder()
        if enc is not None:
            return len(enc.encode(text))
        # Fallback: ~4 chars per token
        return max(1, len(text) // 4)

    def measure_block_tokens(self, block: Any) -> int:
        try:
            text = yaml.dump(block, default_flow_style=False, allow_unicode=True)
        except Exception:
            text = str(block)
        return self.measure_tokens(text)

    def enforce(self, card: Card, budget: int, allocation: dict) -> Card:
        """
        Enforce token budget across all blocks.
        Returns a (possibly summarised) card.
        """
        state_budget = int(budget * allocation.get("state", 0.30))
        knowledge_budget = int(budget * allocation.get("knowledge", 0.25))
        history_budget = int(budget * allocation.get("history", 0.25))
        instructions_budget = int(budget * allocation.get("instructions", 0.20))

        # History
        history_text = yaml.dump(
            card.history.model_dump(), default_flow_style=False, allow_unicode=True
        )
        if self.measure_tokens(history_text) > history_budget:
            card = card.model_copy(
                update={"history": self._summarise_history(card.history, history_budget)}
            )

        # Knowledge
        knowledge_text = yaml.dump(
            card.knowledge.model_dump(), default_flow_style=False, allow_unicode=True
        )
        if self.measure_tokens(knowledge_text) > knowledge_budget:
            card = card.model_copy(
                update={"knowledge": self._summarise_knowledge(card.knowledge, knowledge_budget)}
            )

        return card

    def _summarise_history(self, history: HistoryBlock, budget: int) -> HistoryBlock:
        """Keep all unresolved, keep last 5 resolved, summarise rest."""
        events = history.events
        unresolved = [e for e in events if not e.resolved]
        resolved = [e for e in events if e.resolved]

        kept_resolved = resolved[-5:]
        summarised_count = len(resolved) - len(kept_resolved)

        final_events = list(unresolved) + list(kept_resolved)

        # Check if we still exceed budget
        test_block = HistoryBlock(token_budget_pct=history.token_budget_pct, events=final_events)
        test_text = yaml.dump(test_block.model_dump(), default_flow_style=False, allow_unicode=True)
        if self.measure_tokens(test_text) > budget and len(final_events) > 1:
            # Keep unresolved events (always keep these) and trim resolved
            # Start with just unresolved, then add resolved if budget allows
            candidate = list(unresolved)
            for e in reversed(kept_resolved):
                test_events = candidate + [e]
                test_block = HistoryBlock(token_budget_pct=history.token_budget_pct, events=test_events)
                test_text = yaml.dump(test_block.model_dump(), default_flow_style=False, allow_unicode=True)
                if self.measure_tokens(test_text) <= budget:
                    candidate = test_events
                    break
            final_events = candidate

        logger.debug("BudgetEnforcer: summarised %d resolved history events", summarised_count)
        return HistoryBlock(token_budget_pct=history.token_budget_pct, events=final_events)

    def _summarise_knowledge(self, knowledge: KnowledgeBlock, budget: int) -> KnowledgeBlock:
        """Reduce facts/thresholds to fit budget."""
        # Try removing domain facts first (oldest / least important)
        facts = list(knowledge.domain_facts)
        thresholds = dict(knowledge.thresholds)

        while facts:
            test = knowledge.model_copy(update={"domain_facts": facts})
            test_text = yaml.dump(test.model_dump(), default_flow_style=False, allow_unicode=True)
            if self.measure_tokens(test_text) <= budget:
                return test
            facts = facts[:-1]  # remove last

        # Then try removing thresholds
        threshold_keys = list(thresholds.keys())
        while threshold_keys:
            test_thresholds = {k: thresholds[k] for k in threshold_keys}
            test = knowledge.model_copy(update={"thresholds": test_thresholds, "domain_facts": []})
            test_text = yaml.dump(test.model_dump(), default_flow_style=False, allow_unicode=True)
            if self.measure_tokens(test_text) <= budget:
                return test
            threshold_keys = threshold_keys[:-1]

        return knowledge.model_copy(update={"thresholds": {}, "domain_facts": [], "relationships": []})


# ---------------------------------------------------------------------------
# CardWriter
# ---------------------------------------------------------------------------

class CardWriter:
    """
    Orchestrates the full write cycle for a Card.
    """

    def __init__(
        self,
        definition: CardDefinition,
        rule_engine: Optional[RuleEngine] = None,
        signer=None,
        encryptor=None,
    ) -> None:
        self.definition = definition
        self.rule_engine = rule_engine or RuleEngine()
        self.budget_enforcer = BudgetEnforcer()
        self.signer = signer
        self.encryptor = encryptor

        # Set up history store
        output_dir = Path(definition.output_dir)
        self.history_store = HistoryStore(
            storage_path=output_dir / f"{definition.card_id}.history.json"
        )

        # Ring buffers for derived state (last 10 readings per field)
        self._ring_buffers: dict[str, deque] = {}  # field -> deque of (value, timestamp)

        self._last_card: Optional[Card] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Validator (lazy import to avoid circular)
        self._validator = None

    def _get_validator(self):
        if self._validator is None:
            from .validator import Validator
            self._validator = Validator()
        return self._validator

    def start(self) -> None:
        """Start the background write loop."""
        if self._running:
            logger.warning("CardWriter '%s': already running", self.definition.card_id)
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._write_loop,
            daemon=True,
            name=f"cardwriter-{self.definition.card_id}",
        )
        self._thread.start()
        logger.info("CardWriter '%s': started (interval=%ds)", self.definition.card_id, self.definition.write_interval_seconds)

    def stop(self) -> None:
        """Stop the background write loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("CardWriter '%s': stopped", self.definition.card_id)

    def _write_loop(self) -> None:
        while self._running:
            try:
                self.write_now()
            except Exception as exc:
                logger.error("CardWriter '%s': write cycle error: %s", self.definition.card_id, exc)
            # Sleep in small increments to allow clean shutdown
            elapsed = 0
            interval = self.definition.write_interval_seconds
            while self._running and elapsed < interval:
                time.sleep(min(1, interval - elapsed))
                elapsed += 1

    def write_now(self) -> Card:
        """Execute one full write cycle and return the produced Card."""
        output_dir = Path(self.definition.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Poll all DataSources
        all_readings: dict[str, Reading] = {}
        for source in self.definition._data_sources:
            try:
                readings = source.poll()
                all_readings.update(readings)
            except Exception as exc:
                logger.error(
                    "CardWriter '%s': polling source '%s' failed: %s",
                    self.definition.card_id, source.name, exc,
                )

        # 2. Update ring buffers and compute derived state
        now = datetime.now(timezone.utc)
        for field_name, reading in all_readings.items():
            if isinstance(reading.value, (int, float)):
                if field_name not in self._ring_buffers:
                    self._ring_buffers[field_name] = deque(maxlen=10)
                self._ring_buffers[field_name].append((reading.value, reading.timestamp))

        # Annotate readings with trend and rate_of_change
        dsc = DerivedStateComputer()
        enriched_readings = {}
        for field_name, reading in all_readings.items():
            if field_name in self._ring_buffers:
                buf = list(self._ring_buffers[field_name])
                values = [v for v, _ in buf]
                timestamps = [t for _, t in buf]
                trend = dsc.compute_trend(values)
                roc = dsc.compute_rate_of_change(values, timestamps)
                enriched_readings[field_name] = reading.model_copy(
                    update={"trend": trend.value if hasattr(trend, "value") else trend, "rate_of_change": roc}
                )
            else:
                enriched_readings[field_name] = reading

        # Compute data_age_seconds from oldest reading timestamp
        if enriched_readings:
            oldest_ts = min(
                (r.timestamp for r in enriched_readings.values() if r.timestamp is not None),
                default=now,
            )
            if isinstance(oldest_ts, str):
                oldest_ts = datetime.fromisoformat(oldest_ts.replace("Z", "+00:00"))
            if oldest_ts.tzinfo is None:
                oldest_ts = oldest_ts.replace(tzinfo=timezone.utc)
            data_age_seconds = int((now - oldest_ts).total_seconds())
        else:
            data_age_seconds = 0

        # Compute derived state
        knowledge_block = self.definition.build_knowledge_block()
        derived_state = dsc.compute_breach_state(enriched_readings, knowledge_block.thresholds)
        derived_state = dsc.update_derived_from_history(derived_state, self.history_store)

        state_block = StateBlock(
            data_age_seconds=data_age_seconds,
            readings=enriched_readings,
            derived_state=derived_state,
        )

        # 3. RuleEngine.evaluate → instructions block
        instructions_block = self.rule_engine.evaluate(
            rules=list(self.definition._rules),
            state=state_block,
            definition=self.definition,
        )

        # 4. Build Card
        valid_until = now + timedelta(seconds=self.definition.write_interval_seconds * 2)
        history_events = self.history_store.get_recent(50)

        card = Card(
            card_id=self.definition.card_id,
            card_type=self.definition.card_type,
            generated_at=now,
            valid_until=valid_until,
            authored_by="contextpack-sdk",
            identity=self.definition.build_identity_block(),
            state=state_block,
            knowledge=knowledge_block,
            history=HistoryBlock(
                token_budget_pct=self.definition.token_allocation.get("history", 0.25),
                events=history_events,
            ),
            instructions=instructions_block,
            provenance=ProvenanceBlock(
                card_writer_version=_SDK_VERSION,
                sdk_language="python",
                adapter_versions={s.name: s.version() for s in self.definition._data_sources},
            ),
        )

        # 5. Budget enforcement
        card = self.budget_enforcer.enforce(
            card,
            self.definition.token_budget,
            self.definition.token_allocation,
        )

        # 6. Validate
        try:
            validator = self._get_validator()
            validator.validate(card)
        except Exception as exc:
            logger.warning("CardWriter '%s': validation warning: %s", self.definition.card_id, exc)

        # 7. Sign if enabled
        if self.definition.signing_enabled and self.signer:
            card = self.signer.sign(card)

        # 8. Encrypt if enabled
        if self.definition.encryption_enabled and self.encryptor:
            card = self.encryptor.encrypt(card)

        # 9. Write to disk
        card_path = output_dir / f"{self.definition.card_id}.card.yaml"
        self._write_card(card, card_path)

        self._last_card = card
        logger.debug("CardWriter '%s': wrote card to %s", self.definition.card_id, card_path)
        return card

    def _write_card(self, card: Card, path: Path) -> None:
        """Serialize card to YAML and write to disk."""
        from .encoders.yaml_encoder import YamlEncoder
        content = YamlEncoder().encode(card)
        with open(path, "w") as fh:
            fh.write(content)

    def get_last_card(self) -> Optional[Card]:
        return self._last_card

    def add_history_event(self, event: HistoryEvent) -> None:
        self.history_store.append_event(event)


# ---------------------------------------------------------------------------
# CardReader
# ---------------------------------------------------------------------------

class CardReader:
    """Loads and encodes Cards."""

    def load(self, card_path: Path) -> Card:
        """Load a Card from a YAML file."""
        with open(card_path) as fh:
            data = yaml.safe_load(fh)
        return Card(**data)

    def load_with_dependencies(self, card_path: Path) -> list[Card]:
        """Load a card and all its dependency cards from the same directory."""
        card = self.load(card_path)
        cards = [card]
        base_dir = card_path.parent
        for dep_id in card.identity.dependencies:
            dep_path = base_dir / f"{dep_id}.card.yaml"
            if dep_path.exists():
                try:
                    cards.append(self.load(dep_path))
                except Exception as exc:
                    logger.warning("CardReader: could not load dependency '%s': %s", dep_id, exc)
        return cards

    def encode(self, card: Card, encoding: str) -> str:
        """Encode a single card."""
        from .encoders import get_encoder
        return get_encoder(encoding).encode(card)

    def encode_for_model(self, cards: list[Card], encoding: str) -> str:
        """Encode multiple cards, separated by ---."""
        from .encoders import get_encoder
        enc = get_encoder(encoding)
        parts = [enc.encode(c) for c in cards]
        return "\n---\n".join(parts)
