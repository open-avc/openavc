"""Fetch and unpack guards for untrusted community artifacts.

The bottom layer of the plugin install stack: everything here answers "this
byte stream came from somewhere a plugin author chose — how do we handle it
without letting it hurt us." Size ceilings, zip-bomb rejection, an SSRF guard
for non-catalog download URLs, and a cumulative budget for directory-style
installs.

Kept separate from its callers because all three of them need it —
``plugin_installer`` (plugin zips and GitHub directory walks),
``plugin_wheels`` (wheel downloads), and ``plugin_native_deps`` (release
archives). Importing these back out of ``plugin_installer`` would be a cycle,
so they live at the bottom and the dependency arrows all point one way.

Provenance is a different question and lives in
``server/utils/community_integrity.py``: that module decides whether a source
is *trusted* (catalog pinning, artifact hashes), this one assumes the bytes
are hostile regardless and bounds what they can do.
"""

import asyncio
import ipaddress
import socket
import zipfile
from urllib.parse import urlparse

import httpx

# Download size guards (DoS defense). Generous ceilings sized for real native
# deps (a full ffmpeg build is ~100 MB compressed / ~250 MB extracted), not
# artificial limits — the point is to stop multi-GB downloads and zip bombs.
_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024        # any single fetched file (compressed)
_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024   # total extracted size of an archive
_MAX_ARCHIVE_MEMBERS = 20_000                  # entries in a zip / wheel
_MAX_DIRECTORY_FILES = 5_000                   # files in a directory-style install


async def _validate_download_url(url: str) -> None:
    """SSRF guard for an arbitrary (non-catalog) download URL.

    Native-dependency archives legitimately come from public release hosts
    (GitHub releases, project mirrors), so they can't be pinned to the catalog
    repo like plugin code. Instead require https and reject any URL whose host
    resolves into private, loopback, link-local (cloud-metadata), multicast,
    reserved, or unspecified address space — closing the SSRF vector (e.g. a
    plugin pointing the server at 169.254.169.254). Loopback is allowed only on
    a dev checkout so local tests / mirrors work.

    Mirrors routes/cloud.py:_validate_cloud_api_url; kept local because the
    policy differs (private ranges are blocked here) and to avoid a route->core
    import dependency.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Download URL must use https, got: {parsed.scheme or url!r}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"Download URL is missing a host: {url!r}")
    port = parsed.port or 443
    try:
        infos = await asyncio.get_event_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except (OSError, socket.gaierror) as e:
        raise ValueError(f"Could not resolve download host {host!r}: {e}")

    from openavc.api.auth import _deployment_is_dev
    allow_loopback = _deployment_is_dev()
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_loopback and allow_loopback:
            continue
        if (
            ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(
                f"Download URL resolves to a disallowed (non-public) address: {ip}"
            )


async def _download_capped(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int = _MAX_DOWNLOAD_BYTES,
    label: str = "file",
) -> bytes:
    """Stream a URL into memory, aborting if it exceeds ``max_bytes``.

    Streamed (not ``client.get``) so a multi-GB or unbounded chunked response
    can't exhaust RAM before we notice. Honors an upfront Content-Length when
    present (fast reject) and re-checks the running total as chunks arrive.
    """
    buf = bytearray()
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ValueError(
                f"{label} is too large: {int(declared)} bytes exceeds the "
                f"{max_bytes}-byte limit"
            )
        async for chunk in resp.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError(
                    f"{label} exceeded the {max_bytes}-byte download limit"
                )
    return bytes(buf)


def _check_zip_bomb(zf: zipfile.ZipFile, *, label: str = "Archive") -> None:
    """Reject an archive whose member count or total uncompressed size would
    make extraction a DoS (zip bomb), using the central-directory sizes."""
    infos = zf.infolist()
    if len(infos) > _MAX_ARCHIVE_MEMBERS:
        raise ValueError(f"{label} has too many entries (max {_MAX_ARCHIVE_MEMBERS}).")
    total = 0
    for info in infos:
        total += info.file_size
        if total > _MAX_UNCOMPRESSED_BYTES:
            raise ValueError(
                f"{label} is too large uncompressed "
                f"(max {_MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB)."
            )


class _DownloadBudget:
    """Cumulative file-count + byte caps for a directory-style install."""

    def __init__(
        self,
        max_files: int = _MAX_DIRECTORY_FILES,
        max_bytes: int = _MAX_UNCOMPRESSED_BYTES,
    ):
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.files = 0
        self.bytes = 0

    def add_file(self, size: int) -> None:
        self.files += 1
        self.bytes += size
        if self.files > self.max_files:
            raise ValueError(f"Plugin has too many files (max {self.max_files}).")
        if self.bytes > self.max_bytes:
            raise ValueError("Plugin directory total size exceeds the limit.")
