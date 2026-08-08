"""Tests for StatePersister — variable persistence to disk."""

import asyncio
import json

import pytest

from openavc.core.state_persister import StatePersister
from openavc.core.state_store import StateStore


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


def test_persist_explicit_none_survives(tmp_state_file, store, persister):
    """A persistent variable explicitly set to None persists as null (not
    dropped), so it survives a restart instead of reverting to its default —
    and a sibling write doesn't wipe it either."""
    store.set("var.source", "hdmi1", source="system")
    persister.start({"var.source", "var.label"})

    # Clear the selection to None, and write a sibling so a single flush
    # captures both keys together.
    store.set("var.source", None, source="ui")
    store.set("var.label", "Main", source="ui")
    persister.flush()

    data = json.loads(tmp_state_file.read_text())
    assert "var.source" in data         # present...
    assert data["var.source"] is None   # ...and null, not silently dropped
    assert data["var.label"] == "Main"  # the None didn't wipe the sibling

    # And it reloads as None (present key wins over the default fallback).
    assert persister.load()["var.source"] is None


def test_write_does_not_raise_on_non_serializable(tmp_state_file, store, persister):
    """A non-serializable value in the store is caught and logged inside
    _write(), not raised out into shutdown or the debounced flush task.

    StateStore.set() now rejects non-primitives, so the bad value is injected
    straight into the backing dict — this still exercises the persister's own
    json.dumps try/except (defense-in-depth for any value that reaches the
    store by another path)."""
    store.set("var.ok", "fine", source="system")
    store._store["var.bad"] = {1, 2, 3}  # bypass the flat-primitive guard; a set is not JSON-serializable
    persister.start({"var.ok", "var.bad"})

    persister._write()  # must not raise (json.dumps is now inside the try)

    # The dump of the whole dict failed, so nothing was committed.
    assert not tmp_state_file.exists()


@pytest.mark.asyncio
async def test_flush_task_failure_is_surfaced(persister, caplog):
    """A failure inside the fire-and-forget flush task is logged via the done
    callback, not silently swallowed."""
    import logging

    async def boom():
        raise RuntimeError("kaboom")

    task = asyncio.create_task(boom())
    try:
        await task
    except RuntimeError:
        pass

    with caplog.at_level(logging.ERROR):
        persister._on_flush_done(task)  # retrieves + logs the exception

    assert "flush task failed" in caplog.text.lower()


@pytest.mark.asyncio
async def test_update_keys_flushes_pending_before_swap(tmp_state_file, store):
    """A reload (update_keys) that races a recent change flushes the pending
    write under the OLD key set before swapping, so the change isn't lost to a
    debounce that would otherwise fire against the new set."""
    persister = StatePersister(tmp_state_file, store)
    store.set("var.a", "old", source="system")
    persister.start({"var.a"})

    # A change schedules a 1s debounced flush that hasn't fired yet.
    store.set("var.a", "new", source="ui")
    assert persister._dirty
    assert not tmp_state_file.exists()

    # Reload swaps the watched set within the debounce window.
    persister.update_keys({"var.a"})

    # The pending change is already on disk, flushed before the swap.
    data = json.loads(tmp_state_file.read_text())
    assert data["var.a"] == "new"
    persister.stop()


def test_load_drops_non_primitive_values(tmp_state_file, store, persister):
    """load() keeps only flat primitives; nested objects/arrays are dropped so
    they can't violate the store's primitives-only invariant on the load path."""
    tmp_state_file.write_text(json.dumps({
        "var.ok": "x",
        "var.num": 7,
        "var.flag": True,
        "var.nothing": None,
        "var.obj": {"nested": 1},
        "var.list": [1, 2, 3],
    }))

    loaded = persister.load()

    assert loaded == {
        "var.ok": "x",
        "var.num": 7,
        "var.flag": True,
        "var.nothing": None,
    }
    assert "var.obj" not in loaded
    assert "var.list" not in loaded


def test_load_quarantines_corrupt_file(tmp_state_file, store, persister):
    """A corrupt state.json is moved aside (not silently overwritten) so it can
    be recovered, and load() starts fresh."""
    tmp_state_file.write_text("{ this is not valid json")

    assert persister.load() == {}

    quarantine = tmp_state_file.parent / "state.json.corrupt"
    assert quarantine.exists()
    assert not tmp_state_file.exists()


def test_load_quarantines_non_dict_file(tmp_state_file, store, persister):
    """A valid-JSON-but-non-dict state.json is also quarantined."""
    tmp_state_file.write_text(json.dumps([1, 2, 3]))

    assert persister.load() == {}

    quarantine = tmp_state_file.parent / "state.json.corrupt"
    assert quarantine.exists()
