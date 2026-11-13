"""Pins for how the community install and update fetch a driver's files from
the catalog: the main file, a YAML driver's declared companion and a Python
driver's conventional companions.

Each download is pinned for the client it opens, how each way it can fail is
answered (a refused status, a network failure, a refused certificate, a file
that cannot be written), which names and addresses it refuses before asking,
and how the catalog's published hashes are read and checked, the refetch
included. The three downloads fail differently on purpose or by history; these
record each as it is today, so moving them onto shared code cannot quietly
make one answer like another.
"""

from __future__ import annotations

import hashlib
import logging
import ssl
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import yaml
from fastapi import HTTPException

from openavc.api.models import CommunityDriverInstallRequest
from openavc.api.routes import drivers as routes
from openavc.api.routes.drivers import install_community_driver, update_driver
from openavc.utils.outbound import internet_failure

CATALOG = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/"
DRIVER_ID = "acme_widget"
NOT_YET = (
    "This driver was updated a few minutes ago and cannot be installed yet. "
    "Try again in five minutes."
)
CERT_REFUSED = "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"


class _Response:
    def __init__(self, url: str, status: int, body: bytes):
        self.url = url
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", "replace")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "refused",
                request=httpx.Request("GET", self.url),
                response=httpx.Response(self.status_code),
            )


class _Network:
    """The catalog as the downloads reach it. Every client built is recorded
    with its arguments, every GET with the client it went through."""

    def __init__(self, events: list):
        self.pages: dict[str, object] = {}
        self.clients: list[dict] = []
        self._events = events

    def __call__(self, **kwargs):  # httpx.AsyncClient(**kwargs)
        self.clients.append(kwargs)
        return _Client(self, len(self.clients) - 1)


class _Client:
    def __init__(self, network: _Network, index: int):
        self._network = network
        self._index = index

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self._network._events.append(("closed", self._index))
        return False

    async def get(self, url: str):
        self._network._events.append(("get", self._index, url.rsplit("/", 1)[-1]))
        page = self._network.pages.get(url, 404)
        if isinstance(page, BaseException):
            raise page
        if isinstance(page, int):
            return _Response(url, page, b"")
        return _Response(url, 200, page)


class _Index:
    """The community index the hash lookup reads, answered per ``force``."""

    def __init__(self):
        self.entries: list = []
        self.refetched: list | None = None
        self.forces: list[bool] = []
        self.failure_sentence = ""

    async def get_drivers(self, cache, force: bool = False):
        self.forces.append(force)
        cache.failure_sentence = self.failure_sentence
        if force and self.refetched is not None:
            return self.refetched
        return self.entries


@pytest.fixture
def cat(tmp_path, monkeypatch):
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    events: list = []
    network = _Network(events)
    monkeypatch.setattr("httpx.AsyncClient", network)
    index = _Index()

    async def _get_drivers(self, force=False):
        return await index.get_drivers(self, force)

    monkeypatch.setattr(
        "openavc.discovery.community_index.CommunityIndexCache.get_drivers", _get_drivers
    )
    monkeypatch.setattr(routes, "_get_driver_repo_dir", lambda: repo)
    monkeypatch.setattr("openavc.system_config.DRIVER_REPO_DIR", repo)
    monkeypatch.setattr(
        routes, "register_driver", lambda cls: events.append(("register", cls.DRIVER_INFO.get("id")))
    )
    monkeypatch.setattr(
        routes, "unregister_driver", lambda driver_id: events.append(("unregister", driver_id))
    )

    async def _refresh():
        events.append(("refresh",))

    monkeypatch.setattr("openavc.api.discovery.refresh_all_device_matches", _refresh)
    engine = MagicMock()
    engine.project = None

    async def _retry():
        return []

    async def _reload(driver_id):
        return []

    engine.devices.retry_all_orphans = _retry
    engine.devices.reload_driver = _reload
    engine.devices.get_devices_using_driver = lambda driver_id: []
    monkeypatch.setattr(routes, "_get_engine", lambda: engine)

    async def _announce(driver_id=None):
        events.append(("announce", driver_id))

    monkeypatch.setattr(routes, "announce_drivers_changed", _announce)
    yield SimpleNamespace(repo=repo, events=events, network=network, index=index)
    for name in [n for n in sys.modules if n.startswith("openavc_driver_")]:
        if "acme" in name:
            sys.modules.pop(name, None)


def _yaml(*, companion: str | None = None) -> bytes:
    body: dict = {
        "id": DRIVER_ID,
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "commands": {"power_on": {"label": "On", "send": "P\r", "params": {}}},
    }
    if companion is not None:
        body["discovery"] = {"python": {"file": companion}}
    return yaml.safe_dump(body, sort_keys=False).encode()


def _python(mark: str = "new") -> bytes:
    return (
        "from openavc.drivers.base import BaseDriver\n\n\n"
        "class AcmeWidget(BaseDriver):\n"
        "    DRIVER_INFO = {\n"
        f"        'id': {DRIVER_ID!r},\n"
        "        'name': 'Acme Widget',\n"
        "        'manufacturer': 'Acme',\n"
        "        'category': 'utility',\n"
        "        'version': '1.0.0',\n"
        "        'transport': 'tcp',\n"
        "    }\n"
        f"    MARK = {mark!r}\n"
    ).encode()


COMPANION = b"async def probe(ctx):\n    return None\n"
SIMULATOR = b"class Simulator:\n    pass\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hashes(**files: bytes) -> dict[str, str]:
    return {f"utility/{name}": _sha(data) for name, data in files.items()}


def _entry(files: object) -> dict:
    return {"id": DRIVER_ID, "files": files}


def _install(file_name: str):
    return install_community_driver(
        CommunityDriverInstallRequest(driver_id=DRIVER_ID, file_url=CATALOG + file_name)
    )


def _update(file_name: str):
    request = MagicMock()

    async def _json():
        return {"file_url": CATALOG + file_name}

    request.json = _json
    return update_driver(DRIVER_ID, request)


async def _refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await call
    return caught.value


def _tree(repo: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(repo.iterdir()) if p.is_file()}


def _gets(events: list) -> list[str]:
    return [event[2] for event in events if event[0] == "get"]


def _python_catalog(cat, main: bytes = None) -> None:
    cat.network.pages[CATALOG + "acme_widget.py"] = main if main is not None else _python()
    cat.network.pages[CATALOG + "acme_widget_discovery.py"] = COMPANION
    cat.network.pages[CATALOG + "acme_widget_sim.py"] = SIMULATOR


# --- the client each download opens -----------------------------------------


@pytest.mark.asyncio
async def test_each_download_opens_its_own_client_and_closes_it_before_the_next(cat):
    cat.network.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py"
    )
    cat.network.pages[CATALOG + "acme_widget_discovery.py"] = COMPANION

    await _install("acme_widget.avcdriver")

    assert [e for e in cat.events if e[0] in ("get", "closed")] == [
        ("get", 0, "acme_widget.avcdriver"),
        ("closed", 0),
        ("get", 1, "acme_widget_discovery.py"),
        ("closed", 1),
    ]


@pytest.mark.parametrize("door", ["install", "update"])
@pytest.mark.asyncio
async def test_every_download_is_an_internet_request_with_a_15_second_timeout(cat, door):
    _python_catalog(cat)
    if door == "update":
        (cat.repo / "acme_widget.py").write_bytes(_python("old"))
        await _update("acme_widget.py")
    else:
        await _install("acme_widget.py")

    assert _gets(cat.events) == ["acme_widget.py", "acme_widget_discovery.py", "acme_widget_sim.py"]
    assert len(cat.network.clients) == 3
    for kwargs in cat.network.clients:
        assert set(kwargs) == {"timeout", "verify", "trust_env"}
        assert kwargs["timeout"] == 15.0
        assert isinstance(kwargs["verify"], ssl.SSLContext)
        assert kwargs["trust_env"] is True


@pytest.mark.asyncio
async def test_a_yaml_companion_download_is_an_internet_request_with_a_15_second_timeout(cat):
    cat.network.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py"
    )
    cat.network.pages[CATALOG + "acme_widget_discovery.py"] = COMPANION

    await _install("acme_widget.avcdriver")

    assert [kwargs["timeout"] for kwargs in cat.network.clients] == [15.0, 15.0]
    assert all(isinstance(kwargs["verify"], ssl.SSLContext) for kwargs in cat.network.clients)


# --- how each download fails --------------------------------------------------


@pytest.mark.parametrize("door", ["install", "update"])
@pytest.mark.asyncio
async def test_a_main_file_refused_by_a_certificate_answers_the_certificate_sentence(cat, door):
    error = httpx.ConnectError(CERT_REFUSED)
    cat.network.pages[CATALOG + "acme_widget.avcdriver"] = error
    if door == "update":
        (cat.repo / "acme_widget.avcdriver").write_bytes(_yaml())
        refused = await _refused(_update("acme_widget.avcdriver"))
    else:
        refused = await _refused(_install("acme_widget.avcdriver"))

    assert refused.status_code == 502
    assert refused.detail == internet_failure(error, "raw.githubusercontent.com")
    assert refused.detail.startswith("The connection to raw.githubusercontent.com was answered")


@pytest.mark.asyncio
async def test_a_main_file_that_fails_on_the_network_names_the_driver(cat):
    cat.network.pages[CATALOG + "acme_widget.avcdriver"] = httpx.ConnectError("down")

    refused = await _refused(_install("acme_widget.avcdriver"))

    assert (refused.status_code, refused.detail) == (
        502, "Failed to download driver 'acme_widget'"
    )


@pytest.mark.asyncio
async def test_a_yaml_companion_refused_by_a_certificate_gets_the_plain_failure(cat):
    # Only the main file's download says why a certificate was refused.
    cat.network.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py"
    )
    cat.network.pages[CATALOG + "acme_widget_discovery.py"] = httpx.ConnectError(CERT_REFUSED)

    refused = await _refused(_install("acme_widget.avcdriver"))

    assert (refused.status_code, refused.detail) == (
        502,
        "Driver 'acme_widget' references companion 'acme_widget_discovery.py' "
        "but the download failed",
    )
    assert _tree(cat.repo) == {}


@pytest.mark.asyncio
async def test_a_yaml_companion_refused_with_a_status_names_it_and_aborts(cat):
    cat.network.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py"
    )
    cat.network.pages[CATALOG + "acme_widget_discovery.py"] = 503

    refused = await _refused(_install("acme_widget.avcdriver"))

    assert (refused.status_code, refused.detail) == (
        502,
        "Driver 'acme_widget' references companion 'acme_widget_discovery.py', "
        "but GitHub returned 503 for it. Install aborted.",
    )
    assert _tree(cat.repo) == {}


@pytest.mark.asyncio
async def test_a_yaml_companion_that_cannot_be_written_is_not_answered_as_a_download_failure(cat):
    # The write is outside what the companion download answers for: the
    # OSError reaches the door, which removes only what an HTTPException says.
    cat.network.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(
        companion="./acme_widget_discovery.py"
    )
    cat.network.pages[CATALOG + "acme_widget_discovery.py"] = COMPANION
    (cat.repo / "acme_widget_discovery.py").mkdir()

    with pytest.raises(OSError):
        await _install("acme_widget.avcdriver")

    assert _tree(cat.repo) == {"acme_widget.avcdriver": _yaml(companion="./acme_widget_discovery.py")}
    assert ("register", DRIVER_ID) not in cat.events


@pytest.mark.asyncio
async def test_a_yaml_driver_naming_a_simulator_as_its_companion_is_refused_unasked(cat):
    cat.network.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(companion="./acme_widget_sim.py")
    cat.network.pages[CATALOG + "acme_widget_sim.py"] = SIMULATOR

    refused = await _refused(_install("acme_widget.avcdriver"))

    assert (refused.status_code, refused.detail) == (
        422,
        "Driver 'acme_widget' declares companion './acme_widget_sim.py' with an "
        "invalid filename — must end in '_discovery.py' and use only letters, "
        "numbers, hyphens, and underscores.",
    )
    assert _gets(cat.events) == ["acme_widget.avcdriver"]
    assert _tree(cat.repo) == {}


@pytest.mark.parametrize("name", ["acme widget_discovery.py", "acme.widget_discovery.py"])
@pytest.mark.asyncio
async def test_a_yaml_companion_name_outside_the_safe_characters_is_refused(cat, name):
    cat.network.pages[CATALOG + "acme_widget.avcdriver"] = _yaml(companion=f"./{name}")

    refused = await _refused(_install("acme_widget.avcdriver"))

    assert refused.status_code == 422
    assert "invalid filename" in refused.detail
    assert _gets(cat.events) == ["acme_widget.avcdriver"]


@pytest.mark.asyncio
async def test_a_python_companion_that_cannot_be_written_is_skipped_with_a_warning(cat, caplog):
    _python_catalog(cat)
    (cat.repo / "acme_widget_sim.py").mkdir()

    with caplog.at_level(logging.WARNING):
        answer = await _install("acme_widget.py")

    assert answer["status"] == "installed"
    assert _tree(cat.repo) == {"acme_widget.py": _python(), "acme_widget_discovery.py": COMPANION}
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        message.startswith("Optional companion acme_widget_sim.py not installed: ")
        for message in warnings
    )


@pytest.mark.asyncio
async def test_a_python_companion_that_fails_on_the_network_is_skipped_with_a_warning(cat, caplog):
    _python_catalog(cat)
    cat.network.pages[CATALOG + "acme_widget_discovery.py"] = httpx.ConnectError("down")

    with caplog.at_level(logging.WARNING):
        answer = await _install("acme_widget.py")

    assert answer["status"] == "installed"
    assert _tree(cat.repo) == {"acme_widget.py": _python(), "acme_widget_sim.py": SIMULATOR}
    assert "Optional companion acme_widget_discovery.py not installed: down" in [
        r.getMessage() for r in caplog.records
    ]


@pytest.mark.asyncio
async def test_a_python_companion_off_the_catalog_is_never_asked_for(cat, monkeypatch):
    # The companion's address is the main file's, so it is on the catalog
    # whenever the main file is; the check still stands, and refuses quietly.
    from openavc.utils import community_integrity

    real = community_integrity.validate_catalog_url

    def _refuse_simulators(url, *, owner_repo):
        if url.endswith("_sim.py"):
            raise community_integrity.CommunityArtifactError("not on the catalog")
        real(url, owner_repo=owner_repo)

    monkeypatch.setattr(community_integrity, "validate_catalog_url", _refuse_simulators)
    _python_catalog(cat)
    (cat.repo / "acme_widget_sim.py").write_bytes(b"old simulator\n")
    (cat.repo / "acme_widget.py").write_bytes(_python("old"))

    answer = await _update("acme_widget.py")

    assert answer["status"] == "updated"
    assert _gets(cat.events) == ["acme_widget.py", "acme_widget_discovery.py"]
    # Not a 404, so the previous simulator stays.
    assert (cat.repo / "acme_widget_sim.py").read_bytes() == b"old simulator\n"


# --- the catalog's hashes -----------------------------------------------------


@pytest.mark.asyncio
async def test_every_file_is_checked_against_the_hash_published_for_its_own_path(cat):
    _python_catalog(cat)
    cat.index.entries = [
        _entry(_hashes(**{
            "acme_widget.py": _python(),
            "acme_widget_discovery.py": COMPANION,
            "acme_widget_sim.py": SIMULATOR,
        }))
    ]

    answer = await _install("acme_widget.py")

    assert answer["status"] == "installed"
    assert cat.index.forces == [False]
    assert _tree(cat.repo) == {
        "acme_widget.py": _python(),
        "acme_widget_discovery.py": COMPANION,
        "acme_widget_sim.py": SIMULATOR,
    }


@pytest.mark.asyncio
async def test_a_yaml_companion_is_checked_against_its_published_hash(cat):
    main = _yaml(companion="./acme_widget_discovery.py")
    cat.network.pages[CATALOG + "acme_widget.avcdriver"] = main
    cat.network.pages[CATALOG + "acme_widget_discovery.py"] = COMPANION
    cat.index.entries = [
        _entry(_hashes(**{"acme_widget.avcdriver": main, "acme_widget_discovery.py": b"other"}))
    ]

    refused = await _refused(_install("acme_widget.avcdriver"))

    assert (refused.status_code, refused.detail) == (502, NOT_YET)
    assert cat.index.forces == [False, True]
    assert _tree(cat.repo) == {}


@pytest.mark.asyncio
async def test_the_first_catalog_entry_for_the_id_is_the_one_read(cat):
    _python_catalog(cat)
    good = _hashes(**{
        "acme_widget.py": _python(),
        "acme_widget_discovery.py": COMPANION,
        "acme_widget_sim.py": SIMULATOR,
    })
    cat.index.entries = [
        {"id": "other_driver", "files": {"utility/acme_widget.py": _sha(b"x")}},
        _entry(good),
        _entry({"utility/acme_widget.py": _sha(b"stale")}),
    ]

    answer = await _install("acme_widget.py")

    assert answer["status"] == "installed"
    assert cat.index.forces == [False]


@pytest.mark.parametrize("files", [None, ["utility/acme_widget.py"], "utility/acme_widget.py", {}])
@pytest.mark.asyncio
async def test_an_entry_without_a_mapping_of_files_publishes_no_hashes(cat, files):
    _python_catalog(cat)
    cat.index.entries = [_entry(files)]

    answer = await _install("acme_widget.py")

    assert answer["status"] == "installed"
    assert cat.index.forces == [False]


@pytest.mark.asyncio
async def test_a_disagreement_fetches_the_catalog_again_past_the_cached_copy(cat):
    _python_catalog(cat)
    cat.index.entries = [_entry(_hashes(**{"acme_widget.py": b"stale copy"}))]
    cat.index.refetched = [
        _entry(_hashes(**{
            "acme_widget.py": _python(),
            "acme_widget_discovery.py": COMPANION,
            "acme_widget_sim.py": SIMULATOR,
        }))
    ]

    answer = await _install("acme_widget.py")

    assert answer["status"] == "installed"
    assert cat.index.forces == [False, True]


@pytest.mark.asyncio
async def test_a_disagreement_that_survives_the_refetch_writes_nothing(cat):
    _python_catalog(cat)
    cat.index.entries = [_entry(_hashes(**{"acme_widget.py": b"stale copy"}))]

    refused = await _refused(_install("acme_widget.py"))

    assert (refused.status_code, refused.detail) == (502, NOT_YET)
    assert cat.index.forces == [False, True]
    assert _tree(cat.repo) == {}
    assert _gets(cat.events) == ["acme_widget.py"]


@pytest.mark.asyncio
async def test_an_unreachable_catalog_publishes_no_hashes(cat, monkeypatch, caplog):
    async def _down(self, force=False):
        raise RuntimeError("catalog down")

    monkeypatch.setattr(
        "openavc.discovery.community_index.CommunityIndexCache.get_drivers", _down
    )
    _python_catalog(cat)

    with caplog.at_level(logging.WARNING):
        answer = await _install("acme_widget.py")

    assert answer["status"] == "installed"
    assert "Could not read community catalog hashes for 'acme_widget'" in [
        r.getMessage() for r in caplog.records
    ]


@pytest.mark.asyncio
async def test_a_python_update_whose_simulator_fails_the_check_is_left_unregistered(cat):
    # The new main file and discovery companion are written and the driver
    # unregistered before the simulator is checked, so the refusal leaves
    # the update half done (see the doors' pins for the other rollbacks).
    _python_catalog(cat)
    (cat.repo / "acme_widget.py").write_bytes(_python("old"))
    (cat.repo / "acme_widget_sim.py").write_bytes(b"old simulator\n")
    cat.index.entries = [
        _entry(_hashes(**{
            "acme_widget.py": _python(),
            "acme_widget_discovery.py": COMPANION,
            "acme_widget_sim.py": b"published simulator",
        }))
    ]

    refused = await _refused(_update("acme_widget.py"))

    assert (refused.status_code, refused.detail) == (502, NOT_YET)
    assert _tree(cat.repo) == {
        "acme_widget.py": _python(),
        "acme_widget_discovery.py": COMPANION,
        "acme_widget_sim.py": b"old simulator\n",
    }
    assert [e for e in cat.events if e[0] in ("register", "unregister", "announce")] == [
        ("unregister", DRIVER_ID)
    ]


# --- Browse Community -----------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_community_says_why_the_network_refused_the_catalog(cat):
    cat.index.failure_sentence = "The proxy server refused the connection to raw.githubusercontent.com (x)."

    answer = await routes.get_community_drivers(refresh=True)

    assert answer == {"drivers": [], "error": cat.index.failure_sentence}
    assert cat.index.forces == [True]


@pytest.mark.asyncio
async def test_browse_community_lists_what_the_catalog_answered(cat):
    cat.index.entries = [_entry(None)]

    answer = await routes.get_community_drivers()

    assert answer == {"drivers": [_entry(None)], "error": None}
    assert cat.index.forces == [False]
