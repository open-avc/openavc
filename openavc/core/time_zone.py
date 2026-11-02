"""The time zone this server's clock reads.

Schedules, a script's ``datetime.now()`` and the log's timestamps all read the
process's local time. Windows, the Pi image, a Linux install and a Mac run in
the computer's own zone. A host that runs the server on UTC while the site is
somewhere else (appliance hardware, a Docker container started with neither a
TZ nor the host's ``/etc/localtime``) fires an "11:00 PM" schedule hours early,
while the panel's clock, drawn by a browser in the right zone, looks fine.

Two ways in:

- **TZ in the environment at startup** is an operator's choice and wins.
- Otherwise **the panel on this computer's own screen** reports the zone its
  browser runs in (``GET /api/panel/access?time_zone=``, accepted only from
  ``is_local_console_request``). When the server's clock disagrees with that
  zone, the server takes it, saves it beside ``system.json``
  (``time_zone.json``), and applies the saved zone at the next start, before
  the first schedule is read.

The zone is applied process-wide (``TZ`` plus ``time.tzset()``), so schedules,
scripts and logs move together. ``TZ`` is set to the zone's own POSIX rule,
read from the tz database file, rather than its name: the C library resolves a
name through system zone files a minimal host may not have, and treats a name
it cannot find as UTC without a word. The rule needs no files. After applying,
the clock's offsets are checked against the zone; a mismatch is put back.

POSIX only: Windows has no ``tzset`` and always runs in the computer's zone.
"""

from __future__ import annotations

import json
import os
import time
import zoneinfo
from datetime import datetime, timedelta
from pathlib import Path

from openavc.system_config import get_data_dir
from openavc.utils.logger import get_logger

log = get_logger(__name__)

_FILE_NAME = "time_zone.json"

# TZ as the process started: someone set it on purpose (a compose file's TZ
# line), so nothing here overrides it.
_operator_tz: str | None = os.environ.get("TZ") or None
# The zone name this module applied, for display; None until one is applied.
_applied: str | None = None


def _can_apply() -> bool:
    return hasattr(time, "tzset") and _operator_tz is None


def _zone(name: str) -> zoneinfo.ZoneInfo | None:
    """The zone for a tz database name, or None for anything else."""
    if not isinstance(name, str) or not name or len(name) > 64:
        return None
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return None


def _posix_rule(name: str) -> str | None:
    """The zone's POSIX TZ rule: the footer of its TZif file (version 2 on).

    Read from the system's zone files when it has them, else from the
    ``tzdata`` package, the same places ``zoneinfo`` looks.
    """
    data: bytes | None = None
    for base in zoneinfo.TZPATH:
        path = Path(base) / name
        if path.is_file():
            data = path.read_bytes()
            break
    if data is None:
        try:
            from importlib import resources

            data = resources.files("tzdata.zoneinfo").joinpath(*name.split("/")).read_bytes()
        except (ImportError, FileNotFoundError, OSError, ValueError):
            return None
    if not data.startswith(b"TZif") or data[4:5] in (b"\x00", b""):
        return None
    rule = data.rstrip(b"\n").rsplit(b"\n", 1)[-1]
    try:
        text = rule.decode("ascii")
    except UnicodeDecodeError:
        return None
    return text or None


def _clock_follows(zone: zoneinfo.ZoneInfo) -> bool:
    """True when the process's local time has the zone's offsets now and half
    a year from now (so a zone whose summer time differs is not mistaken for
    one that agrees in winter)."""
    now = time.time()
    for t in (now, now + 182 * 86400):
        local = time.localtime(t).tm_gmtoff
        offset = datetime.fromtimestamp(t, zone).utcoffset() or timedelta(0)
        if local != int(offset.total_seconds()):
            return False
    return True


def _set_tz(value: str | None) -> None:
    if value is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = value
    time.tzset()


def _apply(name: str) -> bool:
    """Run the process on this zone. False, with the clock as it was, when the
    zone cannot be applied."""
    global _applied
    zone = _zone(name)
    rule = _posix_rule(name) if zone is not None else None
    if zone is None or rule is None:
        return False
    before = os.environ.get("TZ")
    _set_tz(rule)
    if not _clock_follows(zone):
        _set_tz(before)
        log.warning("Could not put this system's clock on %s; it stays as it was", name)
        return False
    _applied = name
    return True


def _saved_path() -> Path:
    return get_data_dir() / _FILE_NAME


def _save(name: str) -> None:
    path = _saved_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({"name": name}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.warning("Could not save the time zone %s: %s", name, e)


def restore() -> None:
    """At startup: apply the zone a panel on this screen reported before."""
    if not _can_apply():
        return
    try:
        name = json.loads(_saved_path().read_text(encoding="utf-8")).get("name")
    except (OSError, ValueError, AttributeError):
        return
    if isinstance(name, str) and _apply(name):
        log.info("Time zone: %s", name)


def adopt_from_local_screen(name: str) -> bool:
    """The panel on this computer's own screen runs in ``name``. Take it when
    this server's clock disagrees. True when the zone changed."""
    if not _can_apply():
        return False
    zone = _zone(name)
    if zone is None or _clock_follows(zone):
        return False
    if not _apply(name):
        return False
    _save(name)
    log.info("Time zone set to %s, from the panel on this computer's screen", name)
    return True


def current_name() -> str:
    """The zone this server's clock reads, as a person would name it."""
    if _applied:
        return _applied
    if _operator_tz:
        return _operator_tz
    try:
        real = os.path.realpath("/etc/localtime")
        if "/zoneinfo/" in real:
            name = real.split("/zoneinfo/", 1)[1]
            return "UTC" if name in ("Etc/UTC", "UTC", "Etc/Universal", "Zulu") else name
        text = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    return time.tzname[time.localtime().tm_isdst > 0]
