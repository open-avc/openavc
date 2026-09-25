"""The driver test: one run per driver the person chooses, and what each run did.

A session holds a list of runs. The last one is current: the person chose its
driver on "Which driver?", and every later step (the connection, connect and
listen) acts on it. "Test another driver" closes the current run, keeping its
results for the report, and goes back to the choice; the report gets a section
per run.

Each run owns at most one driver sandbox (``audit/sandbox.py``) at a time. The
session's teardown stops it, whichever way the session ends, before the
session resumes the project devices it paused, so the audited device's one
control connection is free when they reconnect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openavc.audit.driver_choice import (
    DriverChoice,
    driver_identity,
    model_listing,
    verdict_agreement,
)
from openavc.audit.session import AuditError
from openavc.drivers.registry import get_driver_class
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.audit.sandbox import DriverSandbox
    from openavc.audit.session import AuditSession

log = get_logger(__name__)

NOT_INSTALLED = "Install {driver} first, then choose it again."
RUN_IN_PROGRESS = (
    "{driver} is still connected to the device. Choose Test another driver to "
    "finish with it first."
)
NO_DRIVER_CHOSEN = "Choose the driver to test first."
NOT_SAVED_HERE = "That device's saved settings are not available to this audit."



@dataclass
class DriverRun:
    """One driver tested against the device."""

    index: int
    choice: DriverChoice
    started_at: float | None = None
    finished_at: float | None = None
    sandbox: "DriverSandbox | None" = None
    # The connection settings, as the driver will get them (secrets included;
    # never sent to a browser), and the credential values among them.
    config: dict[str, Any] | None = None
    secrets: set[str] = field(default_factory=set)
    # What the connection step shows: the settings with secrets masked, where
    # they came from, and what connecting will send.
    connection: dict[str, Any] | None = None
    # Connect and listen (``audit/listen.py``): the current attempt, and every
    # attempt in order (a failed sign-in, then the one after the fix).
    listen: Any = None
    listens: list[Any] = field(default_factory=list)
    # State the later steps add to ``to_dict`` (name -> value or provider).
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.sandbox is not None and self.sandbox.started

    def to_dict(self) -> dict[str, Any]:
        out = {
            "index": self.index,
            "choice": self.choice.to_dict(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "active": self.active,
            "connection": self.connection,
        }
        for key, provider in self.extra.items():
            try:
                out[key] = provider() if callable(provider) else provider
            except Exception:
                log.debug("Audit run state provider failed", exc_info=True)
        return out

    async def stop(self) -> None:
        """Stop the sandbox, if one is running. Results stay."""
        import time

        if self.listen is not None:
            await self.listen.stop()
        sandbox = self.sandbox
        if sandbox is not None and sandbox.started:
            await sandbox.stop()
        if self.started_at is not None and self.finished_at is None:
            self.finished_at = time.time()


def current_run(session: "AuditSession") -> DriverRun | None:
    return session.runs[-1] if session.runs else None


def runs_state(session: "AuditSession") -> dict[str, Any]:
    """The runs as the wizard draws them (secrets never included)."""
    return {
        "device": dict(session.device_entered),
        "no_driver": session.no_driver,
        "runs": [run.to_dict() for run in session.runs],
    }


def open_runs(session: "AuditSession") -> None:
    """Make ``session`` able to hold driver runs, stopped at its teardown."""

    async def stop_runs() -> None:
        for run in session.runs:
            try:
                await run.stop()
            except Exception:
                log.warning("Stopping an audit driver run failed", exc_info=True)

    session.on_teardown(stop_runs)
    session.add_state_provider(lambda: runs_state(session))


def choose_driver(
    session: "AuditSession",
    driver_id: str | None,
    *,
    manufacturer: str = "",
    model: str = "",
    firmware: str = "",
    catalog: list[dict[str, Any]] | None = None,
) -> DriverRun | None:
    """Record the person's answer on "Which driver?".

    ``driver_id`` None means "there is no driver for this device yet": the
    device's make and model are recorded and the report covers the network
    check. Otherwise the driver must be installed. Choosing again before the
    current run has connected replaces that choice; once it has, the person
    finishes it first (``next_driver``).
    """
    entered = {
        "manufacturer": manufacturer.strip(),
        "model": model.strip(),
        "firmware": firmware.strip(),
    }
    session.device_entered = entered
    session.enter_step("driver")
    run = current_run(session)
    if driver_id is None:
        if run is not None and run.active:
            raise AuditError(RUN_IN_PROGRESS.format(driver=run.choice.identity.get("name")))
        if run is not None and run.started_at is None:
            session.runs.pop()
        session.no_driver = True
        session.add_timeline(
            "driver.none", "The person said there is no driver for this device yet.",
            **entered,
        )
        return None

    if get_driver_class(driver_id) is None:
        raise AuditError(NOT_INSTALLED.format(driver=driver_id))
    if run is not None and run.active:
        raise AuditError(RUN_IN_PROGRESS.format(driver=run.choice.identity.get("name")))

    verdict = session.footprint.verdict if session.footprint is not None else None
    choice = DriverChoice(
        driver_id=driver_id,
        manufacturer=entered["manufacturer"],
        model=entered["model"],
        firmware=entered["firmware"],
        identity=driver_identity(driver_id, catalog),
        model_listing=model_listing(driver_id, entered["manufacturer"], entered["model"], catalog),
        verdict_agreement=verdict_agreement(verdict, driver_id),
    )
    if run is not None and run.started_at is None:
        run.choice = choice  # changed their mind before connecting
    else:
        run = DriverRun(index=len(session.runs), choice=choice)
        session.runs.append(run)
    session.no_driver = False
    name = choice.identity.get("name") or driver_id
    version = choice.identity.get("version")
    session.add_timeline(
        "driver.chosen",
        f"Driver chosen: {name}{' ' + version if version else ''}.",
        driver_id=driver_id, verdict_agreement=choice.verdict_agreement,
        modified=choice.identity.get("modified"),
    )
    return run


def _driver_info(driver_id: str) -> dict[str, Any]:
    cls = get_driver_class(driver_id)
    return dict(getattr(cls, "DRIVER_INFO", {}) or {})


def _masked(config: dict[str, Any], secret_keys: set[str]) -> dict[str, Any]:
    return {
        key: ("***" if key in secret_keys and value not in (None, "") else value)
        for key, value in config.items()
    }


def _secret_keys(config: dict[str, Any], schema: dict[str, Any]) -> set[str]:
    from openavc.utils.log_redaction import is_secret_key

    declared = {
        k for k, spec in (schema or {}).items()
        if isinstance(spec, dict) and spec.get("secret") is True
    }
    return {k for k in config if k in declared or is_secret_key(k)}


def saved_settings(session: "AuditSession", project: Any) -> list[dict[str, Any]]:
    """The paused project devices whose settings the current driver can use.

    Only a device the audit paused at this address, on the same driver: its
    settings are the ones a production system dials this device with. Secret
    values stay here; the browser learns which are set, never what they are.
    """
    from openavc.core.device_config import resolve_device_config

    run = current_run(session)
    if run is None or project is None:
        return []
    paused = {p.device_id: p.name for p in session.paused}
    schema = _driver_info(run.choice.driver_id).get("config_schema") or {}
    out = []
    for device in getattr(project, "devices", []):
        if device.id not in paused or device.driver != run.choice.driver_id:
            continue
        config = resolve_device_config(device, project)["config"]
        secret_keys = _secret_keys(config, schema)
        out.append({
            "device_id": device.id,
            "name": paused[device.id],
            "config": {k: v for k, v in config.items() if k not in secret_keys},
            "secrets_set": sorted(k for k in secret_keys if config.get(k) not in (None, "")),
        })
    return out


def _saved_config(session: "AuditSession", project: Any, device_id: str) -> dict[str, Any]:
    from openavc.core.device_config import resolve_device_config

    offered = {s["device_id"] for s in saved_settings(session, project)}
    if device_id not in offered:
        raise AuditError(NOT_SAVED_HERE)
    device = next(d for d in project.devices if d.id == device_id)
    return dict(resolve_device_config(device, project)["config"])


async def set_connection(
    session: "AuditSession",
    config: dict[str, Any],
    *,
    use_saved: str | None = None,
    project: Any = None,
) -> DriverRun:
    """Record the connection settings for the current driver and preview
    what connecting will send.

    With ``use_saved``, a paused project device's saved settings are the base
    and what the person entered goes on top; a secret field they left empty
    keeps the saved value. The sandbox's own checks run now (a serial port
    that would simulate is refused here, before anything connects).
    """
    from openavc.audit.sandbox import DriverSandbox, audit_device_id
    from openavc.utils.log_redaction import collect_secret_values

    run = current_run(session)
    if run is None or not run.choice.driver_id:
        raise AuditError(NO_DRIVER_CHOSEN)
    # New settings end a connection made with the old ones.
    if run.listen is not None:
        await run.listen.stop()
    entered = {k: v for k, v in config.items() if v not in (None, "")}
    base: dict[str, Any] = {}
    saved_name = ""
    if use_saved:
        base = _saved_config(session, project, use_saved)
        saved_name = next((p.name for p in session.paused if p.device_id == use_saved), use_saved)
    merged = {**base, **entered}

    info = _driver_info(run.choice.driver_id)
    sandbox = DriverSandbox(audit_device_id(session.id), run.choice.driver_id, merged)
    resolved = sandbox.prepare()  # raises AuditError with the sentence
    effective = resolved["config"]
    secret_keys = _secret_keys(effective, info.get("config_schema") or {})

    run.config = merged
    run.secrets = collect_secret_values(effective, info.get("config_schema")) | {
        str(effective[k]) for k in secret_keys
        if isinstance(effective.get(k), str) and effective[k]
    }
    preview = await _preview(session, run, effective)
    run.connection = {
        "config": _masked(effective, secret_keys),
        "transport": sandbox.transport,
        "saved_from": saved_name,
        "preview": preview,
    }
    session.enter_step("connection")
    text = (
        f"Connection settings chosen, from {saved_name}'s saved settings."
        if saved_name else "Connection settings chosen."
    )
    session.add_timeline("driver.connection", text, transport=sandbox.transport)
    return run


async def _preview(
    session: "AuditSession", run: DriverRun, config: dict[str, Any],
) -> dict[str, Any]:
    """What connecting sends, masked for the browser."""
    from openavc.core.device_traffic import TrafficRedactor
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.drivers.dry_run import preview_connect
    from openavc.utils.log_redaction import get_secret_registry

    cls = get_driver_class(run.choice.driver_id)
    preview_id = f"audit-{session.id}-preview"
    try:
        driver = cls(preview_id, dict(config), StateStore(), EventBus())
        result = await preview_connect(driver)
    except Exception as exc:
        log.debug("Connection preview failed", exc_info=True)
        return {"available": False, "reason": f"The preview could not be built: {exc}",
                "steps": [], "poll_interval": 0, "keep_alive_interval": 0}
    finally:
        get_secret_registry().forget(preview_id)
    redactor = TrafficRedactor(run.secrets)
    steps = []
    for step in result.steps:
        if step["kind"] == "send":
            data = redactor.data(step["data"])
            steps.append({"stage": step["stage"], "kind": "send",
                          "hex": data.hex(), "text": data.decode("latin-1")})
        else:
            steps.append(redactor.value(dict(step)))
    return {
        "available": result.available,
        "reason": result.reason,
        "steps": steps,
        "poll_interval": result.poll_interval,
        "keep_alive_interval": result.keep_alive_interval,
    }


async def next_driver(session: "AuditSession") -> None:
    """Finish the current run (its results stay) so another can be chosen."""
    run = current_run(session)
    if run is None:
        return
    await run.stop()
    session.add_timeline(
        "driver.finished",
        f"Finished testing {run.choice.identity.get('name') or run.choice.driver_id}.",
        driver_id=run.choice.driver_id,
    )
