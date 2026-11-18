"""The project's revision: the token every change to the project is saved
under, and the read of the project that carries it.

THE revision token is ``_project_revision``: an opaque token, compared for
equality and nothing else (``_init_project_revision`` says why). Each change
mints the next one (``_new_revision``) under ``_reload_lock`` and puts the
one before it back when the change does not run (the change door,
``core/project_changes.py``). A change sent with the revision it was built on
is refused when the project has moved past it (``_check_revision``,
``ProjectRevisionConflictError``), checked under the same lock that mints
the next one.

THE read a change built elsewhere starts from is ``read_saved_project``: the
project file and the revision it holds, read together under the lock (the
cloud reads the project this way, merges a template into it and pushes the
result back with that revision).

Mixed into Engine through ``ProjectChangesMixin``, which holds the lock.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from typing import Any


class ProjectRevisionConflictError(Exception):
    """A revision-checked save found a newer project revision on the server."""


@dataclass(frozen=True)
class SavedRead:
    """The project file as JSON and the revision it holds, read together
    (:meth:`ProjectRevisionMixin.read_saved_project`): a change built on the
    read elsewhere is sent back with ``revision`` as its expected revision."""

    project_json: Any
    revision: str


class ProjectRevisionMixin:
    """The revision token: minted, checked, and read with the file."""

    def _init_project_revision(self) -> None:
        # The project's revision, as an OPAQUE token — compared for equality
        # and nothing else. It was a counter from 0, which meant every run
        # handed out the same values: a Builder tab left open across a restart
        # held an `If-Match` the new process agreed with, and its first autosave
        # saved over everything anyone had done since, with no 409 and nothing
        # in the log, because as far as the server could tell it WAS a valid
        # save. The random half makes a token from an earlier run unmatchable;
        # the counter half keeps a log line readable ("the 12th save this run").
        # Never parse it, order it, or persist it — a token restored from a
        # backup would be one this run has already handed out.
        self._boot_id: str = secrets.token_hex(4)
        self._revision_counter: int = 0  # saves committed this run
        self._project_revision: str = f"{self._boot_id}-0"

    async def read_saved_project(self) -> SavedRead:
        """THE read a change built elsewhere starts from (the cloud reads the
        project, merges a template into it and pushes the result back): the
        project file and the revision it holds, read together under
        ``_reload_lock``. A change takes its revision before its file is
        written (``ProjectChangesMixin._swap_in``), so a read made beside the
        lock could pair the file as it was with the new revision, and a
        change built on it would pass the revision check and replace that
        change. Raises what the read raises (``FileNotFoundError``, a
        ``ValueError`` for a file that is not JSON)."""
        async with self._reload_lock:
            text = await asyncio.to_thread(self.project_path.read_text, encoding="utf-8")
            return SavedRead(json.loads(text), self._project_revision)

    def _check_revision(self, expected_revision: str | None) -> None:
        """Refuse a save made against a revision this run has moved past;
        ``None`` skips the check. Caller holds ``_reload_lock``."""
        if expected_revision is None or expected_revision == self._project_revision:
            return
        if self._revision_counter == 0:
            # Nothing has been saved since this process started, so no
            # other writer can have invalidated the caller's token — it
            # is from an earlier run. "Another session" would send them
            # looking for a colleague who was never there.
            raise ProjectRevisionConflictError(
                "The system restarted since this page loaded the project. "
                "Reload to see the latest changes."
            )
        raise ProjectRevisionConflictError(
            "Project was modified by another session. "
            "Reload to see the latest changes."
        )

    def _new_revision(self) -> str:
        """Mint the next revision token: this run's id plus a save count."""
        self._revision_counter += 1
        return f"{self._boot_id}-{self._revision_counter}"
