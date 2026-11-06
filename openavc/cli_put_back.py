"""`openavc --put-back-logins`: the logins back into the project files.

For going back to an earlier version where the app has no Rollback. On Docker
that is the only way back: the image tag is changed by hand, and the earlier
version starts on the same data. It reads every login from the project file,
which since the login file holds the saved-login marker in their place, so it
would send the marker to the equipment as each password. Rollback in the app
(and the automatic one) avoids that by writing each saved login back first
(`core.device_logins.put_back_for_rollback`); this runs the same put-back by
hand, then exits without starting a server.

It refuses while OpenAVC answers on its port: the running server would move
the logins out again on its next save. On Docker::

    docker compose stop
    docker compose run --rm openavc --put-back-logins
    (change the image tag back, then)  docker compose up -d
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

from openavc.config import HTTP_PORT, PROJECT_PATH
from openavc.core.device_logins import SAVED_LOGIN, put_back_for_rollback
from openavc.system_config import get_data_dir


def _server_answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _markers_in(path: Path) -> int:
    try:
        return json.dumps(json.loads(path.read_text(encoding="utf-8"))).count(json.dumps(SAVED_LOGIN))
    except (OSError, ValueError):
        return 0


def main() -> int:
    if _server_answers(HTTP_PORT):
        print(
            f"OpenAVC is still running (port {HTTP_PORT} answers). Stop it first, "
            "then run this again. On Docker: docker compose stop"
        )
        return 1

    data_dir = get_data_dir()
    project = Path(PROJECT_PATH)
    files = [project] + sorted((data_dir / "saved_projects").glob("*/project.avc"))
    before = sum(_markers_in(f) for f in files)
    put_back_for_rollback(data_dir, project)
    left = sum(_markers_in(f) for f in files)

    if before == 0:
        print("No login here is saved on the system. Nothing to put back.")
        return 0
    if left:
        print(
            f"Put back {before - left} of {before} logins. {left} have nothing saved on this "
            "system and still say saved on this system: enter them again after going back."
        )
        return 1 if left == before else 0
    print(
        f"Put back {before} logins into the project files. Start the earlier version now; "
        "on Docker, change the image tag back, then docker compose up -d."
    )
    return 0
