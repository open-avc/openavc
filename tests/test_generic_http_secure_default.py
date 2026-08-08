"""The shipped generic_http driver must default to verifying TLS certificates.

generic_http is the platform's own no-code HTTP/REST driver. Its default was
once verify_ssl:false, which silently defeated TLS the moment a user enabled
HTTPS. It's now secure-by-default (the platform default everywhere else); a
self-signed device is handled by explicitly turning the toggle off, guided by
the tls_cert_untrusted offline reason. This guard keeps the default from
regressing back to insecure.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_DRIVER = (
    Path(__file__).resolve().parents[1]
    / "openavc" / "drivers" / "definitions" / "generic_http.avcdriver"
)


def _load() -> dict:
    return yaml.safe_load(_DRIVER.read_text(encoding="utf-8"))


def test_generic_http_verifies_tls_by_default():
    definition = _load()
    assert definition["default_config"]["verify_ssl"] is True
    assert definition["config_schema"]["verify_ssl"]["default"] is True
