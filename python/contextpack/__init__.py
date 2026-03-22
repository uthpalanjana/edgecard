"""
ContextPack Python SDK — build, write, and encode AI context cards.
"""

from .card import (
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
from .rules import Rule, RuleEngine, InvalidConditionError
from .sources import DataSource, ConnectionResult
from .writer import CardWriter, CardReader
from .encoders import Encoding
from .history import HistoryStore

__all__ = [
    # Card models
    "Card",
    "CardDefinition",
    "CardType",
    "Contact",
    "DomainFact",
    "EventType",
    "HistoryBlock",
    "HistoryEvent",
    "IdentityBlock",
    "InstructionsBlock",
    "KnowledgeBlock",
    "MockField",
    "Operator",
    "Priority",
    "ProvenanceBlock",
    "Quality",
    "Reading",
    "Relationship",
    "RelationshipType",
    "Severity",
    "StateBlock",
    "Threshold",
    "Trend",
    # Rules
    "Rule",
    "RuleEngine",
    "InvalidConditionError",
    # Sources
    "DataSource",
    "ConnectionResult",
    # Writer
    "CardWriter",
    "CardReader",
    # Encoders
    "Encoding",
    # History
    "HistoryStore",
]
