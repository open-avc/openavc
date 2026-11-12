"""What a YAML driver sends, as the simulator's dispatcher receives it, for the validator.

The validator (``validate``) asks whether the auto-generated simulator
answers each line a driver puts on the wire, and whether the driver reads
what comes back. Both questions start from the same lines, built here: a
template filled with the config the runtime fills in (``effective_config``),
a command's parameters given sample values (``command_sample``), a query's
``{child_id}`` given a sample child, and the result trimmed the way the
simulator trims a line before matching it (``_as_dispatched``), so a check
compares what the simulator will actually see.

Three checks read a driver's queries, and each reads its own set, side by
side below: poll coverage (``polled_queries``), the query-answer list the
response check leans on (``asked_queries``) and the framing probes
(``probed_lines``). Each says which sources it reads and which it leaves
out, so for a text driver a new kind of query is a decision for each of the
three, made here; an OSC driver's checks each read their own source.

An OSC driver's addresses are read by ``osc_coverage``, which takes its
samples and config from here; its child-id sample is its own (the
``{child_id}`` text only), which a query line here does not share.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from openavc.drivers.compiled_protocol import derive_config, safe_substitute
from openavc.simulator.reread_queries import follow_up_queries, read_back_queries


_CHILD_ID = re.compile(r"\{child_id(?::([^{}]*))?\}")
_TOKEN = re.compile(r"\{([a-z_]+)(?::[^{}]*)?\}")


def effective_config(driver_def: dict) -> dict:
    """default_config plus config_derived values.

    Uses the same shared derivation the runtime runs, so a derived template
    whose referenced fields are all non-empty is substituted; otherwise the
    derived value is "" (so an optional prefix segment simply disappears).
    """
    config = dict(driver_def.get("default_config", {}))
    derive_config(config, driver_def.get("config_derived"))
    return config


def entry_text(entry: Any) -> Any:
    """A query entry's line: a mapping's ``send:``, else the entry as text."""
    return entry.get("send", "") if isinstance(entry, dict) else str(entry)


def _as_dispatched(text: str) -> str:
    """A line as the simulator's dispatcher matches it.

    Stripped, with the line ending gone, both the literal ``\\n`` / ``\\r``
    a YAML template carries and a real one. The final strip mirrors the
    dispatch, which strips each incoming line before matching: without it a
    template with trailing whitespace before its ``\\r`` false-fails handler
    patterns.
    """
    text = text.strip().replace("\\n", "").replace("\\r", "")
    return text.rstrip("\r\n").strip()


def query_line(query_text: str, config: dict, transport: str) -> str:
    """A query string as it reaches the simulator's dispatcher.

    Config substituted, an each_child template given a sample child id, the
    line terminator stripped (the simulator frames by lines), and an HTTP raw
    path expanded to the ``GET /path`` line the HTTP simulator dispatches —
    without that last one every raw-path poll looks unanswered.
    """
    sample = safe_substitute(query_text, config)
    sample = _CHILD_ID.sub(
        lambda m: format(1, m.group(1)) if m.group(1) else "1", sample,
    )
    sample = _as_dispatched(sample)
    if transport == "http" and sample.startswith("/"):
        sample = f"GET {sample}"
    return sample


def command_sample(template: str, params: dict, driver_def: dict) -> str | None:
    """A command's send (or OSC address) with sample values, or None when a
    config field it names has no value.

    The config goes in first, then each parameter's sample value through the
    shared substitution, which applies any ``{name:spec}`` format spec
    exactly the way the runtime send path does (numeric strings coerce
    before formatting).
    """
    filled = safe_substitute(template, effective_config(driver_def))
    test_values = {name: _test_value(param_def) for name, param_def in params.items()}
    sample = _as_dispatched(safe_substitute(filled, test_values))
    return None if _left_unfilled(template, sample) else sample


def _test_value(param_def: dict) -> str:
    """The sample value a parameter is sent with, by its type."""
    param_type = param_def.get("type", "string")
    if param_type in ("integer", "child_id"):
        return str(param_def.get("default", param_def.get("min", 1)))
    if param_type == "number":
        return str(param_def.get("default", param_def.get("min", 1.0)))
    if param_type == "boolean":
        # "1", not "true": a boolean is commonly written with a numeric
        # format spec ({flag:02d}) so a fixed-width protocol gets its 01/00
        # flag byte, and the word cannot be formatted that way — it would be
        # left as a literal placeholder and reported as a command no handler
        # matches.
        return "1"
    if param_type == "enum":
        return _enum_wire_value(param_def)
    return str(param_def.get("default", "test"))


def _enum_wire_value(param_def: dict) -> str:
    """An enum's first value as it goes on the wire.

    An entry is a plain wire value or a ``{value, label}`` pair; the label
    only names it in the picker. Stringifying the pair whole put a Python
    dict in the sample and reported the command as one no handler matches.
    """
    values = param_def.get("values") or ["test"]
    first = values[0]
    return str(first.get("value", "") if isinstance(first, dict) else first)


def _left_unfilled(template: str, sample: str) -> bool:
    """Whether a token of the template is still in braces (a config field
    with no default). The protocol's own braces around a substituted value
    (``{{name}}`` sends ``{test}``) are not one."""
    for token in _TOKEN.finditer(template):
        if re.search(r"\{" + token.group(1) + r"(?::[^{}]*)?\}", sample):
            return True
    return False


# ── The queries each check reads ──


def polled_queries(driver_def: dict, queries: list) -> Iterator[tuple[str, Any]]:
    """The queries poll coverage checks, each set with the name its finding
    gives an entry: the poll, the response rules' ``then_query`` and the
    commands' ``read_back``. Each set is read as it is reached, so a
    malformed one raises after the sets before it were checked. The
    start-up's entries are not among them.
    """
    yield "polling query", queries
    yield "then_query entry", follow_up_queries(driver_def)
    yield "read_back entry", read_back_queries(driver_def)


def asked_queries(driver_def: dict) -> list:
    """The queries whose answer a response rule must read: the poll, the
    response rules' ``then_query`` and an ``on_connect`` entry that declares
    ``query_for``.

    ``polling.queries`` is the set of strings a driver puts on the wire for
    no reason other than to learn what comes back, which is what makes an
    unreadable answer a defect; a response rule's ``then_query`` is too. An
    ``on_connect`` entry counts only when it declares ``query_for`` — the
    rest of that block is a bring-up sequence mixing reads with arming
    writes, and a write's acknowledgement is not something a response rule
    should match. The commands' ``read_back`` queries are not among them.
    """
    polling = driver_def.get("polling") or {}
    entries = list(polling.get("queries") or [])
    entries += follow_up_queries(driver_def)
    entries += [
        entry
        for entry in (driver_def.get("on_connect") or [])
        if isinstance(entry, dict) and entry.get("query_for")
    ]
    return entries


def probed_lines(commands: dict, queries: list, driver_def: dict) -> list[str]:
    """What the framing check sends a real simulator, in the driver's order:
    its liveness probe, its poll queries as written (a ``query_for`` entry
    and a command's name too) and a sample of each command. Neither
    ``then_query`` nor ``read_back`` is probed.

    Normalized the same way the poll checks do it, so an each_child template
    is probed with a sample child id rather than a literal {child_id} — which
    the simulator answers "does not exist", and an example nobody recognises
    is a finding an author reads past.
    """
    config = effective_config(driver_def)
    transport = driver_def.get("transport", "tcp")
    probes = _liveness_probe(driver_def.get("liveness"), config, transport)
    probes += _query_probes(queries, config, transport)
    probes += _command_probes(commands, driver_def)
    return probes


def _liveness_probe(liveness: Any, config: dict, transport: Any) -> list[str]:
    if isinstance(liveness, dict) and isinstance(liveness.get("send"), str):
        return [query_line(liveness["send"], config, transport)]
    return []


def _query_probes(queries: list, config: dict, transport: Any) -> list[str]:
    probes: list[str] = []
    for query in queries or []:
        query_text = entry_text(query)
        if isinstance(query_text, str) and query_text:
            probes.append(query_line(query_text, config, transport))
    return probes


def _command_probes(commands: dict, driver_def: dict) -> list[str]:
    """A sample of each command whose ``send`` is text."""
    probes: list[str] = []
    for cmd_def in commands.values():
        send_template = cmd_def.get("send") if isinstance(cmd_def, dict) else None
        if not isinstance(send_template, str) or not send_template:
            continue
        sample = command_sample(send_template, cmd_def.get("params", {}), driver_def)
        if sample is not None:
            probes.append(sample)
    return probes
