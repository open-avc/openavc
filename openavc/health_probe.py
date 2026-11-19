"""The container's health check: whether the server in this container is ready.

The Docker image's HEALTHCHECK runs ``python -m openavc.health_probe``. It asks
``/api/startup-status`` (the run-state record, middleware/startup_splash.py) on
loopback, at the port the server recorded once its sockets were bound
(status_folder.read_server_address), so a port changed in Settings or set with
OPENAVC_PORT is asked where the server listens. With HTTPS on it asks the
HTTPS port, since the HTTP port then carries a redirect at most. Before the
first start it asks the configured port.

Healthy (exit 0) only when the record says ready. Otherwise it exits 1 and
prints the phase, with a failed start's reason, which ``docker inspect`` shows
under the container's health.
"""

from __future__ import annotations

import json
import ssl
import sys
from urllib.request import Request, urlopen

from openavc import config, status_folder

# Inside the HEALTHCHECK's own --timeout, so a slow answer is reported here
# rather than cut off by Docker with nothing said.
TIMEOUT_SECONDS = 4.0
PATH = "/api/startup-status"


def status_url() -> tuple[str, ssl.SSLContext | None]:
    """The URL the probe asks, and the TLS context for it: the HTTPS port's
    certificate is the server's own, self-signed unless one was installed."""
    recorded = status_folder.read_server_address() or {
        "http_port": config.HTTP_PORT,
        "tls_enabled": config.TLS_ENABLED,
        "tls_port": config.TLS_PORT,
    }
    if recorded["tls_enabled"]:
        return f"https://127.0.0.1:{recorded['tls_port']}{PATH}", ssl._create_unverified_context()
    return f"http://127.0.0.1:{recorded['http_port']}{PATH}", None


def check() -> tuple[bool, str]:
    """Whether the server is ready, and what to say about it."""
    url, context = status_url()
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=TIMEOUT_SECONDS, context=context) as response:
            answer = json.loads(response.read().decode("utf-8"))
        phase, error = answer["phase"], answer.get("error")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return False, f"not answering at {url}: {exc}"
    if answer.get("ready") is True:
        return True, phase
    return False, f"{phase}: {error}" if error else str(phase)


def main() -> int:
    healthy, said = check()
    print(said)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
