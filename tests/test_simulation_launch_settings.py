"""The simulator's settings reach it on its standard input, never as a file.

The settings carry each simulated device's config with its saved logins filled
in, so a file holding them is a login in clear on disk for as long as the file
lasts, and a server that ends without its own clean-up leaves it. The server
writes them to the simulator's standard input and closes it; the simulator
reads to the end before it starts anything. Pinned here with real child
processes, since what matters is how a pipe behaves:

- settings larger than a pipe buffer arrive whole, read by a child that is
  slow to start reading
- a simulator that exits before reading them is left to the readiness wait,
  which says why it exited; the hand-over itself raises nothing
- the simulator reads them from standard input when told ``--config -``, and
  a file path still works for a standalone run
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from types import SimpleNamespace

import pytest

from openavc.core import simulation
from openavc.simulator import __main__ as simulator_entry
from openavc.utils.spawn import CREATE_NO_WINDOW


async def _child(code: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
    )


def _settings(devices: int) -> dict:
    return {
        "ui_port": 19500,
        "devices": [
            {"device_id": f"d{i}", "device_name": f"Salle {i} étage",
             "config": {"password": "x" * 200}}
            for i in range(devices)
        ],
    }


@pytest.mark.asyncio
async def test_settings_larger_than_a_pipe_buffer_arrive_whole():
    settings = _settings(1000)
    assert len(json.dumps(settings)) > 4 * 65536
    process = await _child(
        "import json, sys, time\n"
        "time.sleep(0.3)\n"
        "got = json.loads(sys.stdin.buffer.read())\n"
        "print(len(got['devices']), got['devices'][-1]['device_name'].encode().hex())\n"
    )
    await asyncio.wait_for(simulation._hand_over_settings(process, settings), timeout=10)
    out, err = await asyncio.wait_for(process.communicate(), timeout=10)
    assert process.returncode == 0, err.decode(errors="replace")
    count, name = out.split()
    assert int(count) == 1000
    assert bytes.fromhex(name.decode()).decode() == "Salle 999 étage"


@pytest.mark.asyncio
async def test_a_simulator_that_exits_before_reading_leaves_the_reason_to_the_readiness_wait():
    process = await _child("import sys\nsys.exit(3)\n")
    await process.wait()
    # Larger than a pipe buffer, so the write meets the closed end.
    await asyncio.wait_for(simulation._hand_over_settings(process, _settings(1000)), timeout=10)
    assert process.returncode == 3


def test_the_simulator_reads_its_settings_from_standard_input(monkeypatch):
    settings = _settings(2)
    raw = json.dumps(settings, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw)))
    assert simulator_entry.load_config("-") == settings


def test_a_settings_file_still_works_for_a_standalone_run(tmp_path):
    path = tmp_path / "sim_config.json"
    path.write_text(json.dumps(_settings(1)))
    assert simulator_entry.load_config(str(path)) == _settings(1)
    assert simulator_entry.load_config(None) == {}
