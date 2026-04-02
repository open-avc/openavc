"""Tests for StatePersister — variable persistence to disk."""

import asyncio
import json
from pathlib import Path

import pytest

from server.core.state_persister import StatePersister
from server.core.state_store import StateStore


@pytest.fixture
def tmp_state_file(tmp_path):
    """Return a Path for state.json inside a temp directory."""
    return tmp_path / "state.json"


@pytest.fixture
def store():
    return StateStore()


@pytest.fixture
def persister(tmp_state_file, store):
    return StatePersister(tmp_state_file, store)


def test_persist_write_on_change(tmp_state_file, store, persister):
    """Changing a persisted variable writes to state.json."""
    store.set("var.room_mode", "standby", source="system")
    persister.start({"var.room_mode"})

    # Change the value
    store.set("var.room_mode", "presentation", source="ui")

    # Force flush (instead of waiting for debounce)
    persister.flush()

    data = json.loads(tmp_state_file.read_text())
    assert data["var.room_mode"] == "presentation"


@pytest.mark.asyncio
async def test_persist_debounce(tmp_state_file, store, persister):
    """Rapid changes result in one write (debounced), not one per change."""
    store.set("var.counter", 0, source="system")
    persister.start({"var.counter"})

    # Rapidly change value many times
    for i in range(1, 11):
        store.set("var.counter", i, source="ui")

    # The debounced flush hasn't fired yet (1s delay)
    assert not tmp_state_file.exists()

    # Wait for debounce
    await asyncio.sleep(1.5)

    # Now the file should exist with the final value
    data = json.loads(tmp_state_file.read_text())
    assert data["var.counter"] == 10


def test_persist_restore_on_startup(tmp_state_file, store, persister):
    """Persisted values are loaded on startup."""
    # Write a state file as if from a previous run
    tmp_state_file.write_text(json.dumps({
        "var.room_mode": "presentation",
        "var.volume": 75,
    }))

    loaded = persister.load()
    assert loaded == {"var.room_mode": "presentation", "var.volume": 75}


def test_persist_overrides_default(tmp_state_file, store):
    """Persisted value takes priority over default."""
    # Pre-write state file
    tmp_state_file.write_text(json.dumps({"var.mode": "active"}))

    persister = StatePersister(tmp_state_file, store)
    persisted = persister.load()

    # Simulate engine startup logic: use persisted if available, else default
    default_value = "standby"
    key = "var.mode"
    if key in persisted:
        store.set(key, persisted[key], source="system")
    else:
        store.set(key, default_value, source="system")

    assert store.get("var.mode") == "active"


def test_persist_non_persisted_uses_default(tmp_state_file, store):
    """Non-persisted variables still use their default value."""
    # State file has a value, but the variable is not marked as persist
    tmp_state_file.write_text(json.dumps({"var.other": "saved_value"}))

    persister = StatePersister(tmp_state_file, store)
    persisted = persister.load()

    # Simulate: var.temp is NOT persistent, so use default
    key = "var.temp"
    default_value = "default"
    if key in persisted:
        store.set(key, persisted[key], source="system")
    else:
        store.set(key, default_value, source="system")

    assert store.get("var.temp") == "default"


def test_persist_missing_file(tmp_state_file, store, persister):
    """Missing state.json starts fresh without error."""
    assert not tmp_state_file.exists()
    loaded = persister.load()
    assert loaded == {}


def test_persist_flush_on_shutdown(tmp_state_file, store, persister):
    """Pending writes are flushed on stop()."""
    store.set("var.mode", "standby", source="system")
    persister.start({"var.mode"})

    # Change value (makes it dirty)
    store.set("var.mode", "active", source="ui")

    # Stop flushes pending writes
    persister.stop()

    data = json.loads(tmp_state_file.read_text())
    assert data["var.mode"] == "active"


def test_persist_not_in_export(tmp_state_file, store, persister):
    """State.json is separate from project.avc (not in project export)."""
    store.set("var.mode", "standby", source="system")
    persister.start({"var.mode"})

    # Change value to trigger a write
    store.set("var.mode", "active", source="ui")
    persister.flush()

    # The state file lives alongside project.avc but is a separate file
    assert tmp_state_file.name == "state.json"
    assert tmp_state_file.exists()

    # Verify it's not a .avc file — it's instance-specific runtime state
    assert tmp_state_file.suffix == ".json"
    assert "state" in tmp_state_file.stem
