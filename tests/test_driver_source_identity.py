"""Loaded source receipts survive file edits but never transfer to other code."""
import hashlib
import os
from pathlib import Path
import py_compile
import sys
from types import MethodType

import pytest

from openavc.drivers import driver_loader as loader
from openavc.drivers.registry import get_driver_class, register_driver, unregister_driver


def source(value):
    return f'''from openavc.drivers.base import BaseDriver
class AcmeSnapshot(BaseDriver):
    DRIVER_INFO = {{"id": "acme_snapshot", "name": "Acme Snapshot", "version": "1.0"}}
    async def send_command(self, command, params=None):
        return None
    async def run_setup_action(self, action, params, progress):
        return "{value}"
'''.encode()


@pytest.fixture
def path(tmp_path):
    file = tmp_path / "acme_snapshot.py"
    yield file
    unregister_driver("acme_snapshot")
    sys.modules.pop("openavc_driver_acme_snapshot", None)


async def value(cls):
    return await cls.run_setup_action(None, "check", {}, None)


async def test_timestamp_valid_bytecode_cannot_supply_a_different_source(path):
    old, new = source("old"), source("new")
    path.write_bytes(old)
    stat = path.stat()
    py_compile.compile(str(path), doraise=True)
    path.write_bytes(new)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert len(old) == len(new)
    cls = loader.load_python_driver_file(path)
    assert await value(cls) == "new"
    assert loader.loaded_python_source(cls).sha256 == hashlib.sha256(new).hexdigest()


async def test_receipt_describes_loaded_code_after_the_file_changes(path):
    old = source("old")
    path.write_bytes(old)
    cls = loader.load_python_driver_file(path)
    receipt = loader.loaded_python_source(cls)
    path.write_bytes(source("new"))
    assert await value(cls) == "old"
    assert loader.loaded_python_source(cls) is receipt
    assert receipt.sha256 == hashlib.sha256(old).hexdigest()


async def test_reload_validation_and_activation_execute_one_captured_snapshot(path, monkeypatch):
    path.write_bytes(source("old"))
    old = loader.load_python_driver_file(path)
    register_driver(old)
    new = source("new")
    path.write_bytes(new)
    original = Path.read_bytes
    reads = []

    def edit_after_capture(file):
        captured = original(file)
        if file == path:
            reads.append(captured)
            path.write_bytes(source("bad"))
        return captured

    monkeypatch.setattr(Path, "read_bytes", edit_after_capture)
    assert loader.reload_python_driver(path)["status"] == "reloaded"
    cls = get_driver_class("acme_snapshot")
    assert reads == [new]
    assert await value(cls) == "new"
    assert loader.loaded_python_source(cls).sha256 == hashlib.sha256(new).hexdigest()
    assert await value(old) == "old"


async def test_failed_reload_preserves_old_registration_module_and_receipt(path):
    path.write_bytes(source("old"))
    cls = loader.load_python_driver_file(path)
    register_driver(cls)
    receipt = loader.loaded_python_source(cls)
    module = sys.modules[cls.__module__]
    path.write_bytes(b"this is not valid python !")
    result = loader.reload_python_driver(path)
    assert result["status"] == "error" and result["old_driver_preserved"] is True
    assert get_driver_class("acme_snapshot") is cls
    assert sys.modules[cls.__module__] is module
    assert loader.loaded_python_source(cls) is receipt
    assert await value(cls) == "old"


def test_only_unchanged_loaded_class_and_handler_can_use_the_receipt(path, monkeypatch):
    path.write_bytes(source("old"))
    cls = loader.load_python_driver_file(path)
    instance = object.__new__(cls)
    assert loader.loaded_python_source(instance) is not None
    fake = type("MetadataCopy", (), {"DRIVER_INFO": cls.DRIVER_INFO,
                                     "run_setup_action": cls.run_setup_action})
    assert loader.loaded_python_source(fake) is None

    async def changed(self, action, params, progress):
        return "changed"

    instance.run_setup_action = MethodType(changed, instance)
    assert loader.loaded_python_source(instance) is None
    assert loader.loaded_python_source(cls) is not None
    monkeypatch.setitem(cls.DRIVER_INFO, "version", "2.0")
    assert loader.loaded_python_source(cls) is None


@pytest.mark.parametrize("mutation", ["code", "defaults", "kwdefaults"])
def test_in_place_executable_mutation_invalidates_source_identity(path, mutation):
    path.write_bytes(source("old"))
    cls = loader.load_python_driver_file(path)
    handler = cls.run_setup_action
    assert loader.loaded_python_source(cls) is not None

    async def changed(self, action, params, progress):
        return "changed"

    if mutation == "code":
        handler.__code__ = changed.__code__
    elif mutation == "defaults":
        handler.__defaults__ = (None,)
    else:
        handler.__kwdefaults__ = {"extra": None}
    assert loader.loaded_python_source(cls) is None


def test_mutable_defaults_do_not_receive_primary_source_qualification(path):
    path.write_bytes(source("old").replace(b"params, progress):", b"params, progress=[]):"))
    cls = loader.load_python_driver_file(path)
    assert cls is not None
    assert loader.loaded_python_source(cls) is None


def test_compiler_class_cell_preserves_zero_argument_super_but_not_changed_identity(path):
    path.write_bytes(source("old").replace(b'return "old"', b'return super().run_setup_action(action, params, progress)'))
    cls = loader.load_python_driver_file(path)
    handler = cls.run_setup_action
    assert handler.__code__.co_freevars == ("__class__",)
    assert loader.loaded_python_source(cls) is not None
    handler.__closure__[0].cell_contents = type("Other", (), {})
    assert loader.loaded_python_source(cls) is None
    handler.__closure__[0].cell_contents = cls
    assert loader.loaded_python_source(cls) is not None
    del handler.__closure__[0].cell_contents
    assert loader.loaded_python_source(cls) is None


def test_general_closure_is_not_qualified_by_the_class_cell_exception(path):
    path.write_bytes(b'''from openavc.drivers.base import BaseDriver
def make_driver():
    mutable = []
    class AcmeClosure(BaseDriver):
        DRIVER_INFO = {"id": "acme_snapshot", "name": "Acme", "version": "1.0"}
        async def send_command(self, command, params=None):
            return None
        async def run_setup_action(self, action, params, progress):
            mutable.append(action)
            return super().run_setup_action(action, params, progress)
    return AcmeClosure
AcmeClosure = make_driver()
''')
    cls = loader.load_python_driver_file(path)
    assert cls is not None
    assert loader.loaded_python_source(cls) is None


def test_changed_inheritance_cannot_preserve_super_dispatch_receipt(path):
    path.write_bytes(source("old").replace(b'return "old"', b'return super().run_setup_action(action, params, progress)'))
    cls = loader.load_python_driver_file(path)
    assert loader.loaded_python_source(cls) is not None
    original_base = cls.__bases__[0]
    replacement = type("OtherParent", (original_base,), {})
    cls.__bases__ = (replacement,)
    assert loader.loaded_python_source(cls) is None


def test_no_setup_declaration_receipt_never_grants_setup_handler_authority(path):
    raw = source("unused").split(b"    async def run_setup_action", 1)[0]
    path.write_bytes(raw)
    cls = loader.load_python_driver_file(path)
    receipt = loader.loaded_python_declaration(cls)
    assert receipt.sha256 == hashlib.sha256(raw).hexdigest()
    assert receipt.setup_handler is False
    assert loader.loaded_python_source(cls) is None
    assert loader.loaded_python_declaration(object.__new__(cls)) is None
    cls.DRIVER_INFO["actions"] = [{"id": "pair", "kind": "setup", "result_contract": "setup/v1"}]
    assert loader.loaded_python_declaration(cls) is None
    assert loader.loaded_python_source(cls) is None


def test_inherited_stub_with_declared_setup_cannot_receive_declaration_coverage(path):
    raw = source("unused").split(b"    async def run_setup_action", 1)[0]
    raw = raw.replace(b'"version": "1.0"', b'"version": "1.0", "actions": [{"id": "pair", "kind": "setup", "result_contract": "setup/v1"}]')
    path.write_bytes(raw)
    cls = loader.load_python_driver_file(path)
    assert cls is not None
    assert loader.loaded_python_declaration(cls) is None
    assert loader.loaded_python_source(cls) is None
