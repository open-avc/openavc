"""THE rules for a value on its way out by an exit that may not carry a secret.

``utils/secret_audience`` says which exits may carry a secret at all, and
``utils/log_redaction`` holds every secret the system knows (one registry,
fed by one collector). These are the three shapes a value leaves in, each
asked by every exit of that shape, naming the exit:

  - **a state value** (:func:`state_value_as`): the cloud's relay, its alerts
    and the assistant's state tools, and the state's own log line. Driver
    state on this system may carry a secret (a camera's stream address holds
    its login so a panel can play it); every other exit gets a value a driver
    wrote under a credential's name as ``***`` (:func:`named_secret_state`)
    and its text as :func:`text_as` gives it.
  - **a text** (:func:`text_as`): the password of any
    ``scheme://user:password@host`` address and every registered secret
    masked.
  - **a config nothing declares the secrets of** (:func:`config_as`): a
    plugin's, an orphaned device's. Each field named like a credential is
    ``***`` at any depth and every other text is as :func:`text_as` gives it;
    ``log_redaction.keep_masked_secrets`` with ``shown`` set to the same
    exit's :func:`text_as` puts the saved text back when the config is sent
    back as it was read.
"""

from __future__ import annotations

from typing import Any, Callable

from openavc.utils.log_redaction import (
    IDENTITY_KEYS,
    REDACTED,
    get_secret_registry,
    is_secret_key,
    mask_secrets_by_name,
    mask_url_password,
)
from openavc.utils.secret_audience import may_carry_secret


def named_secret_state(key: Any, value: Any, *, usernames: bool = False) -> bool:
    """Whether ``value`` is text a driver wrote to state under a credential's
    name (``auth_token``, ``password``: ``is_secret_key`` on the key's last
    part). A username names an account, so it counts only for an exit about
    its one device (``usernames``, a report)."""
    if not isinstance(value, str) or not value:
        return False
    name = str(key).rsplit(".", 1)[-1]
    return is_secret_key(name) and (usernames or name.lower() not in IDENTITY_KEYS)


def state_value_as(where: Any, key: Any, value: Any) -> Any:
    """The state value under ``key`` as the exit ``where`` may carry it:
    as it is where it may carry a secret; else ``***`` under a credential's
    name, and its text as :func:`text_as` gives it. Anything but text is
    returned as it is: a flag, a number, nothing."""
    if may_carry_secret(where):
        return value
    if named_secret_state(key, value):
        return REDACTED
    return text_as(where, value)


def text_as(where: Any, text: Any) -> Any:
    """``text`` as the exit ``where`` may carry it: as it is where it may
    carry a secret; else with the password of any
    ``scheme://user:password@host`` address and every registered secret
    masked. Anything but text is returned as it is."""
    if not isinstance(text, str) or not text:
        return text
    if may_carry_secret(where):
        return text
    if "@" in text:
        text = mask_url_password(text)
    registry = get_secret_registry()
    return registry.masked(where, text) if registry.has_secrets() else text


def _text_leaves(value: Any, shown: Callable[[str], str]) -> Any:
    """``value`` with each text inside it, at any depth, as ``shown`` gives it."""
    if isinstance(value, dict):
        return {key: _text_leaves(item, shown) for key, item in value.items()}
    if isinstance(value, list):
        return [_text_leaves(item, shown) for item in value]
    return shown(value) if isinstance(value, str) and value != REDACTED else value


def config_as(where: Any, config: Any) -> Any:
    """A config nothing declares the secrets of as the exit ``where`` may
    carry it: each field named like a credential as ``***``
    (``log_redaction.mask_secrets_by_name``), every other text as
    :func:`text_as` gives it."""
    if may_carry_secret(where):
        return config
    return _text_leaves(mask_secrets_by_name(config), lambda text: text_as(where, text))
