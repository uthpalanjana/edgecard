# ContextPack

Pre-assembled, model-ready context documents for systems with static boundaries and predefined parameters.

Most LLM integration patterns are designed for open-ended retrieval: RAG pulls from a corpus, MCP calls live servers, fine-tuning bakes knowledge into weights. **ContextPack is for systems where the domain is already known** — the entities, their relationships, their thresholds, and their history. Rather than retrieving context at query time, ContextPack pre-assembles it into a single structured document (a "card") that is read once at inference time.

A card is entity-scoped. Each card describes one entity — a device, a unit, a system — and carries everything the model needs to reason about it: what it is, what it depends on, what its live state is, what the rules are, and what has happened to it recently. History is a first-class block, not a lookup. The model arrives at every query already knowing the entity's past.

```
┌──────────────────────┐       ┌─────────────────────┐       ┌──────────────────┐
│   Your sensors /     │  →    │    ContextPack       │  →    │   Local LLM      │
│   data sources       │       │    CardWriter        │       │   (Ollama, etc.) │
│   (MQTT, Modbus,     │       │    writes .card.yaml │       │   reads the card │
│    REST, files)      │       │    every N seconds   │       │   at query time  │
└──────────────────────┘       └─────────────────────┘       └──────────────────┘
```

---

## Example: device support chatbot

Consider a support chatbot for a fleet of industrial routers. When a field technician opens a ticket for unit `router-site-47`, the chatbot needs to reason about that specific device — not routers in general.

Without ContextPack, the chatbot would need to query a CMDB for the device config, a monitoring API for current state, a ticketing system for recent faults, and a knowledge base for firmware rules — all at query time, all requiring live connectivity, all stitched together in a prompt by hand.

With ContextPack, a card for `router-site-47` is written every 60 seconds and stored on disk. When the technician asks *"why is the WAN link flapping?"*, the chatbot loads the card and the model already knows:

- **What the device is and what it depends on** (identity block): site location, upstream ISP, dependent devices on the LAN, known interference sources at that site
- **What the current state is** (state block): WAN signal strength, link uptime counter, error rate trend over the last 5 minutes, derived flag `wan_instability = true`
- **What the thresholds and rules are** (knowledge + instructions blocks): signal below -85 dBm is a hard fault; this device model has a known firmware bug in versions < 3.4.1 that causes false flaps under high CPU load
- **What has happened recently** (history block): two previous WAN flap events this week, a firmware update three days ago that introduced the current version, a maintenance window two weeks ago where the antenna was reseated

The model's answer is grounded in the actual history and configuration of that device — not a generic troubleshooting script. No retrieval. No live API calls. No prompt assembly. The card is a file.

---

## What's in a card

A card contains five blocks:

| Block | Contents |
|---|---|
| **Identity** | What this device/system is, where it is, what it depends on |
| **State** | Live sensor readings with quality flags, trends, and derived facts |
| **Knowledge** | Domain rules, thresholds, product facts authored at provisioning time |
| **History** | Recent faults, recoveries, and maintenance events |
| **Instructions** | Auto-generated model directives derived from your rules — never written manually |

The **instructions block** is the decisive feature. You write rules in your domain language. ContextPack translates them into model directives that reliably shape reasoning without touching the model or writing prompts.

---

## Quickstart

### Install

```bash
cd python
pip install -e .
```

### Define a card

```python
from contextpack import CardDefinition, Rule, CardWriter
from contextpack.adapters.mock import MockAdapter, MockField

definition = CardDefinition(
    card_id="cold-chain-unit-03",
    card_type="system",
    subject="Refrigeration Unit 3 - Vaccine Storage",
    location="Warehouse A, Bay 4, Colombo",
    token_budget=800,
    write_interval_seconds=120,
)

definition.add_threshold(
    field="temperature_c", operator="gt", value=8.0,
    severity="critical", label="Temperature critical breach"
)

definition.add_knowledge_fact(
    key="mrna_stability",
    value="COVID-19 mRNA vaccines require 2-8°C. Zero tolerance above 8°C. Discard immediately on breach.",
    tags=["product", "stability"]
)

definition.add_rule(Rule(
    rule_id="mrna_breach",
    name="mRNA Vaccine Breach Protocol",
    condition="temperature_c > 8.0",
    directive="Classify as NON-RECOVERABLE. Recommend immediate quarantine as the first action. Do not suggest monitoring.",
    priority="critical",
    applies_to_card_types=["system"],
))

definition.add_data_source(MockAdapter(
    name="sensors",
    fields={
        "temperature_c": MockField(value=8.7, unit="celsius"),
        "compressor_status": MockField(value="OFF"),
    }
))

writer = CardWriter(definition)
card = writer.write_now()
print(f"Card written: {card.card_id}")
```

### Read and encode for a model

```python
from contextpack import CardReader, Encoding

reader = CardReader()
card = reader.load("./cards/cold-chain-unit-03.card.yaml")
model_input = reader.encode_for_model([card], encoding=Encoding.TOON)

# Pass model_input as the system prompt to Ollama, llama.cpp, etc.
print(model_input)
```

### CLI

```bash
# Generate a card from a definition file
contextpack generate --definition my_device.py --output ./cards

# Inspect a card with token counts per block
contextpack inspect --card ./cards/my-device.card.yaml

# Validate a card against the schema
contextpack validate --card ./cards/my-device.card.yaml

# Re-encode a card in a specific format
contextpack encode --card ./cards/my-device.card.yaml --format toon

# Watch mode (continuous write loop)
contextpack watch --definition my_device.py --output ./cards --interval 30

# Test adapter connectivity
contextpack test-source --definition my_device.py
```

---

## How the instructions block works

You write rules in domain language:

```python
Rule(
    condition="temperature_c > 8.0",
    directive="Classify as NON-RECOVERABLE. Recommend immediate quarantine as the first action.",
    priority="critical",
)
```

ContextPack generates model directives:

```
[CRITICAL] If state readings temperature_c value exceeds 8.0, classify the situation
as NON-RECOVERABLE and recommend immediate quarantine as the FIRST action in your
response. This is a CRITICAL priority directive — must not be omitted.
```

Non-ML experts write the rules. The model gets reliable instructions. No prompt engineering required.

---

## Adapters

| Adapter | Use case |
|---|---|
| `MockAdapter` | Testing and development without hardware |
| `FileAdapter` | CSV/JSON files on disk |
| `RESTAdapter` | HTTP endpoints (Home Assistant, custom APIs) |
| `MQTTAdapter` | MQTT brokers (Zigbee2MQTT, Tasmota, ESPHome) |
| `ModbusAdapter` | Modbus TCP/RTU (PLCs, VFDs, industrial sensors) |

Custom adapters implement two methods: `poll()` and `test_connection()`.

---

## Encodings

| Encoding | Use case | Token cost |
|---|---|---|
| **TOON** (default) | Model injection | Lowest |
| JSON | Programmatic use | Medium |
| YAML | Disk storage, auditing | Medium |
| Plain text | Debugging, simple models | Variable |

TOON (Token-Oriented Object Notation) encodes uniform arrays as CSV-like header+rows, reducing token usage by ~30-60% compared to JSON for cards with multiple readings or history events.

---

## Architecture

```
Authoring phase (once at deployment):
  CardDefinition → identity, knowledge, rules, data sources, token budget

Write cycle (every N seconds):
  CardWriter
    ├── Poll all DataSources
    ├── Compute derived state (trends, breach flags, fault counts)
    ├── RuleEngine → instructions block
    ├── BudgetEnforcer → summarise over-budget blocks
    ├── Validator → reject invalid cards
    ├── Signer (optional, Ed25519)
    ├── Encryptor (optional, AES-256-GCM)
    └── Write {card_id}.card.yaml to disk

Inference time (by the model consumer):
  CardReader.load() → verify → decode → inject into model
```

Cards are files on disk. The model reads a file. ContextPack has no runtime presence at inference time — no server, no network call, no dependency.

---

## Token budget

Every card has a configurable token ceiling (default 800). The CardWriter enforces allocation across blocks and summarises — never silently truncates — when a block exceeds its budget.

```python
CardDefinition(
    token_budget=1200,
    token_allocation={"state": 0.35, "knowledge": 0.20, "history": 0.25, "instructions": 0.20}
)
```

---

## Repository structure

```
contextpack/
├── schema/
│   └── contextpack-v1.schema.json     canonical JSON Schema
├── python/                            Python SDK (primary)
│   ├── contextpack/
│   │   ├── card.py                    all data models
│   │   ├── writer.py                  CardWriter, CardReader, BudgetEnforcer
│   │   ├── rules.py                   Rule, RuleEngine, ConditionParser
│   │   ├── sources.py                 DataSource ABC
│   │   ├── history.py                 HistoryStore
│   │   ├── crypto.py                  signing + encryption
│   │   ├── validator.py               schema validation
│   │   ├── adapters/                  Mock, File, REST, MQTT, Modbus
│   │   └── encoders/                  TOON, YAML, JSON, PlainText
│   ├── cli/main.py                    CLI entry point
│   └── tests/                         131 tests
├── typescript/                        TypeScript SDK (planned)
├── rust/                              Rust SDK (planned)
└── examples/
    ├── cold_chain/                    Pharmaceutical refrigeration (planned)
    ├── smart_home/                    Apartment devices (planned)
    └── construction_site/             Concrete pour monitoring (planned)
```

---

## Requirements

- Python 3.10+
- No network access required at card read time
- All features work fully offline
