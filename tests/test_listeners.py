"""Which listeners the server starts, on which ports, and what each one serves.

``main()`` binds the primary port and hands its sockets to one of three runs:
plain HTTP alone, plain HTTP beside the port-80 redirect (``_run_http``), or
HTTPS (``_run_tls``) beside the HTTP port's loopback-or-redirect listener and
the port-80 redirect. These pin what each run starts, which runs mark port 80
as served, what a listener that cannot bind says, and how the runs end.
uvicorn is stood in for throughout: nothing here binds a port.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from openavc import config, main, runtime_flags


@pytest.fixture
def uvicorn_fakes(monkeypatch):
    """uvicorn's Config and Server recorded; each serve() returns at once."""
    seen = SimpleNamespace(configs=[], served=[], ssl=True)

    class Config:
        def __init__(self, app, **kwargs):
            self.app, self.kwargs, self.ssl = app, kwargs, None
            seen.configs.append(self)

        def load(self):
            if seen.ssl and self.kwargs.get("ssl_certfile"):
                self.ssl = SimpleNamespace(sni_callback=None)

    class Server:
        def __init__(self, config):
            self.config = config

        async def serve(self, sockets=None):
            seen.served.append((self.config.kwargs["port"], sockets))

        def run(self, sockets=None):
            seen.served.append((self.config.kwargs["port"], sockets))

    monkeypatch.setattr(main.uvicorn, "Config", Config)
    monkeypatch.setattr(main.uvicorn, "Server", Server)
    return seen


@pytest.fixture
def runs(monkeypatch, tmp_path, uvicorn_fakes):
    """_run_tls / _run_http with the auxiliary listeners and TLS recorded."""
    seen = uvicorn_fakes
    seen.aux, seen.refused, seen.hardened, seen.cloud_loaded, seen.use_tls = [], set(), [], [], []

    def make_aux(app, port):
        seen.aux.append((app, port))
        if port in seen.refused:
            return None, f"port {port} taken"

        async def serve():
            seen.served.append((port, "aux"))

        return serve(), None

    monkeypatch.setattr(main, "_make_aux_redirect_server", make_aux)
    monkeypatch.setattr(main, "build_redirect_app", lambda port, scheme="https": ("redirect", port, scheme))
    monkeypatch.setattr(
        main, "build_http_listener_app", lambda app, redirect: ("loopback-or-redirect", app, redirect),
    )
    seen.cert, seen.key, seen.holder, seen.data_dir = tmp_path / "c.pem", tmp_path / "k.pem", object(), tmp_path
    monkeypatch.setattr("openavc.tls.load_or_generate", lambda cfg, data_dir: (seen.cert, seen.key))
    monkeypatch.setattr("openavc.tls.harden_tls_context", seen.hardened.append)
    monkeypatch.setattr("openavc.tls.load_cloud_cert", seen.cloud_loaded.append)
    monkeypatch.setattr("openavc.tls.cloud_cert_holder", lambda: seen.holder)
    monkeypatch.setattr("openavc.tls.make_sni_callback", lambda holder: ("sni", holder))
    monkeypatch.setattr(
        "openavc.listen_sockets.push_listeners.use_tls", lambda c, k: seen.use_tls.append((c, k)),
    )
    monkeypatch.setattr("openavc.system_config.get_system_config", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr("openavc.system_config.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_flags, "port80_active", False)
    for name, value in (
        ("TLS_PORT", 8443), ("HTTP_PORT", 8080), ("TLS_REDIRECT_HTTP", False),
        ("PORT80_REDIRECT", False), ("TLS_CLOUD_CERT", False),
    ):
        monkeypatch.setattr(config, name, value)
    return seen


def _app_listener_kwargs(port):
    return {
        "port": port, "reload": False, "log_level": "info",
        "ws_max_size": main._WS_MAX_SIZE, "forwarded_allow_ips": main._FORWARDED_ALLOW_IPS,
    }


# ===========================================================================
# HTTPS
# ===========================================================================


async def test_https_alone_serves_the_app_with_the_hardened_certificate(runs):
    await main._run_tls(["s1"])

    (listener,) = runs.configs
    assert listener.app == "openavc.main:app"
    assert listener.kwargs == {
        **_app_listener_kwargs(8443),
        "ssl_certfile": str(runs.cert), "ssl_keyfile": str(runs.key),
    }
    assert runs.hardened == [listener.ssl]
    assert listener.ssl.sni_callback == ("sni", runs.holder)
    assert runs.cloud_loaded == []
    assert runs.use_tls == [(str(runs.cert), str(runs.key))]
    assert runs.served == [(8443, ["s1"])]
    assert runs.aux == []
    assert runtime_flags.port80_active is False


async def test_a_cloud_certificate_is_loaded_when_one_is_on(runs, monkeypatch):
    monkeypatch.setattr(config, "TLS_CLOUD_CERT", True)
    await main._run_tls(["s1"])
    assert runs.cloud_loaded == [runs.data_dir]


async def test_with_no_context_built_nothing_is_hardened(runs, monkeypatch):
    monkeypatch.setattr(config, "TLS_CLOUD_CERT", True)
    runs.ssl = False
    await main._run_tls(["s1"])
    assert runs.hardened == [] and runs.cloud_loaded == []
    assert runs.served == [(8443, ["s1"])]


async def test_https_on_port_80_marks_port_80_served(runs, monkeypatch):
    monkeypatch.setattr(config, "TLS_PORT", 80)
    await main._run_tls(["s1"])
    assert runtime_flags.port80_active is True


@pytest.mark.parametrize("http_port, port80", [(8080, False), (80, True)])
async def test_the_http_port_serves_this_machine_and_redirects_the_rest(
    runs, monkeypatch, http_port, port80
):
    monkeypatch.setattr(config, "TLS_REDIRECT_HTTP", True)
    monkeypatch.setattr(config, "HTTP_PORT", http_port)
    await main._run_tls(["s1"])

    assert runs.aux == [
        (("loopback-or-redirect", main.app, ("redirect", 8443, "https")), http_port),
    ]
    assert sorted(map(str, runs.served)) == sorted(map(str, [(8443, ["s1"]), (http_port, "aux")]))
    assert runtime_flags.port80_active is port80


async def test_an_http_port_that_cannot_bind_is_a_warning(runs, monkeypatch, caplog):
    monkeypatch.setattr(config, "TLS_REDIRECT_HTTP", True)
    monkeypatch.setattr(config, "HTTP_PORT", 80)
    runs.refused.add(80)
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await main._run_tls(["s1"])

    assert [r.getMessage() for r in caplog.records] == [
        "HTTP redirect listener could not bind to port 80 (port 80 taken); "
        "old http:// links will not auto-redirect."
    ]
    assert runs.served == [(8443, ["s1"])]
    assert runtime_flags.port80_active is False


async def test_https_with_the_port_80_redirect(runs, monkeypatch):
    monkeypatch.setattr(config, "PORT80_REDIRECT", True)
    await main._run_tls(["s1"])
    assert runs.aux == [(("redirect", 8443, "https"), 80)]
    assert runtime_flags.port80_active is True


async def test_a_port_80_redirect_that_cannot_bind_is_a_warning(runs, monkeypatch, caplog):
    monkeypatch.setattr(config, "PORT80_REDIRECT", True)
    runs.refused.add(80)
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await main._run_tls(["s1"])

    assert [r.getMessage() for r in caplog.records] == [
        "Port-80 redirect listener could not bind (port 80 taken); typed URLs still need the port."
    ]
    assert runtime_flags.port80_active is False


async def test_no_port_80_redirect_when_https_already_listens_on_80(runs, monkeypatch):
    monkeypatch.setattr(config, "PORT80_REDIRECT", True)
    monkeypatch.setattr(config, "TLS_PORT", 80)
    await main._run_tls(["s1"])
    assert runs.aux == []


async def test_no_port_80_redirect_when_the_http_port_is_80(runs, monkeypatch):
    monkeypatch.setattr(config, "PORT80_REDIRECT", True)
    monkeypatch.setattr(config, "TLS_REDIRECT_HTTP", True)
    monkeypatch.setattr(config, "HTTP_PORT", 80)
    await main._run_tls(["s1"])
    assert [port for _app, port in runs.aux] == [80]
    assert runs.aux[0][0][0] == "loopback-or-redirect"


async def test_the_port_80_redirect_still_runs_when_the_http_port_is_not_redirected(
    runs, monkeypatch
):
    monkeypatch.setattr(config, "PORT80_REDIRECT", True)
    monkeypatch.setattr(config, "HTTP_PORT", 80)
    await main._run_tls(["s1"])
    assert runs.aux == [(("redirect", 8443, "https"), 80)]


async def test_a_certificate_that_cannot_be_used_stops_the_start(runs, monkeypatch, capsys):
    from openavc import tls

    error = tls.TLSConfigError("the key does not match the certificate")

    def refuse(cfg, data_dir):
        raise error

    monkeypatch.setattr("openavc.tls.load_or_generate", refuse)
    with pytest.raises(SystemExit) as stopped:
        await main._run_tls(["s1"])

    message = (
        "HTTPS is enabled but the TLS listener cannot start:\n"
        "  the key does not match the certificate\n\n"
        "Fix the certificate configuration in Settings > Security,\n"
        "or disable HTTPS (Settings > Security, or OPENAVC_TLS_ENABLED=false)."
    )
    assert stopped.value.code == 1
    assert stopped.value.__cause__ is error
    assert f"\n*** {message} ***\n" in capsys.readouterr().err
    recorded = json.loads((runs.data_dir / "status" / "startup-error.json").read_text(encoding="utf-8"))
    assert recorded["error"] == "tls_error" and recorded["message"] == message
    assert runs.configs == [] and runs.use_tls == []


# ===========================================================================
# plain HTTP beside the port-80 redirect
# ===========================================================================


async def test_http_beside_the_port_80_redirect(runs):
    await main._run_http(["s1"])

    (listener,) = runs.configs
    assert listener.app == "openavc.main:app"
    assert listener.kwargs == _app_listener_kwargs(8080)
    assert runs.aux == [(("redirect", 8080, "http"), 80)]
    assert sorted(map(str, runs.served)) == sorted(map(str, [(8080, ["s1"]), (80, "aux")]))
    assert runtime_flags.port80_active is True


async def test_http_already_on_port_80_needs_no_redirect(runs, monkeypatch):
    monkeypatch.setattr(config, "HTTP_PORT", 80)
    await main._run_http(["s1"])
    assert runs.aux == []
    assert runtime_flags.port80_active is True


async def test_an_http_port_80_redirect_that_cannot_bind_is_a_warning(runs, caplog):
    runs.refused.add(80)
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await main._run_http(["s1"])

    assert [r.getMessage() for r in caplog.records] == [
        "Port-80 redirect listener could not bind (port 80 taken); typed URLs still need the port."
    ]
    assert runtime_flags.port80_active is False


# ===========================================================================
# an auxiliary listener, and how the runs end
# ===========================================================================


@pytest.fixture
def aux(monkeypatch, uvicorn_fakes):
    from openavc import listen_sockets

    seen = uvicorn_fakes
    seen.bound, seen.quieted, seen.bind_error = [], [], None
    plan = object()
    seen.plan = plan

    def bind(plan_, port, **kwargs):
        seen.bound.append((plan_, port, kwargs))
        if seen.bind_error is not None:
            raise seen.bind_error
        return ["aux-sock"], plan_

    def quiet(server):
        seen.quieted.append(server)
        return server

    monkeypatch.setattr(config, "listen_plan", lambda: plan)
    monkeypatch.setattr(listen_sockets, "bind_for_plan", bind)
    monkeypatch.setattr(listen_sockets, "quiet_server", quiet)
    return seen


async def test_an_auxiliary_listener_binds_the_plan_quietly(aux):
    serve, err = main._make_aux_redirect_server("redirect-app", 80)

    assert err is None
    assert aux.bound == [(aux.plan, 80, {"fallback": False})]
    (cfg,) = aux.configs
    assert cfg.app == "redirect-app"
    assert cfg.kwargs == {
        "port": 80, "log_level": "warning",
        "ws_max_size": main._WS_MAX_SIZE, "forwarded_allow_ips": main._FORWARDED_ALLOW_IPS,
    }
    assert [s.config for s in aux.quieted] == [cfg]
    await serve
    assert aux.served == [(80, ["aux-sock"])]


def test_an_auxiliary_listener_that_cannot_bind_returns_the_reason(aux):
    from openavc import listen_sockets

    reason = OSError(98, "Address already in use")
    aux.bind_error = listen_sockets.ListenBindError("in_use", "0.0.0.0", 80, reason)

    assert main._make_aux_redirect_server("redirect-app", 80) == (None, reason)
    assert aux.configs == []


async def test_the_first_listener_to_stop_stops_the_rest():
    stopped = asyncio.Event()

    async def ends():
        return None

    async def runs_forever():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise

    other = asyncio.create_task(runs_forever())
    await main._serve_until_first_exit([asyncio.create_task(ends()), other])
    assert stopped.is_set() and other.cancelled()


async def test_a_listener_that_fails_raises_after_the_rest_are_stopped():
    async def fails():
        raise OSError("listener died")

    other = asyncio.create_task(asyncio.Event().wait())
    with pytest.raises(OSError, match="listener died"):
        await main._serve_until_first_exit([asyncio.create_task(fails()), other])
    assert other.cancelled()


# ===========================================================================
# main(): which run serves the bound sockets
# ===========================================================================


@pytest.fixture
def started(monkeypatch, uvicorn_fakes, tmp_path):
    from openavc import listen, listen_safety_net, listen_sockets

    seen = uvicorn_fakes
    seen.calls, seen.bind_error, seen.binds = [], None, []
    plan = SimpleNamespace(problem=None, choice="any")
    seen.plan = plan

    def bind(plan_, port, **kwargs):
        seen.binds.append((port, kwargs))
        if seen.bind_error is not None:
            raise seen.bind_error
        return ["sock"], plan_

    async def tls(sockets):
        seen.calls.append(("tls", sockets))

    async def http(sockets):
        seen.calls.append(("http", sockets))

    monkeypatch.setattr(listen, "start", lambda: plan)
    monkeypatch.setattr(listen, "set_current", lambda p: seen.calls.append(("set_current", p)))
    monkeypatch.setattr(listen_sockets, "bind_for_plan", bind)
    monkeypatch.setattr(listen_sockets, "close_all", lambda s: seen.calls.append(("close", s)))
    monkeypatch.setattr(listen_sockets.push_listeners, "enable", lambda port: seen.calls.append(("push", port)))
    monkeypatch.setattr(listen_safety_net, "note_listening", lambda d, choice: seen.calls.append(("noted", choice)))
    monkeypatch.setattr("openavc.system_config.get_system_config", lambda: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr("openavc.system_config.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "_clear_startup_error", lambda: seen.calls.append(("cleared",)))
    monkeypatch.setattr(main, "_write_server_address", lambda: seen.calls.append(("address",)))
    monkeypatch.setattr(main, "_run_tls", tls)
    monkeypatch.setattr(main, "_run_http", http)
    monkeypatch.setattr(runtime_flags, "port80_active", False)
    monkeypatch.delenv("OPENAVC_RESTARTING", raising=False)
    monkeypatch.delenv("OPENAVC_LISTEN_FD", raising=False)
    for name, value in (
        ("TLS_ENABLED", False), ("PORT80_REDIRECT", False), ("HTTP_PORT", 8080), ("TLS_PORT", 8443),
    ):
        monkeypatch.setattr(config, name, value)
    seen.data_dir = tmp_path
    return seen


def test_plain_http_serves_alone_on_the_bound_sockets(started):
    main.main()

    assert started.binds == [(8080, {"retries": 1})]
    assert started.calls == [
        ("set_current", started.plan), ("noted", "any"), ("cleared",), ("address",),
        ("push", 8080), ("close", ["sock"]),
    ]
    (cfg,) = started.configs
    assert cfg.app == "openavc.main:app" and cfg.kwargs == _app_listener_kwargs(8080)
    assert started.served == [(8080, ["sock"])]
    assert runtime_flags.port80_active is False


def test_plain_http_on_port_80_marks_port_80_served(started, monkeypatch):
    monkeypatch.setattr(config, "HTTP_PORT", 80)
    main.main()
    assert runtime_flags.port80_active is True


def test_https_binds_its_own_port_and_runs_the_tls_listeners(started, monkeypatch):
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "PORT80_REDIRECT", True)
    main.main()

    assert started.binds == [(8443, {"retries": 1})]
    assert ("tls", ["sock"]) in started.calls and ("push", 8080) in started.calls
    assert not any(call[0] == "http" for call in started.calls)
    assert started.calls[-1] == ("close", ["sock"])


def test_the_port_80_redirect_runs_beside_plain_http(started, monkeypatch):
    monkeypatch.setattr(config, "PORT80_REDIRECT", True)
    main.main()
    assert ("http", ["sock"]) in started.calls
    assert started.served == []


def test_a_restart_retries_the_bind_and_then_forgets_it_is_one(started, monkeypatch):
    import os

    monkeypatch.setenv("OPENAVC_RESTARTING", "1")
    main.main()
    assert started.binds == [(8080, {"retries": 20})]
    assert "OPENAVC_RESTARTING" not in os.environ


def test_a_missing_adapter_is_logged_once_listening(started, caplog):
    started.plan.problem = SimpleNamespace(message="The chosen adapter Ethernet 2 is not connected.")
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        main.main()
    assert "The chosen adapter Ethernet 2 is not connected." in [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("tls, label, variable, port", [
    (False, "HTTP port", "OPENAVC_PORT", 8080),
    (True, "HTTPS port", "OPENAVC_TLS_PORT", 8443),
])
def test_a_port_that_cannot_be_bound_stops_the_start_and_says_why(
    started, monkeypatch, capsys, tls, label, variable, port
):
    from openavc import listen_sockets

    monkeypatch.setattr(config, "TLS_ENABLED", tls)
    started.bind_error = listen_sockets.ListenBindError("in_use", "0.0.0.0", port, OSError(98, "in use"))
    expected = started.bind_error.message(label, variable)

    with pytest.raises(SystemExit) as stopped:
        main.main()

    assert stopped.value.code == 1
    assert f"\n*** {expected} ***\n" in capsys.readouterr().out
    recorded = json.loads((started.data_dir / "status" / "startup-error.json").read_text(encoding="utf-8"))
    assert recorded["error"] == started.bind_error.error_type
    assert recorded["message"] == expected
    assert started.calls == []


def test_a_supervisor_listener_is_adopted_for_the_same_plan(started, monkeypatch):
    from openavc import listen_sockets

    adopted = []

    def inherited(value, plan, port):
        adopted.append((value, plan, port))
        return ["inherited-sock"]

    monkeypatch.setattr(listen_sockets, "inherited_for_plan", inherited)
    monkeypatch.setenv("OPENAVC_LISTEN_FD", "7")
    main.main()

    assert adopted == [("7", started.plan, 8080)]
    assert started.binds == []
    assert started.calls[0] == ("set_current", started.plan)
    assert started.served == [(8080, ["inherited-sock"])]
    assert started.calls[-1] == ("close", ["inherited-sock"])
