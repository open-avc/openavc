"""Read GitHub evidence through the authenticated GitHub CLI."""
import json
import subprocess
from urllib.parse import urlencode


class Unavailable(RuntimeError):
    """Evidence could not be retrieved or interpreted."""


class GitHub:
    def __init__(self, repository):
        self.root = f"repos/{repository}/"

    def get(self, endpoint, sarif=False):
        accept = "application/sarif+json" if sarif else "application/vnd.github+json"
        try:
            # GitHub answers in UTF-8; without the encoding, Windows reads the
            # reply in its console codepage and a byte it has no character for
            # loses the whole reply.
            result = subprocess.run(
                ["gh", "api", self.root + endpoint, "-H", f"Accept: {accept}"],
                capture_output=True, encoding="utf-8", timeout=60, check=True,
            )
            return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            # Do not echo authentication diagnostics or response bodies.
            raise Unavailable(f"GitHub evidence unavailable: {endpoint.split('?')[0]}") from exc

    def pages(self, endpoint, key=None, **query):
        page = 1
        while True:
            data = self.get(endpoint + "?" + urlencode(dict(query, per_page=100, page=page)))
            rows = data[key] if key else data
            if not isinstance(rows, list):
                raise Unavailable(f"Expected a list from {endpoint}")
            yield from rows
            if len(rows) < 100:
                return
            page += 1
