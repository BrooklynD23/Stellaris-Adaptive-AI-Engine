"""Name -> type adapter registry used by the schema exporter and the contract example tests."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from sae.contracts.c3_intent import INTENT_ADAPTER, IntentBatch
from sae.contracts.c4_parse import PARSE_OUTPUT_ADAPTER, FixtureSpec
from sae.contracts.c5_bus import BUS_EVENT_ADAPTER, GOD_BUS_EVENT_ADAPTER
from sae.contracts.c6_adapter import EmitReceipt, GodObservation, IntentAck, Observation
from sae.contracts.c7_governor import GovernorConfig, GovernorDecision, SensorSample, WorkerOutcome
from sae.contracts.c8_config import SaeConfig
from sae.contracts.c9_control import CONTROL_COMMAND_ADAPTER, ControlResult

#: Every JSON-shaped contract type, by the name used in ``model=<Name>`` doc fences and schema files.
MODELS: dict[str, TypeAdapter[Any]] = {
    "Intent": INTENT_ADAPTER,
    "IntentBatch": TypeAdapter(IntentBatch),
    "ParseOutput": PARSE_OUTPUT_ADAPTER,
    "FixtureSpec": TypeAdapter(FixtureSpec),
    "BusEvent": BUS_EVENT_ADAPTER,
    "GodBusEvent": GOD_BUS_EVENT_ADAPTER,
    "Observation": TypeAdapter(Observation),
    "GodObservation": TypeAdapter(GodObservation),
    "IntentAck": TypeAdapter(IntentAck),
    "EmitReceipt": TypeAdapter(EmitReceipt),
    "GovernorConfig": TypeAdapter(GovernorConfig),
    "SensorSample": TypeAdapter(SensorSample),
    "GovernorDecision": TypeAdapter(GovernorDecision),
    "WorkerOutcome": TypeAdapter(WorkerOutcome),
    "SaeConfig": TypeAdapter(SaeConfig),
    "ControlCommand": CONTROL_COMMAND_ADAPTER,
    "ControlResult": TypeAdapter(ControlResult),
}
