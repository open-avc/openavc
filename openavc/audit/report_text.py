"""How every report writes things down: a clock time, bytes, a traffic
entry, a name in a file name, and the rows of its HTML page.

Shared by the device audit's report and Report a problem's, so the two say
the same thing the same way. Bytes in a sentence are always ``hex aa 0b 01``
(``hex_pairs``), and every clock time is the local time of the computer that
wrote the report (``utc_offset`` says which).
"""

from __future__ import annotations

import html
import re
import time
from datetime import datetime
from typing import Any

from openavc.drivers.command_files import describe_size


def slug(value: Any, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return (text[:40].strip("-")) or fallback


def utc_offset(t: float | None = None) -> str:
    """This computer's time zone at ``t`` as an offset, ``UTC-04:00``: every
    clock time in the report is local to the computer that ran the audit."""
    stamp = datetime.fromtimestamp(t if t is not None else time.time()).astimezone()
    raw = stamp.strftime("%z") or "+0000"
    return f"UTC{raw[:3]}:{raw[3:]}"


def clock(t: float | None) -> str:
    if not t:
        return "--:--:--.---"
    dt = datetime.fromtimestamp(t)
    return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def hex_pairs(hex_text: str) -> str:
    """Bytes as every sentence in a report writes them, ``aa 0b 01``: two
    digits a byte, spaced (a ``hex`` field holds the same bytes unspaced)."""
    return " ".join(hex_text[i:i + 2] for i in range(0, len(hex_text), 2))


def quote_bytes(view: dict[str, str] | None, limit: int = 160) -> str:
    """A byte view as a reader sees it: text when printable, else hex pairs
    (the first ``limit / 2`` bytes)."""
    if not view:
        return "nothing"
    text = view.get("text", "")
    if not text:
        return "nothing"
    printable = all(c.isprintable() or c in "\r\n\t" for c in text)
    if not printable:
        raw = view.get("hex", "")
        return "hex " + hex_pairs(raw[:limit]) + ("..." if len(raw) > limit else "")
    shown = text.encode("unicode_escape").decode("ascii")
    if len(shown) > limit:
        shown = shown[:limit] + "..."
    return f'"{shown}"'


def traffic_text(entry: dict[str, Any], limit: int = 400) -> str:
    """One traffic entry as a timeline line reads it."""
    meta = entry.get("meta") or {}
    if entry.get("channel") == "file":
        return _file_entry_text(entry, meta)
    if isinstance(meta.get("withheld"), int):
        return f"{describe_size(meta['withheld'])}, not recorded while a file command ran"
    body = quote_bytes(entry, limit) if entry.get("text") else ""
    if entry.get("channel") in ("http", "http_listener"):
        head = _http_head(entry, meta)
        return f"{head}, body {body}" if body else head
    return _peer_prefix(meta) + (body or "nothing")


def _file_entry_text(entry: dict[str, Any], meta: dict[str, Any]) -> str:
    """A file a command took or handed back is never recorded: one entry
    names it, and what moved while it ran is kept as its size only."""
    size = meta.get("size")
    sized = f", {describe_size(size)}" if isinstance(size, int) else ""
    moved = "sent" if entry.get("direction") == "tx" else "received"
    return f"file {meta.get('file', '')}{sized} {moved} for {meta.get('command', 'a command')}"


def _http_head(entry: dict[str, Any], meta: dict[str, Any]) -> str:
    """An HTTP entry's request, or what answered it."""
    request = f"{meta.get('method', '')} {meta.get('target', '')}".strip()
    if entry.get("direction") == "tx":
        return request
    if meta.get("error"):
        return f"no response to {request}: {meta['error']}"
    if meta.get("status") is not None:
        return f"{meta.get('status')} {meta.get('reason', '')}".strip() + f" for {request}"
    return request or "request"


def _peer_prefix(meta: dict[str, Any]) -> str:
    if meta.get("topic"):
        return f"topic {meta['topic']}: "
    if meta.get("peer"):
        return f"{meta['peer']}: "
    return ""


REPORT_CSS = """
:root { --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --bg:#ffffff; --panel:#f6f8fa;
  --ok:#1a7f37; --warn:#9a6700; --bad:#cf222e; }
@media (prefers-color-scheme: dark) { :root { --fg:#e6edf3; --muted:#9198a1; --line:#3d444d;
  --bg:#0d1117; --panel:#151b23; --ok:#3fb950; --warn:#d29922; --bad:#f85149; } }
* { box-sizing: border-box; }
body { margin:0; padding:24px 16px; background:var(--bg); color:var(--fg);
  font:15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 920px; margin: 0 auto; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 28px 0 8px; border-bottom: 1px solid var(--line); padding-bottom: 4px; }
h3 { font-size: 15px; margin: 16px 0 4px; }
.meta { color: var(--muted); margin: 0 0 16px; }
.verdict { background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
  padding: 12px 16px; font-size: 17px; }
table { border-collapse: collapse; width: 100%; margin: 4px 0 8px; }
th, td { text-align: left; vertical-align: top; padding: 4px 8px; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; width: 28%; }
code, pre { font-family: ui-monospace, Consolas, "SFMono-Regular", monospace; font-size: 13px; }
pre { white-space: pre-wrap; word-break: break-all; background: var(--panel); padding: 8px;
  border-radius: 6px; margin: 4px 0; }
.matched { color: var(--ok); } .not_matched { color: var(--bad); } .not_observed { color: var(--muted); }
ul { margin: 4px 0; padding-left: 20px; }
footer { color: var(--muted); margin-top: 32px; font-size: 13px; }
"""


def escape_html(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def html_row(label: str, value_html: str) -> str:
    return f"<tr><th>{escape_html(label)}</th><td>{value_html}</td></tr>"
