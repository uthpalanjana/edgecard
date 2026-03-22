"""
adapters/modbus.py — ModbusAdapter supporting TCP and RTU modes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..card import Quality, Reading
from ..sources import ConnectionResult, DataSource

logger = logging.getLogger(__name__)


@dataclass
class RegisterMapping:
    address: int
    field: str
    register_type: str = "holding"  # holding | input | coil | discrete_input
    data_type: str = "uint16"       # uint16 | int16 | uint32 | int32 | float32 | boolean
    scale_factor: float = 1.0
    unit: Optional[str] = None
    byte_order: str = "big"
    word_order: str = "big"


class ModbusAdapter(DataSource):
    """
    Reads Modbus registers over TCP or RTU.
    Connection is opened fresh per poll.
    """

    def __init__(
        self,
        name: str,
        host: str,
        port: int = 502,
        unit_id: int = 1,
        timeout_seconds: float = 3.0,
        registers: list[RegisterMapping] | None = None,
        mode: str = "tcp",
        # RTU-specific
        baudrate: int = 9600,
        parity: str = "N",
        stopbits: int = 1,
        bytesize: int = 8,
        poll_interval_seconds: int = 30,
    ) -> None:
        super().__init__(name=name, poll_interval_seconds=poll_interval_seconds)
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout_seconds = timeout_seconds
        self.registers = registers or []
        self.mode = mode.lower()
        self.baudrate = baudrate
        self.parity = parity
        self.stopbits = stopbits
        self.bytesize = bytesize
        self._last_readings: dict[str, Reading] = {}

    def _get_client(self):
        try:
            from pymodbus.client import ModbusTcpClient, ModbusSerialClient
            from pymodbus.framer import Framer
        except ImportError:
            raise ImportError("pymodbus is not installed")

        if self.mode == "tcp":
            return ModbusTcpClient(
                host=self.host,
                port=self.port,
                timeout=self.timeout_seconds,
            )
        else:  # RTU
            return ModbusSerialClient(
                port=self.host,
                baudrate=self.baudrate,
                parity=self.parity,
                stopbits=self.stopbits,
                bytesize=self.bytesize,
                timeout=self.timeout_seconds,
            )

    def _decode_registers(self, raw_registers: list[int], mapping: RegisterMapping) -> float:
        """Decode register data according to data_type and byte/word order."""
        try:
            from pymodbus.payload import BinaryPayloadDecoder
            from pymodbus.constants import Endian

            byte_order = Endian.BIG if mapping.byte_order.lower() == "big" else Endian.LITTLE
            word_order = Endian.BIG if mapping.word_order.lower() == "big" else Endian.LITTLE

            decoder = BinaryPayloadDecoder.fromRegisters(
                raw_registers,
                byteorder=byte_order,
                wordorder=word_order,
            )

            dt = mapping.data_type.lower()
            if dt == "uint16":
                value = decoder.decode_16bit_uint()
            elif dt == "int16":
                value = decoder.decode_16bit_int()
            elif dt == "uint32":
                value = decoder.decode_32bit_uint()
            elif dt == "int32":
                value = decoder.decode_32bit_int()
            elif dt == "float32":
                value = decoder.decode_32bit_float()
            elif dt == "boolean":
                value = bool(decoder.decode_16bit_uint())
            else:
                value = decoder.decode_16bit_uint()

            return value * mapping.scale_factor
        except Exception as exc:
            logger.error("ModbusAdapter '%s': decode error for '%s': %s", self.name, mapping.field, exc)
            raise

    def poll(self) -> dict[str, Reading]:
        """Groups registers by type, reads them, returns Readings. NEVER raises."""
        results: dict[str, Reading] = {}
        now = datetime.now(timezone.utc)

        try:
            client = self._get_client()
        except ImportError as exc:
            logger.error("ModbusAdapter '%s': %s", self.name, exc)
            return self._stale_copy(now)

        try:
            if not client.connect():
                logger.error("ModbusAdapter '%s': could not connect to %s:%s", self.name, self.host, self.port)
                return self._stale_copy(now)

            for mapping in self.registers:
                try:
                    results[mapping.field] = self._read_register(client, mapping, now)
                except Exception as exc:
                    logger.error("ModbusAdapter '%s': read error for '%s': %s", self.name, mapping.field, exc)
                    if mapping.field in self._last_readings:
                        old = self._last_readings[mapping.field]
                        results[mapping.field] = Reading(value=old.value, unit=old.unit, quality=Quality.stale, timestamp=now)
                    else:
                        results[mapping.field] = Reading(value=0, unit=mapping.unit, quality=Quality.stale, timestamp=now)
        finally:
            try:
                client.close()
            except Exception:
                pass

        self._last_readings = results
        return results

    def _read_register(self, client, mapping: RegisterMapping, now: datetime) -> Reading:
        rt = mapping.register_type.lower()
        count = 2 if mapping.data_type in ("uint32", "int32", "float32") else 1

        if rt == "holding":
            rr = client.read_holding_registers(mapping.address, count=count, slave=self.unit_id)
        elif rt == "input":
            rr = client.read_input_registers(mapping.address, count=count, slave=self.unit_id)
        elif rt == "coil":
            rr = client.read_coils(mapping.address, count=1, slave=self.unit_id)
        elif rt == "discrete_input":
            rr = client.read_discrete_inputs(mapping.address, count=1, slave=self.unit_id)
        else:
            raise ValueError(f"Unknown register type: {mapping.register_type}")

        if rr.isError():
            raise IOError(f"Modbus error reading {rt} register {mapping.address}: {rr}")

        if rt in ("coil", "discrete_input"):
            value = bool(rr.bits[0]) if hasattr(rr, "bits") else bool(rr.registers[0])
            return Reading(value=value, unit=mapping.unit, quality=Quality.measured, timestamp=now)

        value = self._decode_registers(rr.registers, mapping)
        return Reading(value=value, unit=mapping.unit, quality=Quality.measured, timestamp=now)

    def _stale_copy(self, now: datetime) -> dict[str, Reading]:
        if self._last_readings:
            return {
                k: Reading(value=r.value, unit=r.unit, quality=Quality.stale, timestamp=now)
                for k, r in self._last_readings.items()
            }
        return {
            m.field: Reading(value=0, unit=m.unit, quality=Quality.stale, timestamp=now)
            for m in self.registers
        }

    def test_connection(self) -> ConnectionResult:
        """Connect, read holding register 0, disconnect."""
        try:
            client = self._get_client()
        except ImportError as exc:
            return ConnectionResult(success=False, message=str(exc))

        try:
            if not client.connect():
                return ConnectionResult(success=False, message=f"Could not connect to {self.host}:{self.port}")
            rr = client.read_holding_registers(0, count=1, slave=self.unit_id)
            client.close()
            if rr.isError():
                return ConnectionResult(success=False, message=f"Register read error: {rr}")
            return ConnectionResult(success=True, message=f"Connected to {self.host}:{self.port}")
        except Exception as exc:
            try:
                client.close()
            except Exception:
                pass
            return ConnectionResult(success=False, message=str(exc))
