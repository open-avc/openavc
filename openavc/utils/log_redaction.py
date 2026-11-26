"""THE credential-redaction policy for anything the server logs.

Three doors ask this module the same two questions — *is this config key a
credential?* and *does this text contain a credential value?* — so the answers
cannot drift apart:

  - **Transport TX/RX** (``openavc/transport/wire_log.py``) formats every byte a
    device sends or receives. Device protocols carry logins in the clear, that
    traffic is pinned to DEBUG into the in-memory ring buffer regardless of the
    configured log level, and the buffer is served by ``GET /api/logs/recent``
    and offered as a file by the Log view's Download. That is the leak this
    module exists to close.
  - **A driver's own log lines** — anything a driver writes itself, which no
    formatter sees. Covered by :class:`SecretRedactionFilter` on the live
    handlers.
  - **The cloud AI's ``get_logs`` tool** (``cloud/tools/system_tools.py``),
    which once gathered its own list and now asks the registry, as every
    other exit does.

**One registry of every secret the system holds** (:class:`SecretRegistry`),
fed where each comes from rather than gathered by each exit: a device's
config as its driver is built (:func:`register_config_secrets`, kept until
its instance has disconnected), a token or a secret parameter as it is
learned, the project's own and every secret it holds in the clear each time
the project is replaced, and this system's own at the start and on pairing
(:func:`register_system_secrets`).

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

**A username is masked in its own device's traffic only.** It names an account
rather than unlocking one, and it is often a plain word ("admin"), so masking
it in every line hid that word all over the log. It is still masked where the
login goes over the wire (that device's TX/RX lines and its traffic view) and
wherever a config is shown by field (:func:`redact_config`).

**What a secret is.** One definition, asked by every reader (the logs, the
traffic recorder, problem reports, device audits, the login file, the space
trimming, learned settings, the driver validator):

  - a device's setting, by :func:`~openavc.drivers.spec.is_secret_setting`:
    the connection ``password``, and any field the driver marks
    ``secret: true`` or ``type: password``, whatever it is called. It lives in
    the driver contract because the driver validator asks it too; the
    Programmer mirrors it (``secretSetting.ts``). A command's parameter is
    judged by the same rule on the device's own declaration
    (``drivers/secret_params``);
  - the project's own: :data:`PROJECT_SECRET_FIELDS`, the mesh key.

**One collector of a device's secret values** (:func:`secret_values`), which
the registry, the Device Audit and a problem report all read: a secret is
read as its text whatever type it holds (:func:`secret_text`), so a PIN a
driver typed as a number is masked as its digits.

**The name rule** (:func:`is_secret_key`) is the outbound redactors' net and
nothing more: the logs, the traffic recorder's state lines, the Device Audit,
a problem report and the cloud AI's log tool also mask a value named like a
credential, since what they send away may hold one nothing marks (a plugin's
config, state, a field the driver forgot to mark). It never decides what is
a secret: a parameter named ``pin`` or ``token`` that its driver does not mark
is not saved on this system nor filled in from it, and the Programmer shows it
as typed. One reader keeps a value by it: the login file, for a device whose
driver is not installed (``device_logins.login_settings_in``), where nothing
else can say which of its settings are logins.

A report that records one device's settings (the device audit's connection)
asks :func:`secret_config_keys` which of them hold a credential, by name and
by the driver's own declaration, and shows them with :func:`mask_secret_fields`.

**Where a secret may go** is ``utils/secret_audience``'s table, not this
module's: an exit reads the registered values through
:meth:`SecretRegistry.masked` or :meth:`SecretRegistry.to_mask`, naming
itself, and gets nothing masked only where that table says it may carry a
secret.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable
from urllib.parse import quote, quote_plus

from openavc.drivers.spec import PASSWORD_FIELD, secret_settings
from openavc.utils.secret_audience import Exit, may_carry_secret

# Field names that look like a credential's: the outbound redactors' net
# (module docstring, "The name rule"), never what makes a value a secret.
# Matched case-insensitively:
# exact names for ambiguous words (so a benign `user_label` isn't caught, nor a
# command's `send_key` or a setting's `state_key`), substrings for the
# unambiguous secret markers. Mirrors the auth fields BaseDriver.connect reads
# (username/password/token/api_key) plus common variants: a pre-shared key, a
# PIN, and the keys a device hands out or a stream signs in with.
SECRET_KEY_EXACT = frozenset({
    "username", "user", "bearer", "psk", "pin", "key", "app_key", "client_key",
    "session_key", "stream_key", "display_key", "access_key", "pre_shared_key",
})
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


# Credential field names whose values name an account rather than unlock it:
# masked in the device's own traffic, not in every log line (module docstring).
IDENTITY_KEYS = frozenset({"username", "user"})


def is_secret_key(key: Any) -> bool:
    """True when a field *name* looks like a credential's: what the outbound
    redactors mask on top of the declared secrets. Not what decides a secret
    (``drivers/spec.is_secret_setting``, ``drivers/secret_params``)."""
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


# The password in a URL written scheme://user:password@host. The username stays.
_URL_PASSWORD = re.compile(
    r"(?P<head>\b[a-z][a-z0-9+.\-]*://[^\s/@:]*:)(?P<password>[^\s/@]+)(?=@)",
    re.IGNORECASE,
)


def _named_secret(key: Any) -> bool:
    return is_secret_key(key) and str(key).lower() not in IDENTITY_KEYS


def mask_url_password(text: str) -> str:
    """``text`` with the password of every URL written
    ``scheme://user:password@host`` shown as :data:`REDACTED`; the username and
    the rest of the address stay."""
    return _URL_PASSWORD.sub(lambda m: m.group("head") + REDACTED, text)


def named_secret_state(key: Any, value: Any, *, usernames: bool = False) -> bool:
    """Whether ``value`` is text a driver wrote to state under a credential's
    name (``auth_token``, ``password``: :func:`is_secret_key` on the key's
    last part). A username names an account, so it counts only for an exit
    about its one device (``usernames``, a report)."""
    if not isinstance(value, str) or not value:
        return False
    name = str(key).rsplit(".", 1)[-1]
    return is_secret_key(name) and (usernames or name.lower() not in IDENTITY_KEYS)


def state_value_as(where: Any, key: Any, value: Any) -> Any:
    """THE state value as the exit ``where`` may carry it
    (:mod:`~openavc.utils.secret_audience`).

    Driver state on this system may carry a secret: a camera's stream address
    holds its login so a panel can play it, and is given as it is. Every
    other exit (the log, the cloud's relay, alerts and AI tools) gets a value
    under a credential's name as :data:`REDACTED`
    (:func:`named_secret_state`), and text with the password of any
    ``scheme://user:password@host`` address and every registered secret
    masked. Anything else is returned as it is: a flag, a number, nothing.
    """
    if may_carry_secret(where):
        return value
    if named_secret_state(key, value):
        return REDACTED
    return text_as(where, value)


def text_as(where: Any, text: Any) -> Any:
    """THE text as the exit ``where`` may carry it: as it is where it may
    carry a secret; else with the password of any
    ``scheme://user:password@host`` address and every registered secret
    masked. Anything but text is returned as it is."""
    if may_carry_secret(where) or not isinstance(text, str) or not text:
        return text
    if "@" in text:
        text = mask_url_password(text)
    registry = get_secret_registry()
    return registry.masked(where, text) if registry.has_secrets() else text


def _text_leaves(value: Any, shown: Any) -> Any:
    """``value`` with each text inside it, at any depth, as ``shown`` gives it."""
    if isinstance(value, dict):
        return {key: _text_leaves(item, shown) for key, item in value.items()}
    if isinstance(value, list):
        return [_text_leaves(item, shown) for item in value]
    return shown(value) if isinstance(value, str) and value != REDACTED else value


def config_as(where: Any, config: Any) -> Any:
    """A config nothing declares the secrets of (a plugin's, an orphaned
    device's) as the exit ``where`` may carry it: each field named like a
    credential as :data:`REDACTED` (:func:`mask_secrets_by_name`) and every
    other text as :func:`text_as` gives it. :func:`keep_masked_secrets`
    with the same ``where`` is the way back."""
    if may_carry_secret(where):
        return config
    return _text_leaves(mask_secrets_by_name(config), lambda text: text_as(where, text))


def mask_secrets_by_name(value: Any, _secret: bool = False) -> Any:
    """A copy of a nested config with every credential shown as :data:`REDACTED`.

    For a config whose fields nothing declares secret, such as a plugin's: a
    field named like a credential (:func:`is_secret_key`) at any depth, inside
    groups and lists, and the password of any URL written ``user:password@host``.
    A username stays visible, since it names an account rather than unlocking
    one (:data:`IDENTITY_KEYS`). An empty value stays empty and a yes/no stays
    as it is, so "not set" and a switch like ``password_required`` still read.

    :func:`keep_masked_secrets` is the other half: a value sent back as
    :data:`REDACTED` keeps the one already saved.
    """
    if isinstance(value, dict):
        return {
            key: mask_secrets_by_name(item, _secret or _named_secret(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_secrets_by_name(item, _secret) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if _secret and value != "" and isinstance(value, (str, int, float)):
        return REDACTED
    if isinstance(value, str):
        return mask_url_password(value)
    return value


def _matching_item(item: Any, stored: list, index: int) -> Any:
    """The saved list entry an incoming one stands for: the one with the same
    id when it carries one (a list can be reordered or added to), else the
    entry at the same position."""
    if isinstance(item, dict):
        ids = [
            (key, val) for key, val in item.items()
            if (key == "id" or str(key).endswith("_id"))
            and isinstance(val, (str, int)) and not isinstance(val, bool)
            and val != ""
        ]
        if ids:
            for key, val in ids:
                for candidate in stored:
                    if isinstance(candidate, dict) and candidate.get(key) == val:
                        return candidate
            return None
    return stored[index] if index < len(stored) else None


def keep_masked_secrets(
    incoming: Any, stored: Any, _path: str = "", _secret: bool = False, *, where: Any = None,
) -> tuple[Any, list[str]]:
    """Put back each saved credential that ``incoming`` carries as :data:`REDACTED`.

    The write half of :func:`mask_secrets_by_name`: a caller that read the
    masked copy and sends it back changed means "keep the saved one" wherever
    it still says ``***``, never "set the password to ``***``". Returns the
    config to save and the places (``streams[1].password``) where ``***`` came
    back with no saved value to stand for; a caller refuses the write rather
    than store the marker as somebody's password. With ``where``, a text the
    read showed as :func:`config_as` gave it for that exit (a registered
    secret quoted in a field of its own) keeps the saved text too.
    """
    unresolved: list[str] = []
    if isinstance(incoming, dict):
        saved = stored if isinstance(stored, dict) else {}
        out = {}
        for key, item in incoming.items():
            out[key], missed = keep_masked_secrets(
                item, saved.get(key), f"{_path}.{key}" if _path else str(key),
                _secret or _named_secret(key), where=where,
            )
            unresolved += missed
        return out, unresolved
    if isinstance(incoming, list):
        saved_list = stored if isinstance(stored, list) else []
        out_list = []
        for index, item in enumerate(incoming):
            restored, missed = keep_masked_secrets(
                item, _matching_item(item, saved_list, index),
                f"{_path}[{index}]", _secret, where=where,
            )
            out_list.append(restored)
            unresolved += missed
        return out_list, unresolved
    if not isinstance(incoming, str) or REDACTED not in incoming:
        return incoming, unresolved
    if incoming == REDACTED and _secret:
        if (
            isinstance(stored, (str, int, float))
            and not isinstance(stored, bool)
            and stored not in ("", REDACTED)
        ):
            return stored, unresolved
        return incoming, [_path]
    if where is not None and isinstance(stored, str) and text_as(where, stored) == incoming:
        return stored, unresolved
    masked_url = any(
        m.group("password") == REDACTED for m in _URL_PASSWORD.finditer(incoming)
    )
    if not masked_url:
        return incoming, unresolved
    if isinstance(stored, str) and mask_url_password(stored) == incoming:
        return stored, unresolved
    # The address changed around a masked password: the saved one cannot be
    # known to belong to the new address.
    return incoming, [_path]


def typed_secrets(
    incoming: Any, stored: Any, _path: str = "", _secret: bool = False,
) -> list[str]:
    """The places (``streams[1].password``) where ``incoming`` carries a
    credential that ``stored`` does not: a field named like one whose value
    changed, or a URL whose ``user:password@`` password changed.

    For a caller that takes no login typed into a conversation. A value sent
    back as :data:`REDACTED`, the saved-login marker, an empty value and the
    stored value unchanged are not typed logins; the walk matches list entries
    the way :func:`keep_masked_secrets` does.
    """
    found: list[str] = []
    if isinstance(incoming, dict):
        saved = stored if isinstance(stored, dict) else {}
        for key, item in incoming.items():
            found += typed_secrets(
                item, saved.get(key), f"{_path}.{key}" if _path else str(key),
                _secret or _named_secret(key),
            )
        return found
    if isinstance(incoming, list):
        saved_list = stored if isinstance(stored, list) else []
        for index, item in enumerate(incoming):
            found += typed_secrets(
                item, _matching_item(item, saved_list, index), f"{_path}[{index}]", _secret,
            )
        return found
    if isinstance(incoming, bool) or incoming is None or incoming == stored:
        return found
    if _secret and isinstance(incoming, (str, int, float)):
        from openavc.core.device_logins import SAVED_LOGIN

        if incoming not in ("", REDACTED, SAVED_LOGIN):
            found.append(_path)
        return found
    if isinstance(incoming, str):
        before = {
            m.group("password") for m in _URL_PASSWORD.finditer(stored)
        } if isinstance(stored, str) else set()
        if any(
            m.group("password") not in (REDACTED, *before)
            for m in _URL_PASSWORD.finditer(incoming)
        ):
            found.append(_path)
    return found


def _declared_secret(config_schema: dict[str, Any] | None) -> set[str]:
    return set(secret_settings(config_schema))


def secret_config_keys(
    config: dict[str, Any], config_schema: dict[str, Any] | None = None,
) -> set[str]:
    """The keys of ``config`` that hold a credential: a secret by the driver's
    ``config_schema`` (:func:`~openavc.drivers.spec.is_secret_setting`), or
    named like one (:func:`is_secret_key`, so a username too). Top-level keys
    only."""
    declared = _declared_secret(config_schema)
    return {k for k in config if k in declared or is_secret_key(k)}


def mask_secret_fields(config: dict[str, Any], secret_keys: set[str]) -> dict[str, Any]:
    """A copy of ``config`` with each set value under ``secret_keys`` shown as
    :data:`REDACTED`; an empty one stays empty, so "not set" still shows."""
    return {
        key: (REDACTED if key in secret_keys and value not in (None, "") else value)
        for key, value in config.items()
    }


def secret_text(value: Any) -> str | None:
    """THE reading of a secret value as the masking matches it: text as it
    is, a number as its digits; None for anything else (a flag, nothing, a
    list or a mapping). A PIN a driver typed as a number before a secret had
    to be text reaches the wire as its digits, so it is masked as them."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def secret_values(
    config: dict[str, Any] | None,
    config_schema: dict[str, Any] | None = None,
    *,
    floor: int = MIN_SECRET_LEN,
    usernames: bool = False,
) -> set[str]:
    """THE collector of one device's secret values, from its resolved config.

    A field that holds a secret by
    :func:`~openavc.drivers.spec.is_secret_setting` (the connection
    ``password``, or one its ``config_schema`` marks) is read as its text
    whatever type it holds (:func:`secret_text`), even under a name the name
    list would never guess (``site_code``, ``pin``). A field only named like
    a credential (:func:`is_secret_key`) is collected when it holds text:
    the name is the outbound net, and a ``secret_port`` of 2000 must not mask
    every "2000". A nested mapping is read by the name rule alone.

    The registry takes the values of at least :data:`MIN_SECRET_LEN`
    characters, usernames aside (:func:`collect_identity_values`); a report,
    which masks every form of a value in one record, passes a lower
    ``floor`` and ``usernames=True``.
    """
    if not isinstance(config, dict):
        return set()
    declared = _declared_secret(config_schema) | {PASSWORD_FIELD}
    values: set[str] = set()
    for key, value in config.items():
        if isinstance(value, dict):
            values |= secret_values(value, floor=floor, usernames=usernames)
            continue
        name = str(key)
        if name in declared:
            text = secret_text(value)
        elif is_secret_key(name) and (usernames or name.lower() not in IDENTITY_KEYS):
            text = value if isinstance(value, str) else None
        else:
            continue
        if text is not None and len(text) >= max(floor, 1):
            values.add(text)
    return values


def collect_secret_values(
    config: dict[str, Any] | None,
    config_schema: dict[str, Any] | None = None,
) -> set[str]:
    """What the secret registry masks for one device's config:
    :func:`secret_values`, the usernames aside
    (:func:`collect_identity_values`)."""
    return secret_values(config, config_schema)


def collect_identity_values(
    config: dict[str, Any] | None,
    config_schema: dict[str, Any] | None = None,
) -> set[str]:
    """The usernames in one device's resolved config (:data:`IDENTITY_KEYS`,
    unless the driver marks the field secret)."""
    if not isinstance(config, dict):
        return set()
    declared_secret = _declared_secret(config_schema)
    values: set[str] = set()
    for key, value in config.items():
        if isinstance(value, dict):
            values |= collect_identity_values(value)
            continue
        if (
            isinstance(value, str)
            and len(value) >= MIN_SECRET_LEN
            and str(key).lower() in IDENTITY_KEYS
            and str(key) not in declared_secret
        ):
            values.add(value)
    return values


# The project's own secrets, outside any device, as (section, field): the key
# the systems in a mesh prove themselves to each other with.
PROJECT_SECRET_FIELDS: tuple[tuple[str, str], ...] = (("isc", "auth_key"),)


def project_secret_values(project: Any) -> set[str]:
    """The values of :data:`PROJECT_SECRET_FIELDS` in ``project`` (a loaded
    project, or a project file read as JSON) that are set."""
    values: set[str] = set()
    if project is None:
        return values
    for section_name, field in PROJECT_SECRET_FIELDS:
        if isinstance(project, dict):
            section = project.get(section_name)
        else:
            section = getattr(project, section_name, None)
        if isinstance(section, dict):
            value = section.get(field)
        else:
            value = getattr(section, field, None)
        if isinstance(value, str) and value:
            values.add(value)
    return values


# The sections of the system config that hold this system's own credentials:
# the Programmer's password and the API key (as typed when the environment
# supplies them, else as stored), and the cloud pairing's key.
SYSTEM_SECRET_SECTIONS: tuple[str, ...] = ("auth", "cloud")


def system_secret_values(system: dict[str, Any] | None, cloud_key: Any = "") -> set[str]:
    """This system's own secrets: a credential's value in
    :data:`SYSTEM_SECRET_SECTIONS` of the system config ``system`` (as
    ``SystemConfig.to_dict`` gives it), and the cloud pairing's key."""
    values: set[str] = set()
    for section in SYSTEM_SECRET_SECTIONS:
        values |= secret_values((system or {}).get(section))
    if isinstance(cloud_key, str) and cloud_key:
        values.add(cloud_key)
    return values


def register_system_secrets() -> None:
    """Hand the registry this system's own secrets
    (:func:`system_secret_values`), as they stand now: at the start, and
    whenever the cloud pairing changes. Never raises."""
    try:
        import openavc.config as runtime
        from openavc.system_config import get_system_config

        get_secret_registry().set_system_secrets(system_secret_values(
            get_system_config().to_dict(), getattr(runtime, "CLOUD_SYSTEM_KEY", ""),
        ))
    except Exception:  # never block a start or a pairing on the masking
        logging.getLogger(__name__).debug("Could not register this system's secrets", exc_info=True)


def register_config_secrets(
    device_id: str, config: dict[str, Any] | None, config_schema: dict[str, Any] | None,
) -> None:
    """THE door a device's config reaches the registry by: its secret values
    and its usernames (:func:`secret_values`), the saved-on-this-system
    marker left out, since it is no secret and masked would read as one."""
    from openavc.core.device_logins import SAVED_LOGIN

    held = {k: v for k, v in (config or {}).items() if v != SAVED_LOGIN}
    get_secret_registry().set_config_secrets(
        device_id,
        collect_secret_values(held, config_schema),
        identities=collect_identity_values(held, config_schema),
    )


def _percent_forms(secret: str) -> list[str]:
    """How a secret reads in a URL or a form body: percent-encoded (either
    case of hex), and with a space as ``+``."""
    out = []
    for encoded in (quote(secret, safe=""), quote_plus(secret, safe="")):
        lower = re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), encoded)
        out.extend(form for form in (encoded, lower) if form != secret and form not in out)
    return out


def _variants(secret: str) -> list[tuple[str, bool]]:
    """A secret's matchable forms: the literal, as a URL writes it, and its
    hex encoding.

    Returns ``(text, use_word_boundaries)`` pairs. Hex never gets boundaries —
    every hex character is a word character, so a boundary in the middle of a
    hex dump can never match.
    """
    forms: list[tuple[str, bool]] = [(secret, True)]
    forms.extend((form, True) for form in _percent_forms(secret))
    try:
        hexed = secret.encode("utf-8").hex()
    except (UnicodeEncodeError, AttributeError):
        return forms
    if hexed and hexed != secret:
        forms.append((hexed, False))
    return forms


def _byte_forms(secret: str) -> list[tuple[bytes, bool]]:
    """The byte forms of :func:`_variants`, in UTF-8, plus the secret in
    Latin-1 when a device would send that instead (one byte per character)."""
    forms = [(text.encode("utf-8"), bounded) for text, bounded in _variants(secret)]
    try:
        latin = secret.encode("latin-1")
    except UnicodeEncodeError:
        return forms
    if latin != secret.encode("utf-8"):
        forms.append((latin, True))
    return forms


def compile_secret_pattern(
    secrets: Iterable[str], *, min_length: int | None = None, bounded: bool = True,
) -> re.Pattern[str] | None:
    """Compile one alternation matching every form of every secret.

    One compiled pattern rather than a substitution per secret: the log filter
    runs on every record the server emits, and a per-secret pass over a fleet's
    worth of credentials would be paid on every line. Longest form first so a
    secret that is a substring of another is masked whole. ``min_length``
    lowers the floor for a caller that masks shorter secrets too (a device
    audit's report); ``bounded=False`` drops the word boundaries, for a caller
    that would rather mask a secret run into other characters (``CODE1234``)
    than keep a word that contains one whole.
    """
    floor = MIN_SECRET_LEN if min_length is None else min_length
    forms: list[tuple[str, bool]] = []
    for secret in secrets:
        if not isinstance(secret, str) or len(secret) < floor:
            continue
        forms.extend(_variants(secret))
    if not forms:
        return None

    alternatives = []
    for text, edges in sorted(forms, key=lambda f: len(f[0]), reverse=True):
        edges = edges and bounded
        left = r"\b" if edges and _WORD_EDGE.match(text[0]) else ""
        right = r"\b" if edges and _WORD_EDGE.match(text[-1]) else ""
        alternatives.append(left + re.escape(text) + right)
    return re.compile("|".join(alternatives))


def compile_secret_bytes_pattern(
    secrets: Iterable[str], *, min_length: int | None = None, bounded: bool = True,
) -> re.Pattern[bytes] | None:
    """The byte form of :func:`compile_secret_pattern`, for raw wire bytes.

    The device traffic recorder keeps bytes exactly as they crossed the wire
    and redacts only when an entry is written out, so the redaction has to
    work on the bytes themselves: replacing the text form would leave the hex
    view of the same entry carrying the secret, and replacing inside the hex
    would leave an odd-length string no reader can decode. Same forms and the
    same edge rule as the text pattern, matched against the UTF-8 encoding
    (and the Latin-1 one, for a device that sends that).
    """
    floor = MIN_SECRET_LEN if min_length is None else min_length
    forms: list[tuple[bytes, bool]] = []
    for secret in secrets:
        if not isinstance(secret, str) or len(secret) < floor:
            continue
        forms.extend(_byte_forms(secret))
    if not forms:
        return None
    def word(edge: bytes) -> bool:
        # A bytes pattern's \b knows ASCII word characters only.
        return edge.isalnum() or edge == b"_"

    alternatives = []
    for raw, edges in sorted(forms, key=lambda f: len(f[0]), reverse=True):
        edges = edges and bounded
        left = rb"\b" if edges and word(raw[:1]) else b""
        right = rb"\b" if edges and word(raw[-1:]) else b""
        alternatives.append(left + re.escape(raw) + right)
    return re.compile(b"|".join(alternatives))


def redact_bytes(data: bytes, secrets: Iterable[str]) -> bytes:
    """``data`` with every known secret value replaced by ``***``."""
    pattern = compile_secret_bytes_pattern(secrets)
    if pattern is None:
        return data
    return pattern.sub(REDACTED.encode("ascii"), data)


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

    Plus the secrets that are no one device's, each source replaced wholesale
    whenever it changes and masked everywhere a device's are, in every log
    line and in each device's own traffic: the project's
    (:meth:`set_project_secrets`: the mesh key, the plugin logins, and every
    secret the project holds in the clear) and this system's own
    (:meth:`set_system_secrets`: :func:`system_secret_values`).

    Compiled patterns are cached per device and for the union, and invalidated
    on every mutation.
    """

    def __init__(self) -> None:
        self._config: dict[str, set[str]] = {}
        self._runtime: dict[str, set[str]] = {}
        # Usernames: masked in the device's own traffic, not in every line.
        self._identity: dict[str, set[str]] = {}
        # The secrets no one device holds, by source ("project", "system").
        self._shared: dict[str, frozenset[str]] = {}
        self._device_patterns: dict[str, re.Pattern[str] | None] = {}
        self._all_pattern: re.Pattern[str] | None = None
        self._all_dirty = True

    def set_config_secrets(
        self, device_id: str, values: Iterable[str], identities: Iterable[str] = (),
    ) -> None:
        for store, given in ((self._config, values), (self._identity, identities)):
            kept = {v for v in given if isinstance(v, str) and len(v) >= MIN_SECRET_LEN}
            if kept:
                store[device_id] = kept
            else:
                store.pop(device_id, None)
        self._invalidate(device_id)

    def set_project_secrets(self, values: Iterable[str]) -> None:
        """Replace the project's secrets: its own (:func:`project_secret_values`),
        the plugin logins, and every secret it holds in the clear."""
        self._set_shared("project", values)

    def set_system_secrets(self, values: Iterable[str]) -> None:
        """Replace this system's own secrets (:func:`system_secret_values`)."""
        self._set_shared("system", values)

    def _set_shared(self, source: str, values: Iterable[str]) -> None:
        kept = frozenset(v for v in values if isinstance(v, str) and len(v) >= MIN_SECRET_LEN)
        if kept == self._shared.get(source, frozenset()):
            return
        if kept:
            self._shared[source] = kept
        else:
            self._shared.pop(source, None)
        # Every device's pattern carries them.
        self._device_patterns.clear()
        self._all_dirty = True

    def _shared_values(self) -> set[str]:
        return set().union(*self._shared.values())

    def add_runtime_secret(self, device_id: str, value: str) -> None:
        """Mask ``value`` in this device's traffic and every log line, from
        now until the device is forgotten. A value already known keeps the
        compiled patterns: every command carrying a secret parameter adds it
        again (``core/command_gates.register_secret_values``)."""
        if not isinstance(value, str) or len(value) < MIN_SECRET_LEN:
            return
        known = self._runtime.setdefault(device_id, set())
        if value in known:
            return
        known.add(value)
        self._invalidate(device_id)

    def forget(self, device_id: str) -> None:
        self._config.pop(device_id, None)
        self._runtime.pop(device_id, None)
        self._identity.pop(device_id, None)
        self._invalidate(device_id)

    def clear(self) -> None:
        self._config.clear()
        self._runtime.clear()
        self._identity.clear()
        self._shared.clear()
        self._device_patterns.clear()
        self._all_pattern = None
        self._all_dirty = True

    def secrets_for(self, device_id: str) -> set[str]:
        """What to mask in this device's own traffic: its secrets, its
        username and the project's secrets."""
        return (
            self._config.get(device_id, set())
            | self._runtime.get(device_id, set())
            | self._identity.get(device_id, set())
            | self._shared_values()
        )

    def all_secrets(self) -> set[str]:
        """What to mask in every log line: every device's secrets and the
        project's, not the usernames."""
        values: set[str] = self._shared_values()
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
        """Whether :meth:`redact_any` has anything to mask."""
        return bool(self._config or self._runtime or self._shared)

    def to_mask(self, where: Any, device_id: str | None = None) -> set[str]:
        """The values the exit ``where`` must not carry
        (:mod:`~openavc.utils.secret_audience`): none for an exit that may
        carry a secret; else one device's own (:meth:`secrets_for`) when
        ``device_id`` is given, and every device's (:meth:`all_secrets`)
        otherwise."""
        if may_carry_secret(where):
            return set()
        return self.secrets_for(device_id) if device_id else self.all_secrets()

    def masked(self, where: Any, text: str, device_id: str | None = None) -> str:
        """``text`` as the exit ``where`` may carry it: as it is when that
        exit may carry a secret; else with one device's own secrets masked
        (:meth:`redact_for`) when ``device_id`` is given, and every device's
        (:meth:`redact_any`) otherwise."""
        if may_carry_secret(where):
            return text
        return self.redact_for(device_id, text) if device_id else self.redact_any(text)

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
        redacted = registry.masked(Exit.LOG, message)
        if redacted != message:
            # Collapse to the redacted string; args are already interpolated in.
            record.msg = redacted
            record.args = ()
        return True
