"""THE rule for the names this host answers to.

Two questions get the same wrong answer when they are not separated: *what is
this machine called* and *what may somebody type into a browser*. The OS
hostname answers the first, and its shape depends entirely on the platform --
bare on an appliance image and on most Linux boxes (``openavc``), already
dotted on macOS (``Aarons-MacBook-Air.local``), and a full domain name on a
managed host (``box.corp.example.com``).

``mdns_advertiser._sanitize_hostname`` sanitizes a single DNS *label*: it
strips dots, because a label cannot contain one. Handing it a dotted hostname
produced a name nothing on earth can resolve (``Aarons-MacBook-Airlocal``),
and the certificate's SAN list was built on top of exactly that; the setup
screen made the mirror-image mistake and appended ``.local`` to a name that
already ended in it. So the suffix decision lives here, once, and the rule is
that a name already carrying a dot is already resolvable and is left alone.

Pure stdlib and no imports from the rest of the package, so the TLS path can
ask without pulling discovery in.
"""

from __future__ import annotations

import re

# A DNS label is letters, digits and hyphens. Spaces become hyphens because a
# machine named "Aaron's Pi 4" should still produce something typable.
_LABEL_BAD = re.compile(r"[^a-zA-Z0-9\-]")

# RFC 1035: one label is at most 63 bytes.
_LABEL_MAX_BYTES = 63


def sanitize_label(name: str, fallback: str = "openavc") -> str:
    """One DNS label, scrubbed to what a label may contain.

    Dots are removed rather than kept -- this is a *label*, so a caller
    holding a dotted hostname wants :func:`sanitize_hostname` instead.
    """
    sanitized = _LABEL_BAD.sub("", (name or "").replace(" ", "-")).strip("-")
    encoded = sanitized.encode("utf-8")[:_LABEL_MAX_BYTES]
    sanitized = encoded.decode("utf-8", errors="ignore").strip("-")
    return sanitized or fallback


def sanitize_hostname(name: str) -> str:
    """A whole hostname scrubbed label by label, with its dots kept.

    Empty when nothing survives.
    """
    trimmed = (name or "").strip().rstrip(".")
    labels = [sanitize_label(part, fallback="") for part in trimmed.split(".")]
    return ".".join(label for label in labels if label)


def _is_localhost(host: str) -> bool:
    """Whether this name only ever means "this process's own machine"."""
    return host.split(".")[0].lower() == "localhost"


def resolvable_hostname(name: str) -> str:
    """The name to print on a screen or put in a URL, or "" when there is none.

    ``.local`` is appended only to a bare name. A dotted one is already a
    resolvable name -- ``Aarons-MacBook-Air.local`` is the mDNS name itself,
    and ``box.corp.example.com`` is what the site's DNS answers -- so
    appending to either produces a name that resolves nowhere.
    """
    host = sanitize_hostname(name)
    if not host or _is_localhost(host):
        return ""
    return host if "." in host else f"{host}.local"


def cert_hostnames(name: str) -> list[str]:
    """Every name a browser may use for this host, for a certificate's SANs.

    ``localhost`` is the caller's to add: it is true of every host and does
    not come from the OS hostname.
    """
    host = sanitize_hostname(name)
    if not host or _is_localhost(host):
        return []

    names = [host]
    resolvable = resolvable_hostname(name)
    if resolvable and resolvable not in names:
        names.append(resolvable)

    # A managed box's domain name is what the site's DNS answers, but the
    # short mDNS form is what /setup prints and what avahi announces, so a
    # cert carrying only the FQDN still warns on the name people type.
    if "." in host and not host.lower().endswith(".local"):
        short = f"{host.split('.')[0]}.local"
        if short not in names:
            names.append(short)

    return names
