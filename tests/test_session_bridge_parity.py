"""The Programmer's sign-in, as the IDE keeps and sends it and as an embedded panel does.

The IDE keeps its session token in sessionStorage and sends it as
``Authorization: Bearer <token>`` on a request and as the
``auth.bearer.<token>`` WebSocket subprotocol
(openavc/web/programmer/src/api/auth.ts). The UI Builder's canvas and its
Preview are the panel in a same-origin frame, which reads the same key and
sends the same two forms (openavc/web/panel/modules/session.js). Nothing at
run time ties the two halves together: a panel reading a key the IDE no longer
writes, or spelling a form the server does not read, fails only inside the
Builder on a system with a password, where the browser's own sign-in prompt
opens inside the frame and the socket is refused. So these read both files,
hold the key and both forms equal, and run the server's own readers on what
the panel sends.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import openavc.api.auth as auth_mod
import openavc.api.session_tokens as st_mod
from openavc.api.auth import check_ws_auth, require_programmer_auth
from openavc.api.session_tokens import SessionTokenStore

REPO = Path(__file__).resolve().parents[1]
IDE = REPO / "openavc" / "web" / "programmer" / "src" / "api" / "auth.ts"
PANEL = REPO / "openavc" / "web" / "panel" / "modules" / "session.js"

_STORAGE_KEY = re.compile(r"""const STORAGE_KEY = (['"])([^'"]+)\1;""")
#: Every template literal that builds a string from the token.
_TOKEN_FORM = re.compile(r"`([^`]*\$\{token\}[^`]*)`")


def _storage_key(path: Path) -> str:
    found = _STORAGE_KEY.findall(path.read_text(encoding="utf-8"))
    assert len(found) == 1, f"{path.name}: expected one STORAGE_KEY, found {found}"
    return found[0][1]


def _token_forms(path: Path) -> set[str]:
    return set(_TOKEN_FORM.findall(path.read_text(encoding="utf-8")))


def test_the_panel_reads_the_key_the_ide_writes() -> None:
    assert _storage_key(PANEL) == _storage_key(IDE) == "openavc.programmer.session"


def test_the_panel_sends_the_token_in_the_ides_two_forms() -> None:
    assert _token_forms(PANEL) == _token_forms(IDE) == {"Bearer ${token}", "auth.bearer.${token}"}


@pytest.fixture()
def session_token(monkeypatch) -> str:
    """A live session on a system with a password, so nothing passes without one."""
    monkeypatch.setattr(auth_mod, "_get_username", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_password", lambda: "hunter22")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")
    store = SessionTokenStore()
    monkeypatch.setattr(st_mod, "store", store)
    token, _ = store.issue(auth_mod.credential_fingerprint())
    return token


def _panel_form(prefix: str, token: str) -> str:
    (form,) = [f for f in _token_forms(PANEL) if f.startswith(prefix)]
    return form.replace("${token}", token)


def test_the_server_takes_the_header_the_panel_sends(session_token: str) -> None:
    app = FastAPI()

    @app.get("/api/protected", dependencies=[Depends(require_programmer_auth)])
    async def protected():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/api/protected").status_code == 401
    header = _panel_form("Bearer ", session_token)
    assert client.get("/api/protected", headers={"Authorization": header}).status_code == 200


def test_the_server_takes_the_subprotocol_the_panel_offers(session_token: str) -> None:
    assert check_ws_auth({}, {}) is False
    protocol = _panel_form("auth.", session_token)
    assert check_ws_auth({}, {"sec-websocket-protocol": protocol}) is True
