"""How one start runs its stages, and what it keeps of the ones that failed.

A start is a list of stages, written once: the server's own around the
engine's (``openavc.main._initialize_engine``) and the engine's
(``Engine.start``, core/engine_lifecycle.py). Each runs through one
``StartRun``, which knows the stage it is in, so whatever stops the start is
named by its stage, and keeps what the start went on past.

Two kinds of stage:

- ``run`` (or ``stage``, around a few lines): a stage the start cannot go on
  without. One that raises stops the start; the raise goes on up, and
  ``stopped_by`` words it with the stage it came from ("Loading the project
  failed: ...").
- ``go_on``: a stage the start goes on past (a ``GoOn``; which stages are,
  and why, is core/engine_lifecycle.py's docstring). One that raises is
  logged with its traceback and kept in ``problems`` as a ``StartProblem``:
  its stage and the sentence any reader may be told, never the exception's
  text. A service that catches its own failure, because a settings change
  starts it again later as well (core/runtime_services.py), says so, and the
  start keeps it the same way (``kept``).

``openavc.main`` hands both to the run-state record
(middleware/startup_splash.py), which ``/api/startup-status`` answers. Every
part of the start that failed, a device that could not be added included,
is also named in one warning (``report``), by whoever made the run once its
last stage has run, as a stop names its stages (core/stop_stages.py).
"""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, NamedTuple

from openavc.utils.logger import get_logger

log = get_logger("openavc.core.engine")


class GoOn(NamedTuple):
    """A stage the start goes on past when it fails: its code, what it does
    (for the log), and the sentence the run-state record keeps for it."""

    stage: str
    doing: str
    detail: str


@dataclass(frozen=True)
class StartProblem:
    """A whole stage that failed while the start went on: which one (a
    ``GoOn``'s code) and the sentence any reader may be told."""

    stage: str
    detail: str

    def answer(self) -> dict[str, str]:
        return {"stage": self.stage, "detail": self.detail}


def _sentence(doing: str) -> str:
    return doing[:1].upper() + doing[1:]


class StartRun:
    """One start's stages: the one it is in (``at``), the whole stages it
    went on past (``problems``), and every failure the warning names
    (``named``)."""

    def __init__(self) -> None:
        self.at: str | None = None
        self.problems: list[StartProblem] = []
        self.named: list[str] = []

    @contextlib.contextmanager
    def stage(self, doing: str) -> Iterator[None]:
        """The lines inside are the stage ``doing``: a raise from them stops
        the start, named by ``stopped_by``. ``run`` for one call."""
        self.at = doing
        yield

    async def run(self, doing: str, step: Callable[[], Any]) -> Any:
        """Run ``step`` (sync, or returning an awaitable) as the stage
        ``doing`` ("loading the project"), and return what it returns. A
        raise stops the start: it goes on up, named by ``stopped_by``."""
        self.at = doing
        result = step()
        if inspect.isawaitable(result):
            result = await result
        return result

    async def go_on(self, stage: GoOn, step: Callable[[], Any]) -> Any:
        """Run ``step`` as ``stage`` and return what it returns; a raise is
        logged and kept, the start goes on, and the stage returns None."""
        self.at = stage.doing
        try:
            result = step()
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:  # Catch-all: a whole stage the start goes on past
            log.exception("%s failed", _sentence(stage.doing))
            self.problems.append(StartProblem(stage.stage, stage.detail))
            self.name(f"{_sentence(stage.stage.replace('_', ' '))}: {exc}")
            return None

    def kept(self, stage: GoOn) -> None:
        """``stage`` failed and its own catch logged why: kept in
        ``problems`` and named in the warning, as a raise from ``go_on``
        is, and the start goes on."""
        self.problems.append(StartProblem(stage.stage, stage.detail))
        self.name(f"{_sentence(stage.stage.replace('_', ' '))}: did not start")

    def name(self, failure: str) -> None:
        """A part of the start that failed, for the warning alone: a device
        that could not be added says so on its own card."""
        self.named.append(failure)

    def stopped_by(self, exc: BaseException) -> str:
        """The sentence for the raise that stopped the start: the stage it
        came from, then what it said."""
        if self.at is None:
            return str(exc)
        return f"{_sentence(self.at)} failed: {exc}"

    def report(self) -> None:
        """Name every part of the start that failed, in one warning."""
        if self.named:
            log.warning(
                f"Engine started with {len(self.named)} error(s): {'; '.join(self.named)}"
            )
