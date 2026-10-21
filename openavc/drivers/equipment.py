"""OpenAVC equipment protocol client for manufacturer-described capabilities."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextlib
import copy
import hashlib
import json
import os
import ssl
import sqlite3
import time
import uuid

import aiohttp
from yarl import URL

from openavc.drivers.base import (
    BaseDriver,
    ConnectionFaultError,
    normalize_and_validate_command_params,
    validate_device_setting_value,
)
from openavc.drivers.command_outcome import CommandOutcomeError
from openavc.drivers.child_ids import coerce_child_local_id
from openavc.drivers.python_info import python_driver_info_issues
from openavc.drivers.spec import is_file_command
from openavc.drivers.equipment_protocol import MAX_MESSAGE_BYTES, PROTOCOL
from openavc.system_config import get_data_dir
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# How long a command waits, unless the device says otherwise, for the
# equipment to confirm it finished. The equipment answers within a fraction of
# a second either way and keeps the outcome under the same request ID, which is
# read every _RESULT_POLL_S until it is final.
COMPLETION_WAIT_DEFAULT = 5
COMPLETION_WAIT_MAX = 600
_RESULT_POLL_S = 0.1
# A reply body that is not JSON, told apart from a JSON ``null``.
_UNREADABLE = object()


class RequestJournal:
    """Portable local-writer lease and committed request sequence.

    The held transaction is in a separate database: reserving an ID can commit
    without releasing the driver's lifetime lease. This is only a single-host
    guard, not remote-controller authority or power-loss qualification.
    """

    MAX_ID = 9007199254740991

    def __init__(self, folder, stem):
        self.lease = self.database = None
        try:
            self.lease = sqlite3.connect(folder / (stem + '.lease.sqlite3'), timeout=0, isolation_level=None)
            self.lease.execute('BEGIN EXCLUSIVE')
            path = folder / (stem + '.requests.sqlite3')
            existed = path.exists()
            self.database = sqlite3.connect(path, timeout=0, isolation_level=None)
            self.database.execute('PRAGMA journal_mode=DELETE')
            self.database.execute('PRAGMA synchronous=FULL')
            self.database.execute('BEGIN IMMEDIATE')
            if not existed:
                self.database.execute('CREATE TABLE requests (slot INTEGER PRIMARY KEY CHECK(slot=1), client TEXT NOT NULL, highest INTEGER NOT NULL, session TEXT NOT NULL)')
                self.database.execute('INSERT INTO requests VALUES (1, ?, 0, ?)', (uuid.uuid4().hex, ''))
            rows = self.database.execute('SELECT client, highest FROM requests').fetchall()
            if len(rows) != 1:
                raise ValueError('The device request journal is incomplete.')
            self.client, highest = rows[0]
            if (not isinstance(self.client, str) or len(self.client) != 32
                    or any(c not in '0123456789abcdef' for c in self.client)
                    or type(highest) is not int or not 0 <= highest <= self.MAX_ID):
                raise ValueError('The device request journal is invalid.')
            self.database.execute('COMMIT')
        except BaseException:
            self.close()
            raise

    def reserve(self, session):
        if self.database is None or self.lease is None:
            raise RuntimeError('The device request journal is closed.')
        try:
            self.database.execute('BEGIN IMMEDIATE')
            row = self.database.execute('SELECT client, highest FROM requests WHERE slot=1').fetchone()
            if row is None or row[0] != self.client or type(row[1]) is not int or not 0 <= row[1] <= self.MAX_ID:
                raise ValueError('The device request journal changed or is invalid.')
            identifier = row[1] + 1
            if identifier > self.MAX_ID:
                raise ValueError('The command counter has reached its limit. No command was sent.')
            self.database.execute('UPDATE requests SET highest=?, session=? WHERE slot=1', (identifier, session))
            self.database.execute('COMMIT')
            return identifier
        except BaseException:
            if self.database.in_transaction:
                self.database.execute('ROLLBACK')
            raise

    def close(self):
        for name in ('database', 'lease'):
            connection = getattr(self, name, None)
            if connection is not None:
                connection.close()
                setattr(self, name, None)


class AsyncRequestJournal:
    """Keep SQLite and its lifetime lease on one worker, off the event loop.

    Cancellation consumes an in-progress reservation but never sends it. Close
    queues behind that reservation, so the lease outlives every pending write.
    """

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='equipment-journal')
        self._journal = None
        self._close_future = None

    @staticmethod
    def _observe(future):
        # A cancelled caller no longer awaits the worker's eventual exception.
        if not future.cancelled():
            future.exception()

    def _submit(self, operation, *args):
        if self._executor is None:
            raise RuntimeError('The device request journal is closed.')
        future = asyncio.wrap_future(self._executor.submit(operation, *args))
        future.add_done_callback(self._observe)
        return future

    def _open(self, folder, stem):
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._journal = RequestJournal(folder, stem)
        return self._journal.client

    async def open(self, folder, stem):
        return await asyncio.shield(self._submit(self._open, folder, stem))

    async def reserve(self, session):
        return await asyncio.shield(self._submit(self._journal.reserve, session))

    def _close(self):
        if self._journal is not None:
            self._journal.close()
            self._journal = None

    async def close(self):
        if self._close_future is None:
            self._close_future = self._submit(self._close)
            # Queued work finishes in order; shutting down never waits on disk
            # in the event-loop thread, even if this close caller is cancelled.
            self._executor.shutdown(wait=False)
            self._executor = None
        await asyncio.shield(self._close_future)


class EquipmentDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "openavc_equipment",
        "name": "OpenAVC built-in product",
        "manufacturer": "OpenAVC",
        "category": "utility",
        "version": "0.0.1",
        "author": "OpenAVC",
        "description": "A manufacturer's product with OpenAVC built in.",
        "help": {
            "overview": "A manufacturer's product with OpenAVC built in.",
            "setup": "Enter its address and the username and password its documentation gives.",
        },
        "source_url": "https://github.com/open-avc/openavc",
        "transport": "http",
        "commands": {},
        "state_variables": {
            "_sdk_identity": {"type": "string", "label": "Equipment ID"},
            "_sdk_model": {"type": "string", "label": "Equipment model"},
        },
        "default_config": {"host": "", "port": 9443, "username": "", "password": "", "verify_ssl": True,
                           "completion_wait": COMPLETION_WAIT_DEFAULT},
        "config_schema": {
            "reported_identity": {"type": "string", "label": "Equipment ID", "default": "",
                         "advanced": True, "learned_from": "_sdk_identity"},
            "reported_model": {"type": "string", "label": "Equipment model", "default": "",
                            "advanced": True, "learned_from": "_sdk_model"},
            "host": {"type": "string", "label": "Host", "default": "", "required": True},
            "port": {"type": "integer", "label": "Port", "default": 9443},
            "username": {"type": "string", "label": "Username", "default": "", "required": True},
            "password": {"type": "string", "label": "Password", "default": "", "secret": True,
                         "required": True},
            "verify_ssl": {"type": "boolean", "label": "Verify HTTPS certificate", "default": True,
                           "advanced": True, "description": "Verify the equipment's certificate against trusted certificate authorities."},
            "completion_wait": {"type": "number", "label": "Wait for completion (seconds)",
                                "default": COMPLETION_WAIT_DEFAULT, "min": 0, "max": COMPLETION_WAIT_MAX,
                                "advanced": True,
                                "description": "How long a command waits for the product to confirm it finished. "
                                               "A command that takes longer is reported as not confirmed."},
        },
        "discovery": {"mdns": [{"service": "_openavc-device._tcp.local."}]},
    }

    @classmethod
    def discovered_control_port(cls, evidence):
        """Read the equipment control port from its matching discovery record."""
        ports = set()
        for item in evidence:
            data = item.get("data", {})
            if data.get("kind") == "mdns" and data.get("source_id") == "_openavc-device._tcp.local.":
                port = data.get("port")
                if type(port) is int and 1 <= port <= 65535:
                    ports.add(port)
        if len(ports) == 1:
            return ports.pop()
        if ports:
            raise ValueError("More than one equipment control port was discovered. Add the product manually using its configured port.")
        raise ValueError("The equipment control port was not discovered. Scan again or add the product manually using its configured port.")

    async def connect(self):
        # Automatic retry can enter without an intervening disconnect().
        # Close the old HTTP session and lease before replacing their handles.
        await self.disconnect()
        self._last_fault = None
        self._sdk_poll_task = None
        self._sdk_http = None
        self._sdk_journal = None
        # Reconcile the first new snapshot against the previous roster too.
        # Resetting this set leaves removed channels registered after reconnect.
        self._sdk_children = getattr(self, '_sdk_children', set())
        self._sdk_command_lock = asyncio.Lock()
        try:
            # Single-host writer lifetime, not a distributed authority protocol.
            folder = get_data_dir() / "equipment-requests"
            local = "token" in self.config
            if not local:
                host = self.config.get("host", "")
                missing = [word for key, word in (("host", "host"), ("username", "username"),
                                                  ("password", "password"))
                           if not self.config.get(key)]
                if missing:
                    # What is missing, not where to enter it: the device
                    # page's banner gives that step under a refused login.
                    words = missing[0] if len(missing) == 1 else (
                        ", ".join(missing[:-1]) + " and " + missing[-1])
                    verb = "is" if len(missing) == 1 else "are"
                    raise ConnectionFaultError(
                        f"This product's {words} {verb} not set.",
                        code="auth_failed",
                    )
                self.config["url"] = str(URL.build(scheme="https", host=host, port=int(self.config.get("port", 9443))))
            # The platform can redirect the host/port for simulation. Keep
            # the permanent endpoint intact so stopping simulation restores it.
            self._sdk_url = self.config["url"]
            if local and self.config.get("host") and self.config.get("port"):
                self._sdk_url = str(URL.build(scheme="https", host=self.config["host"], port=int(self.config["port"])))
            stem = hashlib.sha256((self.config["identity"] if local else self.config["url"]).encode()).hexdigest()
            self._sdk_journal = AsyncRequestJournal()
            try:
                client = await self._sdk_journal.open(folder, stem)
            except sqlite3.OperationalError as exc:
                if getattr(exc, 'sqlite_errorcode', None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise ConnectionFaultError('Equipment is already open in another local connection. Close that connection before retrying.', code='connection_refused') from exc
                raise
            tls = (ssl.create_default_context(cafile=self.config.get("ca_file", os.environ.get("SSL_CERT_FILE")))
                   if local or self.config.get("verify_ssl", True) else False)
            headers = {"X-OpenAVC-Client": client}
            if local:
                headers["Authorization"] = "Bearer " + self.config["token"]
            # A product reached over the network leaves through the adapter
            # Reach devices through chose; the local service is the product's own.
            bind = None if local else self.local_address()
            self._sdk_http = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=tls, local_addr=(bind, 0) if bind else None),
                headers=headers,
                auth=None if local else aiohttp.BasicAuth(self.config["username"], self.config["password"]),
                timeout=aiohttp.ClientTimeout(total=2),
            )
            description = await self._request("/v1/description")
            if (
                description["protocol"] != PROTOCOL
                or (self.config.get("identity") and description["identity"] != self.config["identity"])
            ):
                raise ValueError(
                    "Endpoint identity or protocol does not match the installed product."
                )
            self.config["identity"] = description["identity"]
            self._sdk_identity = description["identity"]
            info = description["capabilities"]
            if local and self.config.get("integration") != info.get("id"):
                raise ConnectionFaultError(
                    "The product hardware model does not match the product firmware configuration.",
                    code="invalid_config",
                )
            issues = python_driver_info_issues(info, overrides_set_device_setting=True)
            # The equipment protocol carries a command's params as JSON values,
            # so a model cannot declare a command that takes or hands back a
            # file (a Python driver's CommandFile / DeviceFile).
            issues += [
                f"Command '{name}' takes or hands back a file, which equipment cannot declare"
                for name, cmd in (info.get("commands") or {}).items()
                if is_file_command(cmd)
            ]
            if issues:
                raise ValueError("Invalid endpoint model: " + "; ".join(issues))
            if {"_sdk_identity", "_sdk_model"} & set(info.get("state_variables", {})):
                raise ValueError("Endpoint model uses reserved SDK state names.")
            self._sdk_model = info["id"]
            # Registration belongs to the host; remote model describes behavior.
            self.DRIVER_INFO = {
                **info,
                "id": type(self).DRIVER_INFO["id"],
                "config_schema": {} if local else type(self).DRIVER_INFO["config_schema"],
                "state_variables": {
                    **info.get("state_variables", {}),
                    "_sdk_identity": {"type": "string", "label": "Equipment ID"},
                    "_sdk_model": {"type": "string", "label": "Equipment model"},
                },
            }
            self._sdk_session = description["session"]
            await self._refresh()
            await self.events.emit(f"device.connected.{self.device_id}")
            self._sdk_poll_task = asyncio.create_task(self._poll())
        except BaseException:
            await self.disconnect()
            raise

    async def _request(self, path, payload=None):
        async with self._sdk_http.request(
            "POST" if payload is not None else "GET",
            self._sdk_url + path,
            json=payload,
        ) as response:
            data = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                if len(data) + len(chunk) > MAX_MESSAGE_BYTES:
                    raise ValueError("Equipment response exceeds the 1 MiB protocol limit.")
                data.extend(chunk)
            try:
                value = json.loads(data)
            except ValueError:
                # Not the product's own reply: an error page from a web
                # server or proxy at this address. Its parser text means
                # nothing on a device card.
                value = _UNREADABLE
            body = value if isinstance(value, dict) else {}
            message = body.get("message") if isinstance(body.get("message"), str) else ""
            if response.status == 401:
                raise ConnectionFaultError(message or "The product refused the username and password.", code="auth_failed")
            if response.status == 403:
                raise ConnectionFaultError(message or "Device access is disabled in product settings.", code="connection_refused")
            if response.status == 404 and path.startswith("/v1/commands/"):
                return None
            if (response.status == 409 and path == "/v1/command"
                    and isinstance(payload, dict) and isinstance(value, dict)
                    and type(value.get("id")) is int and value["id"] == payload.get("id")
                    and value.get("outcome") in {"failed", "rejected"}):
                return value
            if response.status >= 400:
                raise ValueError(message or f"The product refused the request (HTTP {response.status}).")
            if value is _UNREADABLE:
                raise ValueError(
                    "The reply from this address is not the product's. "
                    "Check that the host and port are the product's."
                )
            return value

    async def _refresh(self):
        snapshot = await self._request("/v1/snapshot")
        if snapshot["identity"] != self._sdk_identity:
            raise ValueError(
                "The product identity changed. Recommission the replacement."
            )
        if snapshot["session"] != self._sdk_session:
            # A new session can describe different firmware or capabilities.
            # Reconnect through description validation before accepting it.
            self._sdk_session = None
            self._connected = False
            raise ConnectionError("The equipment restarted. Reconnecting to read its current capabilities.")
        wanted = {(c["type"], c["id"]) for c in snapshot["children"]}
        for kind, ident in self._sdk_children - wanted:
            self.deregister_child(kind, ident)
        for child in snapshot["children"]:
            kind, ident, state = child["type"], child["id"], child["state"]
            self.register_child(kind, ident, initial_state=state)
            self.set_child_state_batch(kind, ident, state)
        self._sdk_children = wanted
        self.set_states(snapshot["state"])
        self.set_state("_sdk_identity", self._sdk_identity)
        self.set_state("_sdk_model", self._sdk_model)
        self._connected = True
        self.set_state("connected", True)

    async def _poll(self):
        while True:
            await asyncio.sleep(0.1)
            try:
                await self._refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._stash_fault(getattr(exc, "fault_code", "transport_disconnected"), str(exc))
                self._connected = False
                self.set_state("connected", False)
                self.set_states(
                    {
                        key: "unavailable" if key == "quality" else None
                        for key in self.DRIVER_INFO.get("state_variables", {})
                    }
                )
                for kind, ident in self._sdk_children:
                    self.set_child_state_batch(
                        kind,
                        ident,
                        {
                            key: None
                            for key in self.DRIVER_INFO["child_entity_types"][kind].get(
                                "state_variables", {}
                            )
                        },
                    )
                await self.events.emit(f"device.disconnected.{self.device_id}")
                return

    async def _reserve_id(self, session):
        # Commit before sending. Lost replies consume their ID, never replay.
        if session is None:
            raise ConnectionError('The equipment connection must be restored before sending commands.')
        journal = self._sdk_journal
        identifier = await journal.reserve(session)
        if self._sdk_journal is not journal or self._sdk_session != session:
            raise ConnectionError('The device connection changed before the command was sent.')
        return identifier

    async def send_command(self, command, params=None):
        rules = self.DRIVER_INFO["commands"][command].get("params", {})
        values = normalize_and_validate_command_params(command, rules, params or {})
        normalized = {}
        for key, value in values.items():
            rule = rules.get(key, {})
            if value is None and not rule.get("required"):
                continue
            kind = rule.get("type")
            if kind == "child_id":
                child = self.DRIVER_INFO.get("child_entity_types", {}).get(rule.get("child_type"), {})
                value = coerce_child_local_id(child, value)
                if value is None:
                    raise ValueError(f"'{command}': '{key}' is not a valid channel ID")
            elif kind in {"boolean", "integer", "number", "enum"}:
                value = validate_device_setting_value(key, rule, value)
                if kind == "enum":
                    choices = [item.get("value") if isinstance(item, dict) else item
                               for item in rule.get("values", [])]
                    value = next((item for item in choices if str(item) == value), value)
            normalized[key] = value
        async with self._sdk_command_lock:
            session = self._sdk_session
            identifier = await self._reserve_id(session)
            result = await self._request(
                "/v1/command",
                {
                    "session": session,
                    "id": identifier,
                    "command": command,
                    "params": normalized,
                },
            )
        # The lock orders request IDs, so it is released before waiting: a
        # second command is sent while the first finishes.
        if result["outcome"] in {"pending", "uncertain"}:
            result = await self._await_outcome(session, identifier)
        if result["outcome"] in {"failed", "rejected", "pending", "uncertain"}:
            raise CommandOutcomeError(result["outcome"])
        if result["outcome"] != "completed":
            raise RuntimeError("The product returned an unsupported command outcome.")
        # The command is done; this refresh only shows its effect sooner. If
        # the equipment restarted or the link dropped in between, the poll
        # meets the same thing and reconnects, and the outcome still stands.
        try:
            await self._refresh()
        except Exception as exc:
            log.info(
                f"[{self.device_id}] '{command}' completed; the update after it "
                f"waits for the next poll: {exc}"
            )
        return result

    def _completion_wait(self):
        try:
            wait = float(self.config.get("completion_wait", COMPLETION_WAIT_DEFAULT))
        except (TypeError, ValueError):
            return COMPLETION_WAIT_DEFAULT
        if wait != wait:
            return COMPLETION_WAIT_DEFAULT
        return min(max(wait, 0.0), COMPLETION_WAIT_MAX)

    async def _await_outcome(self, session, identifier):
        """Read a command's outcome until it is final or the device's wait runs out.

        Never sends the command again. A new equipment session drops every
        outcome it held, and so does a lost connection, so either ends the
        wait as unconfirmed, as does an outcome the equipment no longer holds.
        """
        wait = self._completion_wait()
        deadline = time.monotonic() + wait
        http = self._sdk_http
        while (remaining := deadline - time.monotonic()) > 0:
            await asyncio.sleep(min(_RESULT_POLL_S, remaining))
            if self._sdk_session != session or self._sdk_http is not http:
                raise CommandOutcomeError("uncertain")
            try:
                result = await self._request(f"/v1/commands/{identifier}")
            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError, ValueError):
                continue
            if not isinstance(result, dict) or result.get("id") != identifier:
                raise CommandOutcomeError("uncertain")
            if result.get("outcome") != "pending":
                return result
        raise CommandOutcomeError("uncertain", waited=wait)

    async def set_device_setting(self, key, value):
        write = copy.deepcopy(self.DRIVER_INFO["device_settings"][key]["write"]["body"])
        params = {
            key: value if item == "{value}" else item
            for key, item in write["params"].items()
        }
        return await self.send_command(write["command"], params)

    async def disconnect(self):
        task = getattr(self, "_sdk_poll_task", None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._sdk_poll_task = None
        if getattr(self, "_sdk_http", None):
            await self._sdk_http.close()
            self._sdk_http = None
        if getattr(self, "_sdk_journal", None):
            journal = self._sdk_journal
            self._sdk_journal = None
            await journal.close()
        self._connected = False
        self.set_state("connected", False)
