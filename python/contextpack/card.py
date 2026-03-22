"""
card.py — All Pydantic models and enums for ContextPack cards.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Union

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CardType(str, Enum):
    device = "device"
    system = "system"
    zone = "zone"
    process = "process"
    asset = "asset"
    agent = "agent"


class Priority(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class Quality(str, Enum):
    measured = "measured"
    estimated = "estimated"
    stale = "stale"
    simulated = "simulated"


class Trend(str, Enum):
    rising = "rising"
    falling = "falling"
    stable = "stable"
    unknown = "unknown"


class Operator(str, Enum):
    gt = "gt"
    lt = "lt"
    gte = "gte"
    lte = "lte"
    eq = "eq"
    neq = "neq"
    between = "between"


class EventType(str, Enum):
    fault = "fault"
    recovery = "recovery"
    maintenance = "maintenance"
    threshold_breach = "threshold_breach"
    inspection = "inspection"
    configuration_change = "configuration_change"
    anomaly = "anomaly"


class Severity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class RelationshipType(str, Enum):
    depends_on = "depends_on"
    monitors = "monitors"
    controls = "controls"
    feeds = "feeds"


# ---------------------------------------------------------------------------
# Simple dataclasses / small models
# ---------------------------------------------------------------------------

class Reading(BaseModel):
    value: Union[float, int, str, bool]
    unit: Optional[str] = None
    quality: Quality = Quality.measured
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trend: Optional[Trend] = None
    rate_of_change: Optional[float] = None

    model_config = {"use_enum_values": True}


class Contact(BaseModel):
    name: str
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None

    def format(self) -> str:
        parts = [self.name]
        if self.role:
            parts.append(f"({self.role})")
        if self.phone:
            parts.append(self.phone)
        if self.email:
            parts.append(self.email)
        return " ".join(parts)


class MockField(BaseModel):
    """Configuration for a single field in MockAdapter."""
    value: Optional[Union[float, int, str, bool]] = None
    value_fn: Optional[Callable[[], Union[float, int, str, bool]]] = None
    unit: Optional[str] = None
    noise_sigma: float = 0.0
    seed: Optional[int] = None

    model_config = {"arbitrary_types_allowed": True}

    def get_value(self) -> Union[float, int, str, bool]:
        if self.value_fn is not None:
            return self.value_fn()
        return self.value


class Threshold(BaseModel):
    field: str
    operator: Operator
    value: Union[float, int, list[float]]
    severity: Severity
    label: str

    model_config = {"use_enum_values": True}


class DomainFact(BaseModel):
    key: str
    value: str
    tags: list[str] = Field(default_factory=list)


class Relationship(BaseModel):
    type: RelationshipType
    target_card_id: str
    description: str

    model_config = {"use_enum_values": True}


# ---------------------------------------------------------------------------
# Block models
# ---------------------------------------------------------------------------

class IdentityBlock(BaseModel):
    subject: str
    entity_type: str
    location: str
    project_ref: Optional[str] = None
    owner: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class KnowledgeBlock(BaseModel):
    thresholds: dict[str, Threshold] = Field(default_factory=dict)
    domain_facts: list[DomainFact] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)


class StateBlock(BaseModel):
    data_age_seconds: int = 0
    readings: dict[str, Reading] = Field(default_factory=dict)
    derived_state: dict[str, Any] = Field(default_factory=dict)


class HistoryEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt-{uuid.uuid4().hex[:8]}")
    event_type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: Severity
    description: str
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    card_version: str = "1.0"

    model_config = {"use_enum_values": True}


class HistoryBlock(BaseModel):
    token_budget_pct: float = 0.25
    events: list[HistoryEvent] = Field(default_factory=list)


class InstructionsBlock(BaseModel):
    generated_from_rules: list[str] = Field(default_factory=list)
    token_budget_pct: float = 0.20
    role_directive: str = ""
    data_freshness_directive: str = ""
    reasoning_directives: list[str] = Field(default_factory=list)
    output_format: list[str] = Field(default_factory=list)
    escalation_contacts: list[str] = Field(default_factory=list)


class ProvenanceBlock(BaseModel):
    card_writer_version: Optional[str] = None
    sdk_language: Optional[str] = "python"
    adapter_versions: dict[str, str] = Field(default_factory=dict)
    signing: Optional[dict[str, str]] = None
    encryption: Optional[dict[str, str]] = None


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------

class Card(BaseModel):
    contextpack_version: str = "1.0"
    card_id: str
    card_type: CardType
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until: datetime
    authored_by: str = "contextpack-sdk"
    schema_version: str = "1.0"
    identity: IdentityBlock
    state: StateBlock = Field(default_factory=StateBlock)
    knowledge: KnowledgeBlock = Field(default_factory=KnowledgeBlock)
    history: HistoryBlock = Field(default_factory=HistoryBlock)
    instructions: InstructionsBlock = Field(default_factory=InstructionsBlock)
    provenance: Optional[ProvenanceBlock] = None

    model_config = {"use_enum_values": True}


# ---------------------------------------------------------------------------
# CardDefinition — builder / configuration object
# ---------------------------------------------------------------------------

DEFAULT_ALLOCATION: dict[str, float] = {
    "identity": 50,   # fixed tokens
    "state": 0.30,
    "knowledge": 0.25,
    "history": 0.25,
    "instructions": 0.20,
}


class CardDefinition(BaseModel):
    """
    Author-time configuration object used to build a Card.
    """
    card_id: str
    card_type: CardType
    subject: str
    location: str
    entity_type: Optional[str] = None
    token_budget: int = 800
    token_allocation: dict = Field(default_factory=lambda: dict(DEFAULT_ALLOCATION))
    write_interval_seconds: int = 120
    staleness_threshold_seconds: int = 300
    output_dir: Path = Path("./cards")
    signing_enabled: bool = False
    encryption_enabled: bool = False
    contacts: list[Contact] = Field(default_factory=list)

    # Knowledge
    _thresholds: dict[str, Threshold] = {}
    _domain_facts: list[DomainFact] = []
    _relationships: list[Relationship] = []

    # Rules and sources (not serialized as Pydantic fields, stored as plain attrs)
    _rules: list = []
    _data_sources: list = []

    model_config = {"arbitrary_types_allowed": True}

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "_thresholds", {})
        object.__setattr__(self, "_domain_facts", [])
        object.__setattr__(self, "_relationships", [])
        object.__setattr__(self, "_rules", [])
        object.__setattr__(self, "_data_sources", [])

    def add_knowledge_fact(self, key: str, value: str, tags: list[str] = None) -> None:
        self._domain_facts.append(DomainFact(key=key, value=value, tags=tags or []))

    def add_threshold(
        self,
        field: str,
        operator: Union[Operator, str],
        value: Union[float, int, list],
        severity: Union[Severity, str],
        label: str,
    ) -> None:
        name = f"{field}_{operator}_{value}"
        self._thresholds[name] = Threshold(
            field=field,
            operator=operator,
            value=value,
            severity=severity,
            label=label,
        )

    def add_rule(self, rule: "Rule") -> None:  # noqa: F821
        self._rules.append(rule)

    def add_data_source(self, source: "DataSource") -> None:  # noqa: F821
        self._data_sources.append(source)

    def add_dependency(self, card_id: str) -> None:
        # stored for identity block building
        if not hasattr(self, "_dependencies"):
            object.__setattr__(self, "_dependencies", [])
        self._dependencies.append(card_id)

    def add_contact(self, contact: Contact) -> None:
        self.contacts.append(contact)

    def build_identity_block(self) -> IdentityBlock:
        deps = getattr(self, "_dependencies", [])
        return IdentityBlock(
            subject=self.subject,
            entity_type=self.entity_type or self.card_type,
            location=self.location,
            dependencies=deps,
        )

    def build_knowledge_block(self) -> KnowledgeBlock:
        return KnowledgeBlock(
            thresholds=dict(self._thresholds),
            domain_facts=list(self._domain_facts),
            relationships=list(self._relationships),
        )
