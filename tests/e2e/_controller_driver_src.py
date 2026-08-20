"""Source for the synthetic controller driver used by E2E tests.

Copied to ``driver_repo/`` by ``conftest.py`` so the server subprocess can
discover and instantiate it like a real community driver. Kept as a
plain source file rather than imported at test time because the loader
operates on filesystem paths, not Python module objects.

The driver has no real transport. ``connect()`` skips network I/O and
synthesizes ``initial_children`` registrations from device config, then
runs a 200 ms file-watch loop reading add/remove ops from the path in
``OPENAVC_E2E_CONTROL_FILE``. Tests drive runtime mutations by writing
new ``seq`` values into that file; the driver ignores duplicate seqs so
file reads are idempotent.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from openavc.drivers.base import BaseDriver


class E2ETestController(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "e2e_test_controller",
        "name": "E2E Test Controller",
        "manufacturer": "OpenAVC",
        "category": "controller",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "config_schema": {
            "initial_children": {"type": "integer", "default": 0},
        },
        "child_entity_types": {
            "encoder": {
                "label": "Encoder",
                "label_plural": "Encoders",
                "id_format": {
                    "type": "integer", "min": 1, "max": 9999, "pad_width": 3,
                },
                "state_variables": {
                    "name": {"type": "string"},
                    "ip": {"type": "string"},
                    "signal_present": {"type": "boolean"},
                },
                "summary_fields": ["name", "ip", "signal_present"],
                "label_field": "name",
            },
        },
    }

    async def connect(self) -> None:
        self._connected = True
        self.set_state("connected", True)
        await self.events.emit(f"device.connected.{self.device_id}")

        initial = int(self.config.get("initial_children", 0))
        for lid in range(1, initial + 1):
            self.register_child(
                "encoder", lid,
                initial_state={
                    "name": f"Encoder {lid}",
                    "ip": f"10.0.0.{(lid % 250) + 1}",
                    "signal_present": (lid % 3) != 0,
                },
            )

        ctrl = os.environ.get("OPENAVC_E2E_CONTROL_FILE")
        if ctrl:
            self._poll_task = asyncio.create_task(self._watch_control_file(ctrl))

    async def disconnect(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
        self._poll_task = None
        self._connected = False
        self.set_state("connected", False)
        await self.events.emit(f"device.disconnected.{self.device_id}")

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def refresh_children(self) -> dict[str, Any]:
        return {
            "encoder_count": len(self._children.get("encoder", {})),
        }

    async def _watch_control_file(self, path: str) -> None:
        last_seq = -1
        while True:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                seq = int(data.get("seq", 0))
                if seq != last_seq:
                    last_seq = seq
                    for op in data.get("operations", []):
                        self._apply_op(op)
            except FileNotFoundError:
                pass
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            await asyncio.sleep(0.2)

    def _apply_op(self, op: dict[str, Any]) -> None:
        kind = op.get("op")
        ctype = op.get("child_type", "encoder")
        lid = int(op["local_id"])
        if kind == "add":
            initial_state = op.get("initial_state") or {
                "name": f"Encoder {lid}",
                "ip": f"10.0.0.{(lid % 250) + 1}",
                "signal_present": True,
            }
            self.register_child(ctype, lid, initial_state=initial_state)
        elif kind == "remove":
            if self.is_child_registered(ctype, lid):
                self.deregister_child(ctype, lid)
        elif kind == "fault":
            # Drive the child fault vocabulary from the test: `code` empty
            # clears, a code sets it and takes `online` down with it.
            if self.is_child_registered(ctype, lid):
                self.set_child_state_batch(
                    ctype, lid,
                    self.child_fault(op.get("code", ""), op.get("message", "")),
                )
