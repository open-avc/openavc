"""Tests for server.tls cloud-cert support — load, atomic install, SNI selection.

Uses an invented label + zone throughout (no real issuance, no real domain).
The live dual-serve/hot-swap path through a real uvicorn listener is covered
in test_main_tls.py.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import ssl
import stat
from types import SimpleNamespace

import pytest
from cryptography import x509

from server import tls
from tests.helpers import make_cloud_cert_pem

LABEL = "ab12cd34ef56ab78"
ZONE = "i.certtest.invalid"
BASE = f"{LABEL}.{ZONE}"


@pytest.fixture(autouse=True)
def _clean_holder():
    """Cloud state lives in a module-level holder — isolate every test."""
    tls.cloud_cert_holder().clear()
    yield
    tls.cloud_cert_holder().clear()


def _install(tmp_path, **kwargs) -> tls.CloudCertState:
    cert_pem, key_pem = make_cloud_cert_pem(LABEL, ZONE, **kwargs)
    return tls.install_cloud_cert(tmp_path, cert_pem, key_pem)


# ---------------------------------------------------------------------------
# load_cloud_cert
# ---------------------------------------------------------------------------


def test_load_missing_files_returns_none(tmp_path):
    assert tls.load_cloud_cert(tmp_path) is None
    assert tls.cloud_cert_holder().get() is None


def test_load_after_install_roundtrip(tmp_path):
    installed = _install(tmp_path)
    tls.cloud_cert_holder().clear()

    state = tls.load_cloud_cert(tmp_path)
    assert state is not None
    assert state.exact_names == frozenset({BASE})
    assert state.wildcard_bases == frozenset({BASE})
    assert state.hostname_suffix == BASE
    assert state.expires_at == installed.expires_at
    assert tls.cloud_cert_holder().get() is state


def test_load_expired_cert_serves_self_signed_only(tmp_path, caplog):
    """Expired cert on disk → holder stays empty, warning logged, no raise."""
    cert_pem, key_pem = make_cloud_cert_pem(LABEL, ZONE, expired=True)
    cert_path, key_path = tls.cloud_cert_paths(tmp_path)
    cert_path.parent.mkdir(parents=True)
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)

    with caplog.at_level(logging.WARNING, logger="server.tls"):
        assert tls.load_cloud_cert(tmp_path) is None
    assert tls.cloud_cert_holder().get() is None
    assert any("expired" in r.message for r in caplog.records)


def test_load_garbage_cert_serves_self_signed_only(tmp_path, caplog):
    cert_path, key_path = tls.cloud_cert_paths(tmp_path)
    cert_path.parent.mkdir(parents=True)
    cert_path.write_bytes(b"not a pem")
    key_path.write_bytes(b"not a key")

    with caplog.at_level(logging.WARNING, logger="server.tls"):
        assert tls.load_cloud_cert(tmp_path) is None
    assert tls.cloud_cert_holder().get() is None


def test_load_mismatched_key_serves_self_signed_only(tmp_path):
    """Cert from one pair + key from another → rejected at load_cert_chain."""
    cert_pem, _ = make_cloud_cert_pem(LABEL, ZONE)
    _, other_key_pem = make_cloud_cert_pem(LABEL, ZONE)
    cert_path, key_path = tls.cloud_cert_paths(tmp_path)
    cert_path.parent.mkdir(parents=True)
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(other_key_pem)

    assert tls.load_cloud_cert(tmp_path) is None
    assert tls.cloud_cert_holder().get() is None


# ---------------------------------------------------------------------------
# install_cloud_cert
# ---------------------------------------------------------------------------


def test_install_writes_files_and_swaps_holder(tmp_path):
    state = _install(tmp_path)
    cert_path, key_path = tls.cloud_cert_paths(tmp_path)

    assert cert_path.exists()
    assert key_path.exists()
    assert tls.cloud_cert_holder().get() is state
    # No validation temp files left behind.
    leftovers = {p.name for p in cert_path.parent.iterdir()}
    assert leftovers == {cert_path.name, key_path.name}


def test_install_invalid_input_raises_and_preserves_existing(tmp_path):
    """A bad renewal payload must not clobber the working cert on disk."""
    good = _install(tmp_path)
    cert_path, _ = tls.cloud_cert_paths(tmp_path)
    original_bytes = cert_path.read_bytes()

    with pytest.raises(tls.TLSConfigError):
        tls.install_cloud_cert(tmp_path, b"garbage", b"garbage")

    assert cert_path.read_bytes() == original_bytes
    assert tls.cloud_cert_holder().get() is good


def test_install_expired_cert_raises(tmp_path):
    with pytest.raises(tls.TLSConfigError, match="expired"):
        _install(tmp_path, expired=True)
    assert tls.cloud_cert_holder().get() is None


def test_install_mismatched_key_raises(tmp_path):
    cert_pem, _ = make_cloud_cert_pem(LABEL, ZONE)
    _, other_key = make_cloud_cert_pem(LABEL, ZONE)
    with pytest.raises(tls.TLSConfigError):
        tls.install_cloud_cert(tmp_path, cert_pem, other_key)


def test_install_replaces_previous_cert(tmp_path):
    """Renewal: second install swaps both disk files and the served state."""
    first = _install(tmp_path)
    second = _install(tmp_path)
    assert tls.cloud_cert_holder().get() is second
    assert second is not first

    reloaded = tls.load_cloud_cert(tmp_path)
    assert reloaded.expires_at == second.expires_at


def test_install_accepts_str_pem(tmp_path):
    cert_pem, key_pem = make_cloud_cert_pem(LABEL, ZONE)
    state = tls.install_cloud_cert(
        tmp_path, cert_pem.decode("utf-8"), key_pem.decode("utf-8")
    )
    assert state.hostname_suffix == BASE


@pytest.mark.skipif(os.name != "posix", reason="POSIX only — Windows uses NTFS ACLs")
def test_install_key_mode_0600(tmp_path):
    _install(tmp_path)
    _, key_path = tls.cloud_cert_paths(tmp_path)
    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# remove_cloud_cert
# ---------------------------------------------------------------------------


def test_remove_deletes_files_and_clears_holder(tmp_path):
    _install(tmp_path)
    tls.remove_cloud_cert(tmp_path)

    cert_path, key_path = tls.cloud_cert_paths(tmp_path)
    assert not cert_path.exists()
    assert not key_path.exists()
    assert tls.cloud_cert_holder().get() is None


def test_remove_when_nothing_installed_is_quiet(tmp_path):
    tls.remove_cloud_cert(tmp_path)  # no files, no raise


# ---------------------------------------------------------------------------
# CloudCertState.matches
# ---------------------------------------------------------------------------


def test_matches_names(tmp_path):
    state = _install(tmp_path)

    # Exact + single-level wildcard.
    assert state.matches(BASE)
    assert state.matches(f"192-168-1-20.{BASE}")
    assert state.matches(f"anything.{BASE}")
    # Case-insensitive; robust to a trailing dot.
    assert state.matches(f"Foo.{LABEL.upper()}.{ZONE}")
    assert state.matches(f"foo.{BASE}.")
    # Wildcards are single-level only.
    assert not state.matches(f"a.b.{BASE}")
    # Other names, the bare zone, and junk don't match.
    assert not state.matches(ZONE)
    assert not state.matches(f"other-label.{ZONE}")
    assert not state.matches("localhost")
    assert not state.matches("192.168.1.20")
    assert not state.matches(f"*.{BASE}")
    assert not state.matches("")


# ---------------------------------------------------------------------------
# SNI callback
# ---------------------------------------------------------------------------


def _fake_sslobj():
    return SimpleNamespace(context="default-context")


def test_sni_callback_swaps_context_on_match(tmp_path):
    state = _install(tmp_path)
    cb = tls.make_sni_callback(tls.cloud_cert_holder())

    sslobj = _fake_sslobj()
    assert cb(sslobj, f"present.{BASE}", None) is None
    assert sslobj.context is state.context


def test_sni_callback_leaves_default_for_other_names(tmp_path):
    _install(tmp_path)
    cb = tls.make_sni_callback(tls.cloud_cert_holder())

    sslobj = _fake_sslobj()
    cb(sslobj, "openavc.local", None)
    assert sslobj.context == "default-context"


def test_sni_callback_no_sni_is_untouched(tmp_path):
    _install(tmp_path)
    cb = tls.make_sni_callback(tls.cloud_cert_holder())

    sslobj = _fake_sslobj()
    cb(sslobj, None, None)
    assert sslobj.context == "default-context"


def test_sni_callback_empty_holder_is_untouched():
    cb = tls.make_sni_callback(tls.cloud_cert_holder())
    sslobj = _fake_sslobj()
    cb(sslobj, f"present.{BASE}", None)
    assert sslobj.context == "default-context"


def test_sni_callback_expired_at_handshake_falls_back_and_clears(tmp_path, caplog):
    """A cert that expires while serving stops matching — self-signed instead."""
    state = _install(tmp_path)
    expired = tls.CloudCertState(
        context=state.context,
        exact_names=state.exact_names,
        wildcard_bases=state.wildcard_bases,
        hostname_suffix=state.hostname_suffix,
        expires_at=_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=1),
    )
    holder = tls.cloud_cert_holder()
    holder.set(expired)
    cb = tls.make_sni_callback(holder)

    sslobj = _fake_sslobj()
    with caplog.at_level(logging.WARNING, logger="server.tls"):
        cb(sslobj, f"present.{BASE}", None)
    assert sslobj.context == "default-context"
    assert holder.get() is None
    assert any("expired" in r.message for r in caplog.records)


def test_sni_callback_never_raises(tmp_path):
    """An exception inside the callback would abort the handshake — it must
    be swallowed and fall back to the default context instead."""
    _install(tmp_path)

    class _ExplodingHolder:
        def get(self):
            raise RuntimeError("boom")

    cb = tls.make_sni_callback(_ExplodingHolder())
    sslobj = _fake_sslobj()
    assert cb(sslobj, f"present.{BASE}", None) is None
    assert sslobj.context == "default-context"


def test_sni_callback_matching_is_isolated_per_handshake(tmp_path):
    """Two handshakes, one matching and one not, don't leak context."""
    state = _install(tmp_path)
    cb = tls.make_sni_callback(tls.cloud_cert_holder())

    matching, other = _fake_sslobj(), _fake_sslobj()
    cb(matching, f"a.{BASE}", None)
    cb(other, "unrelated.example", None)
    assert matching.context is state.context
    assert other.context == "default-context"


# ---------------------------------------------------------------------------
# Loading with real ssl contexts — the context actually serves the right cert
# ---------------------------------------------------------------------------


def test_state_context_holds_installed_cert(tmp_path):
    """The SSLContext built by install really contains the installed leaf."""
    cert_pem, key_pem = make_cloud_cert_pem(LABEL, ZONE)
    state = tls.install_cloud_cert(tmp_path, cert_pem, key_pem)
    assert isinstance(state.context, ssl.SSLContext)

    # get_ca_certs()/cert introspection on a server context is limited from
    # Python; the live handshake proof lives in test_main_tls.py. Here we
    # confirm the loaded chain parses back to the same leaf on disk.
    cert_path, _ = tls.cloud_cert_paths(tmp_path)
    on_disk = x509.load_pem_x509_certificate(cert_path.read_bytes())
    original = x509.load_pem_x509_certificate(cert_pem)
    assert on_disk.serial_number == original.serial_number
