"""A Python driver that will not parse must not be persisted by Save & Reload.

The Code view's two buttons want two different answers to "this source has a
syntax error", and before this the route gave the same answer to both: write
it, say nothing.

* **Save & Reload** means "make this the live driver". Writing source that
  cannot parse leaves the *running* process happily serving devices from the
  old code while the file on disk is dead — so nothing looks wrong until a
  restart drops the driver and takes its devices offline with it. The reload
  is already refused; the write must be too.
* **Plain Save** is an editor keeping work in progress, which is the normal
  state of an edit and must not be lost. It persists — and now says what it
  kept will not load, which is the half the author was never told.

The check is a *parse*, not an import: a file that fails to import (a missing
third-party module, a NameError at module level) is legitimate work in
progress, while a file that fails to parse cannot load on any future startup.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from server.api.routes import python_drivers as driver_routes
from server.drivers.driver_loader import (
    python_source_syntax_error,
    reload_python_driver,
)


GOOD_SOURCE = '''\
from server.drivers.base import BaseDriver


class AcmeWidget(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
    }
'''

# Same file with the class statement's colon removed — a parse failure, on a
# line the editor can point at.
BROKEN_SOURCE = GOOD_SOURCE.replace(
    "class AcmeWidget(BaseDriver):", "class AcmeWidget(BaseDriver)"
)

# Parses fine, cannot import: the module this names does not exist. Must be
# treated as work in progress, not refused.
UNIMPORTABLE_SOURCE = GOOD_SOURCE.replace(
    "from server.drivers.base import BaseDriver",
    "from acme_vendor_sdk_that_is_not_installed import BaseDriver",
)


@pytest.fixture
def driver_repo(tmp_path, monkeypatch):
    """Point the save route's driver_repo/ at a scratch directory."""
    import server.system_config as system_config

    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(system_config, "DRIVER_REPO_DIR", repo)
    return repo


async def _save(driver_id: str, source: str, **extra):
    return await driver_routes.save_python_driver_source(
        driver_id, {"source": source, **extra}
    )


# --- the parse check itself --------------------------------------------------

def test_broken_source_reports_the_line():
    report = python_source_syntax_error(BROKEN_SOURCE, "acme_widget.py")
    assert report is not None
    assert report["line"] == 4
    assert "acme_widget.py" in report["error"]
    assert report["error"].startswith("SyntaxError: ")


def test_good_source_reports_nothing():
    assert python_source_syntax_error(GOOD_SOURCE, "acme_widget.py") is None


def test_unimportable_but_parseable_source_is_not_a_syntax_error():
    """The gate is a parse. An import that cannot resolve is still valid work."""
    assert python_source_syntax_error(UNIMPORTABLE_SOURCE, "acme_widget.py") is None


def test_save_and_reload_describe_a_broken_file_identically(driver_repo):
    """One formatter, so the author reads the same sentence at either door.

    The reload path's wording is the one the docs quote and the editor's
    clickable line marker is built from; a save door that phrased it its own
    way would be a second description of the same fact.
    """
    path = driver_repo / "acme_widget.py"
    path.write_text(BROKEN_SOURCE, encoding="utf-8")

    from_reload = reload_python_driver(path)
    from_save = python_source_syntax_error(BROKEN_SOURCE, path.name)

    assert from_reload["status"] == "error"
    assert from_reload["error"] == from_save["error"]
    assert from_reload["line"] == from_save["line"]


# --- Save & Reload: refuse, and leave the file alone -------------------------

@pytest.mark.asyncio
async def test_save_and_reload_refuses_to_persist_unparseable_source(driver_repo):
    path = driver_repo / "acme_widget.py"
    path.write_text(GOOD_SOURCE, encoding="utf-8")

    result = await _save("acme_widget", BROKEN_SOURCE, require_valid_syntax=True)

    assert result["status"] == "error"
    assert result["saved"] is False
    assert result["line"] == 4
    # The load-bearing assertion: the bytes on disk are the ones that work.
    assert path.read_text(encoding="utf-8") == GOOD_SOURCE


@pytest.mark.asyncio
async def test_save_and_reload_persists_source_that_parses(driver_repo):
    path = driver_repo / "acme_widget.py"
    path.write_text("# placeholder\n", encoding="utf-8")

    result = await _save("acme_widget", GOOD_SOURCE, require_valid_syntax=True)

    assert result["status"] == "saved"
    assert path.read_text(encoding="utf-8") == GOOD_SOURCE


@pytest.mark.asyncio
async def test_save_and_reload_persists_source_that_only_fails_to_import(driver_repo):
    """A dependency that isn't installed must not cost the author their edit."""
    path = driver_repo / "acme_widget.py"
    path.write_text(GOOD_SOURCE, encoding="utf-8")

    result = await _save("acme_widget", UNIMPORTABLE_SOURCE, require_valid_syntax=True)

    assert result["status"] == "saved"
    assert path.read_text(encoding="utf-8") == UNIMPORTABLE_SOURCE


# --- plain Save: persist, but say so -----------------------------------------

@pytest.mark.asyncio
async def test_plain_save_keeps_work_in_progress_and_reports_it(driver_repo):
    path = driver_repo / "acme_widget.py"
    path.write_text(GOOD_SOURCE, encoding="utf-8")

    result = await _save("acme_widget", BROKEN_SOURCE)

    assert result["status"] == "saved"
    assert path.read_text(encoding="utf-8") == BROKEN_SOURCE
    # Silence here is the original defect: the file no longer loads and the
    # running driver hides it until the next restart.
    assert result["line"] == 4
    assert "SyntaxError" in result["syntax_error"]


@pytest.mark.asyncio
async def test_plain_save_of_good_source_reports_no_syntax_error(driver_repo):
    path = driver_repo / "acme_widget.py"
    path.write_text("# placeholder\n", encoding="utf-8")

    result = await _save("acme_widget", GOOD_SOURCE)

    assert result["status"] == "saved"
    assert "syntax_error" not in result
    assert path.read_text(encoding="utf-8") == GOOD_SOURCE


@pytest.mark.asyncio
async def test_missing_driver_is_still_a_404_before_any_parsing(driver_repo):
    with pytest.raises(HTTPException) as exc:
        await _save("no_such_driver", BROKEN_SOURCE, require_valid_syntax=True)
    assert exc.value.status_code == 404
