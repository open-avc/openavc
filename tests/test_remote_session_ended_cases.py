"""The case file for an ended Remote Access session, held to the wire contract.

A panel or Programmer page opened through Remote Access learns its session
ended from the cloud: a bare-text refusal of a request, or its socket closed
with the code the wire contract flags ``session_ended`` (``ws_close_codes``).
Both pages read those through ``tests/fixtures/remote_session_ended_cases.json``
(the panel harness's remote_session scenarios and the Programmer's
remoteSessionEnded.test.ts). The pages cannot read the contract, so their code
is a copy; this holds the case file's copy to it.

The code reaches a page only from the cloud. This instance's own closes pass
through the tunnel as they are, so none of the sockets a page reads may close
with it.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads(
    (ROOT / "tests" / "fixtures" / "remote_session_ended_cases.json").read_text("utf-8")
)
CONTRACT = json.loads(
    (ROOT / "openavc" / "cloud" / "wire_contract.json").read_text("utf-8")
)

#: Server modules that close a socket with the session-ended code, and why a
#: page cannot mistake it: the Programmer's IR learning socket, whose close
#: code nothing reads.
MAY_CLOSE_WITH_IT = {
    "openavc/api/ir_learn_ws.py": "the IR learning socket; no page reads its close code",
    "openavc/cloud/wire_tables.py": "the contract's own entry",
}


def _session_ended_code() -> int:
    codes = [
        entry["value"] for entry in CONTRACT["vocabularies"]["ws_close_codes"]
        if "session_ended" in entry.get("flags", [])
    ]
    assert len(codes) == 1, codes
    return codes[0]


def test_the_case_files_ended_close_is_the_contracts_code():
    ended = {c["code"] for c in CASES["closes"] if c["ended"]}
    assert ended == {_session_ended_code()}


def test_no_other_close_in_the_case_file_carries_it():
    others = [c for c in CASES["closes"] if not c["ended"]]
    assert others
    assert all(c["code"] != _session_ended_code() for c in others)


def test_every_case_is_named_once():
    for group in ("answers", "closes"):
        names = [c["name"] for c in CASES[group]]
        assert len(names) == len(set(names)), group


def test_this_instance_never_closes_a_page_socket_with_it():
    code = re.compile(rf"(?<!\d){_session_ended_code()}(?!\d)")
    found = sorted(
        str(path.relative_to(ROOT).as_posix())
        for path in (ROOT / "openavc").rglob("*.py")
        if code.search(path.read_text("utf-8"))
    )
    assert found == sorted(MAY_CLOSE_WITH_IT), (
        "only the cloud closes a page's socket with this code; "
        f"a server module now uses it: {sorted(set(found) - set(MAY_CLOSE_WITH_IT))}"
    )
