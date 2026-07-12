"""The runtime must honor the configured file-logging settings.

``logging.file_enabled`` / ``max_size_mb`` / ``max_files`` are saved and shown
in the UI; they used to be ignored (the handler hardcoded 10 MB / 3 files and
was always added), a backend/frontend parity violation. These tests pin that
the settings drive the actual rotating file handler.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from server.utils import logger as lg


def _patch_cfg(monkeypatch, tmp_path, settings: dict) -> None:
    class _Cfg:
        def get(self, section, key, default=None):
            return settings.get(key, default)

    monkeypatch.setattr("server.system_config.get_system_config", lambda: _Cfg())
    monkeypatch.setattr("server.system_config.get_log_dir", lambda: tmp_path)


_FMT = logging.Formatter("%(message)s")


def test_build_file_handler_disabled_returns_none(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, tmp_path, {"file_enabled": False})
    assert lg._build_file_handler(_FMT) is None


def test_build_file_handler_honors_size_and_count(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, tmp_path, {
        "file_enabled": True, "max_size_mb": 25, "max_files": 7,
    })
    h = lg._build_file_handler(_FMT)
    try:
        assert isinstance(h, RotatingFileHandler)
        assert h.maxBytes == 25 * 1024 * 1024
        assert h.backupCount == 7
    finally:
        h.close()


def test_build_file_handler_bad_values_fall_back_to_defaults(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, tmp_path, {
        "file_enabled": True, "max_size_mb": "oops", "max_files": None,
    })
    h = lg._build_file_handler(_FMT)
    try:
        assert h.maxBytes == 50 * 1024 * 1024  # default
        assert h.backupCount == 5               # default
    finally:
        h.close()


def test_build_file_handler_non_positive_size_uses_default(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, tmp_path, {
        "file_enabled": True, "max_size_mb": 0, "max_files": -2,
    })
    h = lg._build_file_handler(_FMT)
    try:
        assert h.maxBytes == 50 * 1024 * 1024
        assert h.backupCount == 5
    finally:
        h.close()


def test_set_file_logging_hot_applies_and_disables(monkeypatch, tmp_path):
    """set_file_logging swaps the live handler: a custom size takes effect, and
    disabling removes the file handler entirely — no restart required."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_fh = lg._file_handler
    try:
        _patch_cfg(monkeypatch, tmp_path, {
            "file_enabled": True, "max_size_mb": 12, "max_files": 4,
        })
        lg.set_file_logging()
        assert isinstance(lg._file_handler, RotatingFileHandler)
        assert lg._file_handler.maxBytes == 12 * 1024 * 1024
        assert lg._file_handler.backupCount == 4
        assert lg._file_handler in root.handlers

        _patch_cfg(monkeypatch, tmp_path, {"file_enabled": False})
        lg.set_file_logging()
        assert lg._file_handler is None
        assert not any(isinstance(h, RotatingFileHandler) for h in root.handlers)
    finally:
        # Restore the root logger to exactly the handler set we found it with.
        for h in root.handlers[:]:
            if h not in saved_handlers:
                root.removeHandler(h)
        for h in saved_handlers:
            if h not in root.handlers:
                root.addHandler(h)
        lg._file_handler = saved_fh
