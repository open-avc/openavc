"""THE credential-redaction policy for anything the server logs.

Three doors ask this module the same two questions — *is this config key a
credential?* and *does this text contain a credential value?* — so the answers
cannot drift apart:

  - **Transport TX/RX** (``server/transport/wire_log.py``) formats every byte a
    device sends or receives. Device protocols carry logins in the clear, that
    traffic is pinned to DEBUG into the in-memory ring buffer regardless of the
    configured log level, and the buffer is served by ``GET /api/logs/recent``
    and offered as a file by the Log view's Download. That is the leak this
    module exists to close.
  - **A driver's own log lines** — anything a driver writes itself, which no
    formatter sees. Covered by :class:`SecretRedactionFilter` on the live
    handlers.
  - **The cloud AI's ``get_logs`` tool** (``cloud/tools/system_tools.py``),
    which was the *only* redaction the core had before this module and is now
    a second caller of the same rule rather than a private copy of it.

**Exact-match on values already known to be secret, never pattern-guessing at
the bytes.** A regex hunting for password-shaped text in wire traffic is both
leaky (it cannot know a device's framing) and false-positive-prone. Instead the
runtime *registers* the values it already knows are credentials — the resolved
config of every device, plus any session token a driver hands to
``BaseDriver.redact_in_log()`` — and only those exact strings are masked.

Two details that look like fussiness and are not, both learned from bugs:

  - **Token boundaries.** A plain substring replace mangles unrelated words: an
    SNMP community of ``public`` turned ``republic`` into ``re***``. Boundaries
    are applied per edge, and only when that edge is itself a word character,
    so a secret with punctuation edges (``p@ss!``) still matches wherever it
    really appears.
  - **The hex form.** A binary protocol's login frame is logged as hex, so the
    ASCII form of the password never appears in the line. Each secret is
    therefore also matched as its hex encoding (no boundaries — hex has no word
    edges).

Values shorter than :data:`MIN_SECRET_LEN` are ignored, so a blank or trivial
password cannot blank unrelated log text.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

# Config field names whose values are credentials. Matched case-insensitively:
# exact names for ambiguous words (so a benign `user_label` isn't caught),
# substrings for the unambiguous secret markers. Mirrors the auth fields
# BaseDriver.connect reads (username/password/token/api_key) plus common
# variants.
SECRET_KEY_EXACT = frozenset({"username", "user", "bearer"})
SECRET_KEY_SUBSTRINGS = (
    "password", "passwd", "passphrase", "secret",
    "token", "api_key", "apikey", "credential", "private",
    "community", "auth_key", "system_key", "lock_code",
)

# Values shorter than this are never treated as secrets. A device password of
# "1234" would otherwise mask every unrelated "1234" in the log — and the log is
# the author's debugging tool. Under-redaction is the security bug and
# over-redaction is the usability one; four characters is where the two cross,
# and it is one constant so every door draws the same line.
MIN_SECRET_LEN = 4

REDACTED = "***"

_WORD_EDGE = re.compile(r"\w")


def is_secret_key(key: Any) -> bool:
    """True when a config field *name* marks its value as a credential."""
    key_l = str(key).lower()
    return key_l in SECRET_KEY_EXACT or any(
        marker in key_l for marker in SECRET_KEY_SUBSTRINGS
    )


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a device config with credential values masked.

    A present-but-masked value (``"***"``) still tells a caller the field is
    set without revealing it; empty/None values are left as-is so "not
    configured" stays visible. Nested dicts are redacted recursively.
    """
    redacted: dict[str, Any] = {}
    for key, value in config.items():
        if isinstance(value, dict):
            redacted[key] = redact_config(value)
            continue
        redacted[key] = (
            REDACTED if (is_secret_key(key) and value not in (None, "")) else value
        )
    return redacted


def collect_secret_values(
    config: dict[str, Any] | None,
    config_schema: dict[str, Any] | None = None,
) -> set[str]:
    """Every credential *value* in one device's resolved config.

    Two sources, per the redaction decision: fields the driver explicitly
    declares ``secret: true`` in its ``config_schema``, and fields whose name
    matches the conventional credential names above. A driver that marks a
    field secret gets it redacted even when the field is called something the
    name list would never guess (``site_code``, ``pin``).
    """
    if not isinstance(config, dict):
        return set()

    declared_secret: set[str] = set()
    if isinstance(config_schema, dict):
        for field, spec in config_schema.items():
            if isinstance(spec, dict) and spec.get("secret") is True:
                declared_secret.add(str(field))

    values: set[str] = set()
    for key, value in config.items():
        if isinstance(value, dict):
            values |= collect_secret_values(value)
            continue
        if not isinstance(value, str) or len(value) < MIN_SECRET_LEN:
            continue
        if str(key) in declared_secret or is_secret_key(key):
            values.add(value)
    return values


def _variants(secret: str) -> list[tuple[str, bool]]:
    """A secret's matchable forms: the literal, and its hex encoding.

    Returns ``(text, use_word_boundaries)`` pairs. Hex never gets boundaries —
    every hex character is a word character, so a boundary in the middle of a
    hex dump can never match.
    """
    forms: list[tuple[str, bool]] = [(secret, True)]
    try:
        hexed = secret.encode("utf-8").hex()
    except (UnicodeEncodeError, AttributeError):
        return forms
    if hexed and hexed != secret:
        forms.append((hexed, False))
    return forms


def compile_secret_pattern(secrets: Iterable[str]) -> re.Pattern[str] | None:
    """Compile one alternation matching every form of every secret.

    One compiled pattern rather than a substitution per secret: the log filter
    runs on every record the server emits, and a per-secret pass over a fleet's
    worth of credentials would be paid on every line. Longest form first so a
    secret that is a substring of another is masked whole.
    """
    forms: list[tuple[str, bool]] = []
    for secret in secrets:
        if not isinstance(secret, str) or len(secret) < MIN_SECRET_LEN:
            continue
        forms.extend(_variants(secret))
    if not forms:
        return None

    alternatives = []
    for text, bounded in sorted(forms, key=lambda f: len(f[0]), reverse=True):
        left = r"\b" if bounded and _WORD_EDGE.match(text[0]) else ""
        right = r"\b" if bounded and _WORD_EDGE.match(text[-1]) else ""
        alternatives.append(left + re.escape(text) + right)
    return re.compile("|".join(alternatives))


def redact_text(text: str, secrets: Iterable[str]) -> str:
    """Mask every known secret value in ``text``.

    The one-shot form, for callers that redact a batch and throw the pattern
    away (the cloud ``get_logs`` tool). Hot paths compile once and keep the
    pattern — see :class:`SecretRegistry`.
    """
    pattern = compile_secret_pattern(secrets)
    if pattern is None:
        return text
    return pattern.sub(REDACTED, text)


class SecretRegistry:
    """Every credential value the running instance knows about, by device.

    Two sets per device, kept apart on purpose: values derived from the device's
    config (replaced wholesale whenever the config is re-resolved, so an edited
    password does not leave the old one registered forever) and values a driver
    registered at runtime via ``redact_in_log`` (session tokens the device
    issued, which config cannot know and a reconnect must not drop).

    Compiled patterns are cached per device and for the union, and invalidated
    on every mutation.
    """

    def __init__(self) -> None:
        self._config: dict[str, set[str]] = {}
        self._runtime: dict[str, set[str]] = {}
        self._device_patterns: dict[str, re.Pattern[str] | None] = {}
        self._all_pattern: re.Pattern[str] | None = None
        self._all_dirty = True

    def set_config_secrets(self, device_id: str, values: Iterable[str]) -> None:
        kept = {v for v in values if isinstance(v, str) and len(v) >= MIN_SECRET_LEN}
        if kept:
            self._config[device_id] = kept
        else:
            self._config.pop(device_id, None)
        self._invalidate(device_id)

    def add_runtime_secret(self, device_id: str, value: str) -> None:
        if not isinstance(value, str) or len(value) < MIN_SECRET_LEN:
            return
        self._runtime.setdefault(device_id, set()).add(value)
        self._invalidate(device_id)

    def forget(self, device_id: str) -> None:
        self._config.pop(device_id, None)
        self._runtime.pop(device_id, None)
        self._invalidate(device_id)

    def clear(self) -> None:
        self._config.clear()
        self._runtime.clear()
        self._device_patterns.clear()
        self._all_pattern = None
        self._all_dirty = True

    def secrets_for(self, device_id: str) -> set[str]:
        return self._config.get(device_id, set()) | self._runtime.get(device_id, set())

    def all_secrets(self) -> set[str]:
        values: set[str] = set()
        for group in (self._config, self._runtime):
            for entry in group.values():
                values |= entry
        return values

    def redact_for(self, device_id: str, text: str) -> str:
        """Mask one device's secrets in ``text`` (the TX/RX formatter's path)."""
        if device_id not in self._device_patterns:
            self._device_patterns[device_id] = compile_secret_pattern(
                self.secrets_for(device_id)
            )
        pattern = self._device_patterns[device_id]
        return text if pattern is None else pattern.sub(REDACTED, text)

    def redact_any(self, text: str) -> str:
        """Mask any registered device's secrets (the log filter's path)."""
        if self._all_dirty:
            self._all_pattern = compile_secret_pattern(self.all_secrets())
            self._all_dirty = False
        pattern = self._all_pattern
        return text if pattern is None else pattern.sub(REDACTED, text)

    def has_secrets(self) -> bool:
        return bool(self._config or self._runtime)

    def _invalidate(self, device_id: str) -> None:
        self._device_patterns.pop(device_id, None)
        self._all_dirty = True


_registry = SecretRegistry()


def get_secret_registry() -> SecretRegistry:
    """The process-wide registry every redaction door reads."""
    return _registry


class SecretRedactionFilter(logging.Filter):
    """Mask registered credential values in any record reaching a handler.

    The backstop for log lines the transport formatter never sees — anything a
    driver, a script or a plugin writes itself. Attached to the live handlers
    (console, file, in-memory buffer) rather than to a logger, because a filter
    on a logger does not see records propagating up from its children.

    Idempotent: the same record passing through three handlers is redacted once
    and the later passes are no-ops.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        registry = get_secret_registry()
        if not registry.has_secrets():
            return True
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = registry.redact_any(message)
        if redacted != message:
            # Collapse to the redacted string; args are already interpolated in.
            record.msg = redacted
            record.args = ()
        return True
